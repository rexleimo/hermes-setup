# 底层操作是否应该用 Rust？——选型评估

> 问题：做一个操作 Hermes 底层（进程控制、配置写入）的运维平台，用 Rust 会不会更好？
> 结论先行：**v1 的 Web/CRUD 层保持 Python 是正确选择；对底层操作的「特权部分」，
> Rust 有真实收益，但收益来源是特权隔离与部署形态，而不是语言本身的速度。
> 本平台已把底层操作收敛到 `app/hermes/` 单一接缝，二期可无创伤地引入 Rust 守护进程。**

## 1. 这个平台的工作负载画像

拆开看平台的「底层操作」，其实是四类：

| 操作 | 频率 | 瓶颈 | Rust 能带来什么 |
|---|---|---|---|
| 写 config.yaml / .env（读-改-原子替换） | 低频（人工配置） | 正确性：注释保留、并发锁、崩溃一致性 | 内存安全有帮助，但 Python + `fcntl` + `os.replace` + fsync 已经达成同样的正确性保证 |
| `hermes gateway start/stop/status` | 低频 | 与官方 CLI 的进程语义对齐 | 无收益——本质是 spawn 子进程并解析文本 |
| 拉取模型列表（HTTP） | 低频 | 等待网络 | 无收益（I/O bound） |
| 表单 CRUD / 页面渲染 | 高频 | 开发迭代速度 | 负收益（Rust 的 CRUD/模板生态与迭代速度明显弱于 Python） |

**没有任何一处是 CPU bound 或高并发**。运维平台一天写不了几次配置文件。
所以「Rust 更快」在这个场景是伪命题；真正要回答的是安全与工程问题。

## 2. Rust 的真实收益点（以及何时值得）

### 收益点 A：特权分离的守护进程（最有价值）
当前形态：控制台与 Hermes 同用户运行——控制台被攻破即等同 Hermes 被攻破。
更硬的形态是拆成两个进程：

```
┌──────────────────────┐    Unix Domain Socket     ┌──────────────────────┐
│ Web 控制台（无人特权） │ ────── 窄协议、白名单 ────→ │ hermes-opsd（root 或  │
│ FastAPI + HTMX       │      命令 + 参数校验        │ hermes 专用用户 +     │
└──────────────────────┘                           │ CAP 有限能力）        │
                                                   └──────────────────────┘
```

特权守护进程只暴露极小的命令集（start/stop/status/write-config-section），
对参数做严格校验，拒绝一切 shell 拼接。**这正是 Rust 的甜点**：
- 单个静态链接二进制，部署到目标服务器零运行时依赖（不用装 Python）；
- 内存安全 + 无 GC，长驻进程不担心漏洞类别中最常见的一类；
- 生态成熟：axum + UDS、serde 校验、`nix` crate 做信号/进程控制。

### 收益点 B：作为「随 Hermes 分发」的伴生组件
如果未来这个 ops 能力要反哺 Hermes 上游（用户 `curl install.sh` 时顺带装上），
一个 Rust 编译的 `hermes-opsd` + 官方安装脚本分发，比要求目标机预装 Python 3.11 + uv 更顺滑。

### 收益点 C：大文件/高频轮询场景（目前不存在）
如果将来做「每秒级指标采集 + 实时终端流」这类常驻 I/O 密集服务，Rust 的可预测延迟有意义。

## 3. Rust 的成本（为什么 v1 不用它）

- **CRUD 迭代速度**：管理后台的需求必然高频变化（本期就改了十几轮模板与表单）。
  Jinja2 + HTMX 的改一行看一秒，在 Rust（askama 重编译 / leptos 等较重栈）下是分钟级反馈；
- **生态重合度**：Argon2/TOTP/QR/YAML round-trip/表单描述符驱动渲染，Python 侧全是成熟件；
- **团队与协作**：管理后台是典型「多人多次小改」的代码，Rust 的学习曲线与借用检查
  在这类代码上是纯摩擦；
- **诚实的结论**：这个项目的风险不在内存安全，而在**认证链与特权边界**——
  这两者用任何语言写都要靠设计（Argon2/CSRF/审计/最小暴露面），语言不提供豁免。

## 4. 推荐路线

| 阶段 | 形态 | 理由 |
|---|---|---|
| **v1（当前）** | 纯 Python；底层操作收敛在 `app/hermes/`（supervisor / config_store / installer） | 交付速度 + 配置 schema 仍在跟随上游演进，Python 改起来快 |
| **v1.5（同机加固）** | 保持 Python Web；把「写配置 + 进程控制」抽成 `hermes-opsd`（Rust，UDS + 白名单命令），Web 进程降权运行 | 特权分离落地；`app/hermes/` 接缝使得 Web 层零改动 |
| **v2（分发形态）** | `hermes-opsd` 随 Hermes 官方安装脚本分发；控制台可跨机管理多台 Hermes | Rust 静态二进制的部署优势兑现 |

## 5. 接缝已就绪

`app/hermes/` 包是平台对底层操作的唯一出口：

```
supervisor.py   status/start/stop/restart/version → 换成 UDS RPC 调用 opsd
config_store.py load/save/env → 换成 opsd 的 write-config 事务接口
installer.py    submit/job_log → 换成 opsd 的 job 流
```

三个模块的公开函数签名就是未来 RPC 的接口草案（tests/test_services.py 与
tests/test_supervisor.py 已把这些行为固化成 44 个用例中的子集），
替换实现时 Web 层与模板层一行不改。
