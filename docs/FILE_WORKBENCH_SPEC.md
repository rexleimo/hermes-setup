# 文件工作台（File Workbench / 「WebOS」）需求与设计规格

> 状态：草案 v1（未开工）。覆盖 W3（文件服务）、W4（注入↔工作台联动）、W5（工作台外壳）。
> 决策依据：工程注入层（engineering_service）已定义文件落位契约，工作台是它的展示与执法面。

## 1. 需求（用户故事）

- R1：用户不陪跑浏览器操作时，Agent 产出的文件有**固定去处**，事后能在 Console 一处查看全部产物。
- R2：能浏览/预览/下载 Agent 产物（markdown、图片、代码、pdf），并看到「哪个任务产出了哪些文件」。
- R3：文件落位纪律可被**验证**而非信仰：散乱文件、超期 scratch 能在 UI 暴露并可一键整理。
- R4：平台自身不乱放：所有新增运行数据仍收敛在既有目录约定内。

### 非目标
- 不做真 OS 范式：进程隔离、多用户桌面、图标拖拽壁纸（同机同用户，权限层面无意义）。
- 不做在线 IDE / 协同编辑；上传仅限投递素材到 downloads/。
- 不做文件系统级强制拦截 Agent 写盘（与 Hermes 抢地盘，成本>收益）；执法靠巡检可视化。

## 2. 单一契约：workspace 根目录

- 唯一数据源：`EngSettings.workspace`（DB 键 `engineering_settings`，默认 `~/hermes-workspace`）。
  文件服务**必须**读同一配置，禁止另设路径项，杜绝配置漂移。
- 目录语义（注入层已立法，工作台直接映射）：
  | 目录 | 语义 | 工作台呈现 |
  |---|---|---|
  | `projects/<name>/` | 一切产出归属 | 主视图，卡片按项目分组 |
  | `downloads/` | 外部材料 | 独立入口，可上传 |
  | `scratch/` | 一次性实验 | 灰显 + 超期告警 |
  | `archive/<name>-<YYYYMMDD>/` | 完结项目 | 折叠归档区 |

### 2.1 安全硬约束（实现验收标准）
- S1 jail：所有路径 `resolve()` 后必须位于 workspace 根内，`..`/symlink 逃逸一律 4xx + 审计 `denied`。
- S2 根目录防劫持：保存 `EngSettings.workspace` 时（仅 admin + 审计）拒绝深度 < 2 的路径
  （`/`、`~`、`/home/user` 直接拒绝），拒绝符号链接指向 home 之外。
- S3 写操作只有两类：上传到 `downloads/`、整理动作 mv 到 `archive/`；永不覆盖同名（自动后缀）。
- S4 全部动作打 audit_log（list 首次进入某项目也记录，防侦察不可见）。
- S5 复用现有守卫：读=require_login，写=require_admin + csrf_guard。

## 3. 架构落位（符合 web→core/hermes 收敛点）

```
app/web/routers/files.py            # W3 薄控制器
app/hermes/workspace_service.py     # W3 领域层：jail 解析、list/stat/zip/preview 分类、mv 整理
app/hermes/workspace_inspect.py     # W4 归位巡检：散文件/超期 scratch/缺失 delivery.json
app/web/templates/files/*.html      # HTMX 局部交换，零构建链不变
```
- 依赖方向不变：`files(router) → workspace_service → paths/config_store`；web 层不碰文件系统。
- 零新增依赖：路径与打包用 stdlib（zipfile），预览分类靠扩展名白名单。

## 4. API 草案（W3）

| 路由 | 方法 | 说明 |
|---|---|---|
| `/files` | GET | 文件管理器页（面包屑 + 列表 + 预览面板） |
| `/files/api/list?dir=` | GET | JSON：条目 name/kind/size/mtime；HTMX 局部片段可选 |
| `/files/api/file?path=` | GET | 预览：md 渲染 / 图片 / 代码（<512KB 截断）/ pdf inline |
| `/files/api/download?path=` | GET | 单文件流式下载 |
| `/files/api/archive?dir=` | GET | 目录 zip 打包 |
| `/files/api/upload` | POST | → downloads/（大小上限走 app_settings，默认 50MB） |
| `/files/api/move` | POST | 拖入归档/整理（admin），mv 语义不留副本 |

## 5. W4 注入↔工作台联动

- **delivery.json 约定**：托管块（SOUL.md）追加一条：完结时在项目根写
  `delivery.json = {task, finished_at, files:[{path, what, verify}]}`。
  实现 = 修改 `DEFAULT_RULES` 与 `project-init` 技能文本，走既有 apply/remove 机制，向后兼容
  （无此文件 = 卡片降级为目录浏览，不报错）。
- **任务↔产物关联**：`job_runs`（install/update/gateway_*）已存 DB；任务卡片流 =
  job_runs + audit_log 时间线，点卡片跳转 `/files?path=projects/<name>`。
  需要给 job_runs 增加可空列 `project`（写任务时若 workspace 有活跃项目则填）。
- **归位巡检**（执法面）：`/files/inspect` 扫描：根部散文件、`scratch/` mtime > 14 天、
  projects/ 下缺 docs/requirements.md 的项目 → 黄条计数；每项提供 mv→archive/ 建议操作（人确认后执行）。

## 6. W5 工作台外壳

- 布局：左树（项目/目录）+ 中列表 + 右预览；顶部任务卡片抽屉。
- 窗口感：HTMX 局部交换 + 原生 `<dialog>` + 可开合面板；**不引前端框架**（守住零构建链决策）。
- 入口：导航新增「工作台」，dashboard 增加 workspace 健康摘要卡（巡检计数）。
- 触发条件：W3+W4 上线并被真实使用后，才决定是否值得做窗口拖拽等重交互。

## 7. 里程碑与验收

- M1（W3）：S1-S5 自动化测试通过；Windows/Linux 双平台跑通（依赖 W1）。验收：匿名 402→302、
  jail 逃逸测试、上传→下载往返、zip 打包。
- M2（W4）：注入新版本 apply 后 SOUL 块含 delivery 约定；巡检对人工布置的乱目录全命中；
  卡片→文件跳转可用。
- M3（W5）：三栏工作台在日常浏览器宽度可用；无构建链引入；页面 <script> 全部走现有 app.js。

## 8. 风险
- Agent 不守约写飞 → 巡检兜底可见，不阻塞。
- workspace 在 NAS/慢盘 → list 加 TTL 缓存（30s），zip 流式不落地。
- 大量文件（>10k）→ 分页 + 目录级汇总，M1 内实现分页即可。

---

## 修订 R1（v0.5.0）— OS 风格重构（用户验收反馈）

用户反馈：M1 的三栏表格"不是很能接受"，要求做成真正的 OS 文件管理器（Windows Explorer / macOS Finder 均可），且要有自己的文件类型体系（图片、视频等）。

落地：
1. **类型体系**：`CATEGORIES = images/videos/audio/documents/code/archives`（按扩展名）；
   物理目录新增 `documents/ pictures/ videos/` 标准桶（init 幂等补齐）。
2. **智能集合**：侧栏虚拟视图（全工作区聚合、按时间倒序、上限 1000），只读定位，
   **不移动不复制文件**（维持"永不产生第二份"执法原则）。
3. **OS 交互**：网格（图片/视频真缩略图，浏览器原生渲染，零依赖）⇄ 详细列表；
   单击选中 + 操作条、双击进入/预览、右键菜单、Backspace 上一级、筛选、排序；
   hx-boost 整页导航，URL 即状态，前进/后退原生可用。
4. 预览面板 = Finder Quick Look 角色，新增 video/audio 内联播放。

约束遵守：零新依赖（缩略图/播放用浏览器原生能力）、零构建链（交互为 app.js 委托事件）、
jail/审计/上传落 downloads/归档不复制等执法规则全部未变。

---

## 修订 R2（v0.8.27）— WebOS 窗口层（双击开窗在线查看）

用户反馈：R1 的预览面板「不够像操作系统」，要求双击文件像 Windows/Finder
一样**开窗**，支持最小化/还原/全屏/多窗口，文件在浏览器内直接打开（底座
是 Linux/WebOS，`.exe` 无意义，不做本地执行委托）。

落地（三层解耦，扩展点单向）：

1. **后端描述符 API**（WI-9）：`GET /files/viewer?path=...` 返回
   `{ok, kind, rel, name, size, text?, truncated?}`——走 `file_for_download`
   同一 jail，登录必需，记 `files_open` 审计；文本内容 512KB 截断。
   端点刻意不带 `src`（前端按 `rel` 拼 `/files/raw`），加类型时端点不变。
2. **通用窗口管理器**（WI-10）：`static/js/window-manager.js`（原生 JS
   IIFE，零构建链不变）——`WM.open(desc)` 开 OS 风格窗口（标题栏：图标 +
   名称 + 最小化/全屏/关闭；拖拽、级联定位、z-order 焦点）；任务条 chip
   固定底部；`#wm-root` 在 `htmx:afterSwap` 后重挂，窗口跨 hx-boost 页面
   导航存活（OS 语义）。
3. **Viewer 注册表**（WI-11）：内置 image（适配/原始大小）、video（原生
   控件、还原续播）、audio、pdf（iframe）、text/code（pre）、none（不支持
   在线打开 + 下载）。**新增文件类型 = 后端 `preview_class` 一个分支 +
   `WM.register(kind, factory)` 一个条目。**

窗口记忆语义：最小化 = 销毁内容 DOM（释放解码内存）+ 保留描述符指针，
还原 = 按描述符重建（媒体回保存位置）；关闭 = DOM 全销毁。无最大化按钮
（最小化/全屏/关闭三键）；多窗口并存。

约束遵守：零新依赖、零构建链（CSP `script-src 'self'`，全外部 JS）、
web 层不碰文件系统（仍走 workspace_service）、无 DB 迁移。

前端回归测试：项目无前端测试框架（零构建链决策），故用零依赖 Node
mini-DOM shim 直跑 `tests/js/wm-test.js`（47 断言：生命周期 / 任务条
chip 归属 / 媒体续播 / 注册表 / 扁平描述符 / XSS 转义 / 工厂异常降级），
`tests/test_window_manager_js.py` 做 pytest 包装（无 Node 时跳过）。
另经浏览器 E2E 人工回归（文本/图片/视频/最小化/还原/全屏/多窗口/
关闭全场景，2026-09-20 实机通过）。

缓存语义：`/files/raw` 与 `/files/zip` 带 `Cache-Control: no-cache`
（工作区文件可变，强制浏览器按 ETag 再验证，防止 viewer/缩略图拿到
更新前的旧内容）。


---

## 修订 R3（v0.8.34）— 每请求开销上界（线上「文件一多就进不去」根治）

症状：工作区文件上千后，`/files?cat=images&view=grid` 让整台服务不可用。逐层量
下来（合成工作区 1500 图 + 6000 个 `node_modules` 文件，单进程）发现：没有任何
单独一项是“慢查询”，**慢的是每请求固定开销全部没有上界**，乘在一起就塌了。

| 开销项 | R2 之前 | R3 的界 |
| --- | --- | --- |
| 一次渲染遍历全树 | 4 遍（计数/位置/集合/巡检各一遍，且 `rglob` 先钻依赖目录再丢弃） | 1 遍：`_walk_files()` 单遍 scandir，聚合视图全部从 `_index()` 派生 |
| 缓存到期时的并发重算 | N 个请求 = N 次全树重算（stampede） | single-flight：同键并发只算 1 次，其余等结果 |
| 索引规模 | 无界 | `INDEX_LIMIT=50000` 硬上界；`WALK_PRUNE`（node_modules/`__pycache__`/.git/.venv/venv/.tox）+ 隐藏目录 + 软链不下钻 |
| 一页条目数 | 全部（`COLLECTION_LIMIT=1000`） | `PAGE_SIZE=120` + `/files/more` 按 offset 增量 |
| 首屏媒体请求 | 1000 个 `<img src>`（浏览器并发随意，每个首次都要服务端解码原图） | 一律 `data-src`，IntersectionObserver 进视口才挂，全页并发上限 4 |
| 每请求 DB 写事务 | 2（session 滑动两条 UPDATE）+ 1 设置读 | 滑动限流 60s 一次且合并成 1 条；`get_setting` 进程内 5s TTL（写即失效） |
| SSE `/files/watch` | 同步生成器，一条连接独占一个 worker 线程 30 分钟 | async 端点 + 每事件循环限流 `workbench_sse_max`（默认 6），满位推 `busy` 由前端 60s 退避；扫描走 `anyio.to_thread` |
| 同步 worker 额度 | anyio 默认 40 | `worker_threads`（默认 64，`HERMES_CONSOLE_WORKER_THREADS`）在 lifespan 显式抬升 |

实测（同一台机器、同一合成工作区）：冷启动首屏 729ms → 30ms；页面每请求 SQL
18 条（含 3 次写）→ 3 条（1 次写：审计）；缩略图每请求 5.0 条 SQL（2.0 写）→
2.0 条（0 写）；开一页引发的缩略图请求 1000 → ≤120 且并发 4。

口径交换（明确接受的代价）：

1. 绕过服务层直接写盘的文件，最多 `_COUNTS_TTL`（30s）后才出现在列表/集合/搜索；
   服务层任何写操作（上传/改名/删除/移动/复制/新建）立即 `invalidate_caches()`。
2. 盯盘签名变化时也会作废快照 —— 否则「推送了 changed 但页面还是旧 30s」，
   两个机制各说各话。
3. `location_counts` 口径随遍历边界收敛：依赖目录与隐藏目录不再计入位置总数。
4. 排序加名字次级键：同一时间戳的条目不再随进盘顺序翻转。

护栏：`tests/test_workbench_perf.py`（19 项）钉住“有上界”这件事本身 —— 走盘次数、
合流、分页窗口、offset 保留、写事务节流、遍历边界、缩略图闸门、SSE 形态。这些
用例失败意味着有人把某一头上界又拆掉了，而不是样式变了。
