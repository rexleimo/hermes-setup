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
