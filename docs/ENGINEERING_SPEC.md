# 工程规范层（Engineering Guardrails）设计文档

> 目标：解决「用户随手一句指令，Hermes 产出东一块西一块」的问题——文件散落、没有项目概念、
> 需求未消化就动手。参考 AIOS（AI Agent Operating System）的操作系统式管理思想，
> 给 Hermes 加一层可配置、幂等注入、可一键卸载的工程纪律。

## 1. 设计原则

| 原则 | 落地方式 |
|---|---|
| **只用官方机制** | 三个注入通道全部是 Hermes 官方支持的：`SOUL.md`、`skills/`、`agent.coding_instructions`。不做 monkey-patch，`hermes update` 不受影响 |
| **不破坏用户内容** | SOUL.md 中平台内容放在 `<!-- hermes-console:engineering begin/end -->` 标记之间；标记之外的用户自有内容任何操作都不触碰 |
| **幂等** | 重复「应用」不会产生重复块；无变更时不落盘 |
| **可逆** | 「移除」按标记剥离托管块、按 `managed-by` 标记删除自家技能，全程留备份（保留 10 份） |
| **渐进加载** | 长流程放技能（按需 `skill_view` 加载），系统提示只保留精炼铁律，控制 token 预算 |
| **可配置** | 工作区路径、铁律清单、技能组合、是否注入 coding_instructions 全部在控制台表单里改 |

## 2. 三层注入通道

```
┌─────────────────────────────────────────────────────┐
│ L1  SOUL.md 托管块（每次会话常驻，~600 字符）          │
│     工作区布局图 + 六条铁律 + 需求消化流程摘要          │
├─────────────────────────────────────────────────────┤
│ L2  skills/（按需加载，不占常驻预算）                  │
│     project-init / requirement-digest / file-placement│
├─────────────────────────────────────────────────────┤
│ L3  agent.coding_instructions（可选，编码任务硬约束）   │
│     铁律子集，官方「coding posture」系统块追加          │
└─────────────────────────────────────────────────────┘
```

## 3. 工作区布局（文件管理规范）

```
<工作区根>/                      # 默认 ~/hermes-workspace，可配置
├── projects/<项目名>/           # 所有产出必须归属某个项目
│   ├── docs/requirements.md    # 需求消化（见 §4）
│   ├── docs/plan.md            # 任务拆解与执行顺序
│   ├── src/                    # 代码与产出
│   ├── data/                   # 项目数据与素材
│   └── notes/                  # 过程记录
├── downloads/                  # 下载与外部材料
├── scratch/                    # 一次性实验，随时可整目录清理
└── archive/                    # 完结项目（<名>-<YYYYMMDD>）
```

**六条默认铁律**（可在 UI 逐条改）：
1. 任何非平凡任务先确认/创建项目目录，禁止直接在 HOME 或工作区根部落地文件；
2. 下载与外部材料一律放入 `downloads/`，产出物一律放入所属项目的子目录；
3. 动手实现前先写 `docs/requirements.md` 与 `docs/plan.md`，执行偏离时回写计划；
4. 命名使用小写中划线，不用 `tmp` / `新建文件夹` 这类名字；
5. 任务结束输出交付清单：产出了什么、在哪个路径、如何验证；
6. 完结项目移入 `archive/` 并追加日期后缀。

## 4. 需求消化流程（requirement-digest）

```
用户指令 ──→ 识别非平凡任务？
              ├─ 否 → 直接执行（但仍守文件归位纪律）
              └─ 是 → ① 建/认领项目目录
                      ② 填 docs/requirements.md：
                         目标 / 范围与非目标 / 约束 / 可验证的验收标准 / 任务拆解
                      ③ 信息不足 → 列「待确认问题」一次问全
                      ④ 与用户确认 → 按 docs/plan.md 执行
                      ⑤ 偏离计划 → 回写计划再继续
                      ⑥ 交付清单（产出物 + 路径 + 验证方式）
```

## 5. 三个内置技能

| 技能 | 触发 | 内容 |
|---|---|---|
| `project-init` | 新任务且无对应项目目录 | 目录脚手架、命名规范、开工前向用户复述目标与验收标准 |
| `requirement-digest` | 需求含糊或多步骤任务 | requirements.md 模板填写流程、待确认问题清单、偏离回写 |
| `file-placement` | 任何文件创建/下载/移动/清理 | 归位对照表、禁止区域（Desktop/HOME 根）、归档规则 |

技能采用官方 SKILL.md 格式（agentskills.io 兼容 frontmatter，含
`metadata.hermes.managed-by: hermes-console` 标记），卸载时只删除带标记的目录。

## 6. 平台侧交互

「工程规范」页（`/engineering`）：
- **规范定义**：工作区路径、铁律（逐行编辑）、技能勾选、coding_instructions 开关 → 应用；
- **注入状态卡**：SOUL.md 托管状态与用户自有内容字节数、各技能安装状态、工作区存在性；
- **工作区初始化**：物理创建目录树 + README + 示例项目的两份文档模板（仅创建缺失项）；
- **SOUL 托管块预览 / 需求模板预览**；
- **卸载**：剥离托管块、删 coding_instructions，可选保留技能。

所有动作写入审计日志（`engineering_apply` / `engineering_workspace_init` / `engineering_remove`）。
规范文本本身存于平台 DB（`engineering_settings`），应用结果落在 Hermes 侧，两边职责清晰。

## 7. 已知边界

- 规范对 Agent 是「强提示」而非硬约束——LLM 偶尔仍会偏离；SOUL.md 常驻块 + 技能渐进加载
  已是 Hermes 官方机制内最强的持久约束形态；
- `agent.coding_instructions` 官方语义是「代码工作区的编码姿态」，非编码任务的约束由
  SOUL.md 块承担；
- 跨机部署时工作区路径应指向 Hermes 所在机器的本地路径。
