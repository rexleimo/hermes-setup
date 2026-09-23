# Changelog

## 0.8.39 — 2026-09-23（点侧栏「位置/智能集合」后整页布局错乱）

### 症状
点左侧「主文件夹 / 项目 / 草稿 / 回收站 / 智能集合…」任意一项，整页卡片直接糊到侧栏、
顶栏和详情面板上。反过来**直接刷新到同一个 URL 是正常的** —— 说明服务端渲染没问题，
是 boost 换页之后客户端状态被破坏了。

### 根因一：htmx 换页后的属性回填抹掉窗口化状态（`app.js`）
htmx 换 body 时会对「同 id + 同标签」的元素做属性保留：交换时把旧元素的 class/style
抄给新元素，并把回填任务排进 settle —— 而 settle 永远排在 `htmx:afterSwap` **之后**。
`wbVirtInit()` 正是在 afterSwap 里跑的，于是它刚给 `#osfm-items` 加上 `.wb-virt` 和内联
`height`，就被响应自带的 `class="e-grid"` + 空 style 覆盖回去。

`.wb-virt` 是 `#osfm-items` 唯一的 `position:relative` 来源（`admin.css` 里
`#osfm-items.wb-virt{display:block;position:relative}`）。它一掉，JS 造出来的
`.wb-pblock`（`position:absolute`）就改为相对初始包含块定位，几百张卡片按整页坐标平铺，
压住侧栏/顶栏/详情面板 = 用户看到的「整页乱」。

修复：`htmx.config.attributesToSettle = []`。项目里只有 `.htmx-swapping`、没有任何
`.htmx-settling` 过渡样式，关掉回填没有视觉代价。

### 根因二：块高在容器定稿之前测量（`app.js`）
`W.blockH = W.blocks[0].offsetHeight` 发生在 `shell.classList.add("wb-virt")` 之前，
此时外壳还是 `.e-grid`（static + grid），块按「没有相对父级」的宽度被测出高度，
和之后的真实块高不一致（实测 3926 vs 15709）→ 块与块互相重叠。同一行代码顺序还让
列表的 `W.offsetTop`（表头高）在块 0 建好之后才赋值，块 0 的 `top:0` 压在粘滞表头下面。
修复：先落 `.wb-virt`、再量表头高、再建块 0。

### 根因三：列表分页片段按非表格上下文解析（`app.js`）
`document.createRange().createContextualFragment(html)` 走 body 插入模式，`<tr>`/`<td>`
的开始标记会被整个丢掉、只剩里面的内容 → 列表视图翻到第 2 页时，块里的 `tbody` 塞的是
120 个非 `tr` 节点（真机实测 `tr.osfm-card-item = 0`）。修复：按视图选解析宿主，
列表用 `<tbody>`（新增 `wbVirtNodes()`）。jsdom 与 Chrome 在这里行为一致，已钉成回归项。

### 根因四：首屏页被当成缺块反复重取（`app.js`）
块 0 没进 `W.loaded`，于是每次 paint 都重发 `/files/more?...&offset=0`（服务端返回的正是
首屏那 120 条）。新块覆盖 `W.blocks[0]` 后，旧节点没人引用、也就没人摘除 → 首屏两套卡片
重叠（真机实测：块数 3、`p0@0px` 出现两次、120 个重复 `data-rel`）。修复：建好块 0 即
`W.loaded.add(0)`。

### 根因五：窗口按「其实不可滚的滚动容器」算 → 窗口化形同不存在（`app.js`）
`#osfm-content` 写着 `overflow:auto`，但本页从 body 到它整条 flex/grid 链只有 `min-height`，
外壳的内联高度会把它撑到 `clientHeight == scrollHeight`（实测 20964/20964），滚动条其实在
文档上。按它算可视窗口就等于全高 → 一次把所有分页都拉下来画完。修复：新增 `wbVirtBand()`，
先验证就近容器真的可滚，否则退回视口坐标（`getBoundingClientRect` + `innerHeight`），
并补上 window 的 scroll 监听。试过在 CSS 侧给 `.content-full .win11`、`.e-body` 定高，
但链路上 `.app-shell`/`.main-wrap` 也全是 auto 高度，要钉死得改全站布局骨架，故留在 JS 侧。

### 验证
- **真机 + 真后端**（`_repro/realapp.py`：隔离数据目录起真实 app，450/150 条种子文件，
  浏览器真实点击）：把 `app.js` 换成 0.8.38 版本后点「文档 documents」，卡片整片糊到侧栏/
  顶栏/详情面板上，状态栏显示「共 156 个项目 已显示 276」（276 = 首屏块被重取了一遍，
  即根因四）；换回修复版同一操作，侧栏干净、状态栏「156 个项目」、
  `position:relative`、块不重叠、无重复 `data-rel`。截图：
  `_repro/shot-real-broken.png` vs `_repro/shot-real-fixed-wide.png`。
- 网格/列表两种视图、6 个侧栏入口逐个点（含 753 条的「文档」智能集合）、回收站空目录、
  直接刷新与 boost 点击两条路径都过；列表视图滚到第 2 页后块内 `tbody` 全是 `TR`
  （修复前是 120 个非 `tr` 节点）；滚到 12000px 后 DOM 只剩 3 块、只多取 1 页
  （修复前一次拉满 4 页 —— 根因五）。
- `tests/js/wb-virt-test.js` 新增 S1/P1 共 5 条断言，其中 3 条在 0.8.38 上是红的（回填未关、
  offset=0 重取、`<tr>` 解析丢失），修复后 16 条全绿；`uv run pytest` 全套通过。

## 0.8.38 — 2026-09-23（列表视图首块缺 `.wb-pblock-list` 致首屏布局错乱）

0.8.37 把窗口化扩展到列表视图，但用 jsdom 实际加载 `app.js` 时发现一个会让
列表首屏排版错开的客户端 bug（网格不受影响，故 0.8.36/0.8.37 的浏览器验证没暴露）。

### 问题
`wbVirtInit` 里 `W.blocks[0] = wbVirtBlock(0, page0)` 发生在
`wbVirt = W` 之前。而 `wbVirtBlock` 靠**全局 `wbVirt.view`** 判断列表/网格块样式：

```js
if (wbVirt && wbVirt.view === "list") {
  b.className = "wb-pblock wb-pblock-list";
}
```

块 0 创建时全局 `wbVirt` 仍是 `null`，条件不成立 → **列表第 0 页块被建成网格
样式的 `.wb-pblock`**（`display:grid; left/right:3px`），而不是列表样式
`.wb-pblock.wb-pblock-list`（`display:block; left:0; right:0; width:auto`，块内
`<table class=e-table>` 要全宽块级盒）。

效果：列表视图第一排用网格窄幅渲染，与下方所有列表块（全宽表）错开 = 首屏
「布局错乱」。往下翻后新取的块（此时 `wbVirt` 已赋值）样式又正确，所以只在
首屏偶发、极易被误认为随机。

### 修复（`app/web/static/js/app.js`）
把 `wbVirt = W;` 提前到构建块 0 之前。块 0 的 `top = offsetTop + 0*blockH` 不受
影响（`blockH` 后续才测），块 0 现在正确带上 `.wb-pblock-list`，与后续块一致。

### 验证
`_repro/list_view.js`（真实 jsdom + 真实 app.js）：修复前列表首块 `count(.wb-pblock-list)=0`、
卡片未挂出；修复后 `count(.wb-pblock-list)=1`、6 张卡片全部正确包入块内。
网格视图、boost 换页竞态、异步取块竞态回归均无退步（303 个 Python 测试全绿）。

## 0.8.37 — 2026-09-23（列表视图窗口化 + 窗口化关键修正）

0.8.36 的窗口化只覆盖了网格，且客户端逻辑未经浏览器验证。
在把同一套机制扩展到列表时，用 jsdom 实际加载验证，发现并修复了
会让窗口化完全失效的几个客户端 bug（网格/列表共用同一套代码，一并修好），
同时把列表视图也纳入窗口化。服务端契约依旧**全部不变**。

### 一、列表视图窗口化（`web/static/js/app.js` + `admin.css`）
- `<table id=osfm-items>` 不能可靠承载绝对定位块：初始化时把它换成
  `<div id=osfm-items class="osfm-list-shell">` 外壳，表头抽成粘滞的
  `.osfm-list-head`，卡片 `<tr>` 按页包成「块内放 `<table class=e-table>`
  的 `.wb-pblock-list`」。单元格/选中/媒体 `data-src` 全部复用，无需改 `_item.html`。
- 块 top = 表头高度 + 页号 × 页高；容器高度 = 表头 + 总页数 × 页高。
- resize 时按 `.wb-pblock`（不是 `firstChild`，因为列表第一个子是表头）
  重测块高，并同步表头高度，保证块不压表头。

### 二、窗口化关键修正（网格/列表共用）
- **读对滚动容器**：可视窗口按 `#osfm-content`（`overflow:auto` 的滚动容器）
  的 `scrollTop/clientHeight` 算，而不是 `#osfm-items` 自己。读错会算出
  「全高窗口」，把全部页面都拉下来（和窗口化目标正好相反）。
- **保留集 key 类型**：`keep` 用字符串页号（`W.blocks` 的 `for..in` key 是字符串），
  否则 `keep.has` 永远 miss，窗口内块被误删、删完又重拉，形成拉取风暴。
- **空页不再重试**：`/files/more` 返回空（无更多）时记为已兑换，`miss` 不再命中，
  避免完成一次就重铺、重铺又重拉的死循环。
- 换页/写操作重渲染后由 `__wbVirtInit` 重新接管（`afterSwap` + `DOMContentLoaded`）。

## 0.8.36 — 2026-09-22（文件管理器窗口化渲染 + 监听缓存精准失效）

继 0.8.33/0.8.34 给「每请求开销加界」之后，这一版修的是**翻页/浏览本身的卡**：
0.8.34 每请求只渲染一页，但浏览器侧有两个没解决的累积型卡顿，文件一多就明显——

| 症状 | 0.8.35 | 0.8.36 |
| --- | --- | --- |
| 翻几百页的 DOM 卡片数 | 无限累积（数百页=数千卡片） | 只挂可视区附近约 3 页（~360 卡）
| 选中 / 状态栏 每次数算 | 扫全量卡片 | 只扫窗口内卡片（~360） |
| Agent 写文件时的缓存失效 | `watch` 每触发一次 `invalidate_caches()` 全树重算 | 只失效受影响目录 + 签名去抖 |

### 一、窗口化滚动（网格视图，`web/static/js/app.js`）
- **无限追加改窗口化**：服务器端契约（`/files`、`/files/list`、`/files/more`、
  `_content.html` 的 `data-total`/`data-view`、缩略图 `data-src`）**全部保持不变**，
  虚拟化只加在客户端层。首屏仍由服务端渲染好一页，JS 把它包成「第 0 页块」、
  丢掉 `#osfm-more` 按钮，再按滚动位置只挂可视区附近（+ 缓冲 1 页）的若干块，
  滚出视口的块**连内存一起回收**，滚回再取（命中已加载即免重取）。
- **行高统一**：`.e-fname` 由两行省略改单行省略 → 单页高度固定 = 每页条数 × 行高，
  凭 `scrollTop ÷ 页高度` 即可算出可视区落在哪几页，无需逐元素测量。
- **绝对定位块**：块 `position:absolute` + 容器高度 = 总页数 × 页高度，翻页/丢块
  **不跳滚动条**；`scroll` 走 rAF 节流。
- **仅网格视图启用**（列表无缩略图、较轻，保留既有「加载更多」）；resize 时块高
  随列数重算并重新定位。

### 二、监听缓存精准失效（`web/routers/files.py::watch`）
- 原来 `watch` 每探测到一次磁盘变化就 `invalidate_caches()`，把全树索引与所有目录
  缓存一锅端。多用户 / 单用户多窗口同时写时，`sig != last` 反复触发，
  等于翻页时每动一下就全树重算——回到 0.8.33 的大慢点。
- 改为 `invalidate_dir(path)`：只对受影响目录失效缓存，目录缓存失效不影响其它目录的
  目录缓存与全树索引；配合 `_path_sig()` 签名避免同一文件反复写入的噪声。
- 新增 `invalidate_dir_cache` 的按目录失效缓存键，未引入全局缓存破坏。

### Changed
- **CSS**：`.e-fname` 改单行省略；新增 `.wb-pblock` / `#osfm-items.wb-virt` 块样式。
- **测试**：`tests/test_workbench_perf.py::test_watch_change_busts_the_listing_cache`
  改写为「精准失效」契约——盯盘发现变化必须调用 `invalidate_dir_cache(path)`
  （而非 `invalidate_caches` 一把清全树），并伴随 `changed` SSE 事件产出。

### Tests
- `tests/test_workbench_perf.py`：`test_watch_change_busts_the_listing_cache` 断言
  命中 `"changed"` 与 `"invalidate_dir_cache"`（钉死「只作废当前查看目录的缓存」）；
  行为面另由已有的 `test_list_dir_cache_busts_on_service_write` 兜住。新增网格视图
  窗口化相关覆盖由浏览器手动验收（见下方「验收」）。

### 验收（浏览器，手动）
- 大目录网格视图：向下滚动到末尾再滚回，滚动流畅、不跳条；任务管理器进程内存无明显累积。
- 状态栏数字 ≈ 可见卡片数；缩略图按需加载。
- 另一个窗口写文件：当前窗口目录列表秒级更新，服务无卡死、CPU 不尖峰。

## 0.8.35 — 2026-09-22（首屏不再等全树索引：侧栏计数改异步加载）

继 0.8.34「给每请求开销加界」之后，这一版修掉一个它没碰的残留：0.8.34 把侧栏计数
（`category_counts` / `location_counts`）从**首屏同步等全树索引**改成了**首屏先渲染、
数字异步补**。文件很多时冷缓存进页面「空转很久」的根因，恰恰就是这步同步等待。

| 项目 | 0.8.34 | 0.8.35 |
| --- | --- | --- |
| 首屏是否等全树索引 | 等（`category/locat/inspect` 同步走盘） | 不等，先出卡片 + 占位侧栏 |
| 侧栏数字来源 | 首屏同步聚合 | `/files/side` 片段异步补（可等索引） |
| 写入后侧栏刷新 | OOB | 不变（OOB 不回归） |

### Fixed
- **首屏解耦全树索引**（`web/routers/files.py::files_page`）：`/files` 不再同步调用
  `category_counts()` / `location_counts()`。侧栏链接本身是常量（buckets / 集合
  key 写死），所以首屏就能渲染完整导航，只有数字留空等异步片段。
- **后台预热**（`workspace_service.prefetch_index`）：首屏触发一个后台线程预热索引，
  调用方立即返回；计算仍走 `_cached_counts` 单飞，重复预热自动收敛。
- **新片段 `/files/side`**：侧栏真实计数走它，可安心等待索引（次请求、可缺省，
  不卡首屏）。

### Changed
- **模板 `files.html`**：侧栏 `<aside id="osfm-side">` 加 `hx-get="/files/side"`
  `hx-trigger="load"` 首屏异步拉数；占位内容先渲染。
- **测试**：`tests/test_workbench_perf.py` 新增 4 项——阻塞 `_walk_files` 时首屏仍
  200、首屏不调聚合计数、`/files/side` 出数、`prefetch_index` 不阻塞；写入 OOB 不回归。


线上症状：工作区文件一多，`/files?cat=images&view=grid` 把服务打到完全进不去。
不是“某条查询慢”，而是一次页面开销的每一项都没有上界，乘法效应叠在一起：

| 项目 | 0.8.33 | 0.8.34 |
| --- | --- | --- |
| 首屏渲染（1500 图 + 6000 依赖文件，冷缓存） | 729ms | 30ms |
| 页面每请求 SQL | 18 条（3 次写事务） | 3 条（1 次写：审计） |
| 首屏卡片数 | 1000（全部） | 120（一页）+「加载更多」 |
| 缩略图每请求 SQL | 5.0 条（2.0 写） | 2.0 条（0 写） |
| 开一页引发的服务端缩略图请求 | 1000（浏览器并发随意） | ≤120，且前端并发上限 4 |

### Fixed
- **全树只走一次**（`workspace_service`）：索引层重写为 `scandir` 单遍下行（边
  走边判类型，不再先钻 `node_modules/.git/__pycache__` 再丢掉结果），侧栏计数 /
  位置计数 / 集合 / 最近 / 搜索 / 健康检查全部从同一份 `_index()` 派生。以前一次
  渲染要 `rglob` 四遍，目录越深越慢；现在走盘次数与视图个数无关。
- **缓存到期不打群架**：TTL 缓存 + single-flight 合流，同键并发只算一次，其余等
  结果（以前 30s 到期的瞬间，N 个标签页 = N 次全树重算）。
- **目录列表也进缓存**（`list_dir`）：以前每请求 `iterdir` + 建满量 `Entry`，1500
  文件的目录就是 100ms（翻页/切排序/返回上级各扫一遍）；现在同目录只扫一次，
  排序在内存做。服务层写操作与盯盘变化均主动作废。
- **每个带 cookie 的请求不再开写事务**（`sessions`）：滑动续期从“每请求两条
  UPDATE”改为限流 60s 一次并合并成一条（一个照片页 = 几百次写事务在 SQLite 写锁
  上串行，是直接死因）。`last_seen_at` 就是节流依据，不引入新状态。
- **设置读取上进程内缓存**（`appsettings.get_setting` 5s TTL + 写入即失效）：工作区
  根路径以前每个带 `path` 的请求都要解一次库，而网格页有几百个这样的请求。
- **`/files/watch` 改 async**：同步 SSE 生成器会整段占住一个 worker 线程 30 分钟
  （10 个标签页就能把线程池抽干）。现在不占线程，且每事件循环限流
  `HERMES_CONSOLE_SSE_MAX`（默认 6），满了推 `busy` 让前端 60s 后退避；轮询扫描
  走 `anyio.to_thread`，签名封顶 2000 项。
- **线程池显式扩容**：`HERMES_CONSOLE_WORKER_THREADS`（默认 64）在 lifespan 里抬
  anyio worker 额度，并把监听拆成不占额度的形式。

### Added
- **分页 + 增量端点**：`GET /files/more`（同源 `_listing`，返回裸卡片片段）按
  `offset` 取一页，用 `X-Osfm-More` / `X-Osfm-Offset` 响应头告知还有没有下一页；
  前端 `fetch` + `insertAdjacentHTML` 追加到占位按钮之前（多根节点片段不交给 htmx
  换，不可靠）。`PAGE_SIZE=120`、`INDEX_LIMIT=50000`（索引硬上界）。
- **媒体受控加载**（`app.js`）：网格图片/视频一律 `data-src`，IntersectionObserver
  进入视口才挂 `src`，全页并发上限 4（`load`/`error`/20s 兜底释放名额）。以前 120
  个 `<img src>` 同时发出，每个首次命中都要服务端解码原图。
- 状态栏说清“共 N 项 · 已显示 M 项”（`data-total`），不让用户以为只剩这 120 个
  文件；集合视图现在跟随地址栏 `sort`（以前 `cat=images` 永远拿到时间序）。
- 模板拆分：`files/_item.html`（单条，网格/列表两种长相）+ `files/_macros.html`
  （类型图标）+ `files/_more.html`（增量页）——首屏与增量页共用一份单条标记，
  避免两套长相在翻页时露馅。

### Tests
- 新 `tests/test_workbench_perf.py` 19 项性能护栏：全树只走一次、TTL 内不重复扫盘、
  到期合流（8 并发 = 1 次重算）、索引上界、分页窗口与 offset 钳位、重渲染保留
  offset 窗口、集合跟随 URL 排序、会话写事务节流、依赖/隐藏目录与自指软链不进入
  统计、缩略图类型与体积闸门、设置读取缓存、SSE 为 async 且满位限流、盯盘签名
  语义、目录快照与服务层写操作不脱钩。
- `test_collection_view_cached_and_invalidated` 修正：以前靠“进盘顺序碰运气”区分
  时间序，现在显式 `utime` 拉开 mtime；`_sort_entries` 加名字次级键，同时间戳不再
  随风翻转。

约束遵守：零新依赖、无 DB 迁移、守卫不变（读=require_login/写=admin/CSRF/jail/审计）；
新增的只是“每请求开销上界”。口径交换：绕过服务层直接写盘的文件，最多
`_COUNTS_TTL`（30s）后出现在列表/集合/搜索中（服务层写操作立即作废）。

## 0.8.33 — 2026-09-21（图片缩略图：网格不再拉原图——文件 OS 最后一个已知大慢点）

### Added
- **图片缩略图**：网格图片此前 `<img src=/files/raw>` 直接加载原图，开一个
  照片目录就是几百 MB 流量 + 秒级渲染。新 `GET /files/thumb`（Pillow 生成
  最长边 320px JPEG，质量 82），落盘 `data/thumbs/`；缓存键 =
  rel+mtime+size 的 sha1 → 文件一变自动换新键，同一 URL 的响应永不变化，
  浏览器可放心长缓存（`private, max-age=7d, immutable`）。
- 生成细节：JPEG `draft()` 解码降采样（大图省内存）、EXIF 自动摆正手机
  照片、临时文件原子换名（并发首访同一文件不会读到半张 JPEG）、GIF 取首帧。
- 前端：网格图片走 `/files/thumb?v=<mtime>`，图标垫底；生成失败（非图片/
  已损坏）`onerror` 移除缩略图回退类型图标，不出破图。`/files/raw` 在网格
  仅剩视频 `preload=metadata`（浏览器只拉元数据，本就不拉全片）。
- 依赖：新增 Pillow（仅缩略图用，延迟导入，不影响启动路径）。

### Tests
- 新 `tests/test_files_thumb.py` 5 项：320px 尺寸上限、缓存复用与内容变化
  换新键、非图片/缺失/越狱路径拒绝、未登录不可读（follow_redirects=False，
  TestClient 默认跟重定向会假阳性 200）、模板断言网格走 thumb 端点。

## 0.8.32 — 2026-09-21（WebOS 窗口二连修：八向桌面式缩放 + 最小化数据丢失根治；文件 OS 反馈修复）

### Added
- **窗口八向缩放**（实机反馈：只能拖右下角）：n/s/e/w + 四角共 8 个 JS 手柄，
  拖拽缩放带最小尺寸钳制（320×200）与视口边界；四角加宽好抓。CSS `resize:
  both` 弃用（浏览器原生只给右下手柄）；全屏时手柄隐藏。标题栏拖拽的边界
  钳制同步改为按当下尺寸计算（此前用 open() 时的旧尺寸，缩放后拖拽范围锁错）。

### Fixed
- **最小化数据丢失根治**（实机反馈：最小化回来数据对不上）：`minimize` 原设计
  无差别销毁内容 DOM、还原时按打开时快照重建——文本**未保存的编辑**、HTML
  预览档位、图片查看模式、PDF 滚动位置全部丢失。修订为：只有 video/audio
  仍销毁（释放解码内存，还原按进度续播），其余类型最小化只隐藏，内容原样
  回来。关闭未保存提醒（`onBeforeClose`）语义不变。

### Fixed（文件 OS 反馈，同迭代合入）
- **操作失败从此可见**：htmx 对 400/403/404 默认不换任何内容——此前重命名
  撞名、上传超限/空文件、删除保护目录、拖拽移动被护栏拒绝等一律「点了没反应」，
  即实机反馈「操作多多少少有点问题」的主来源。补全局 `htmx:responseError`
  toast，优先展示服务端 detail 文案（如「同名条目已存在」）。
- **集合/最近视图提速**：`list_collection`/`list_recent` 此前每次导航都全树
  rglob（Windows 上秒级），与侧栏计数一样走 30s TTL 缓存 + 写操作主动失效。
- **回收站操作收紧**：在回收站里重命名/复制/再删除会让回收站 manifest 指
  错原位置（还原时落错地方）；工具条重命名/复制/删除按钮与键盘 Delete 在
  回收站视图一律禁用，只留「还原到原位置」。

### Tests
- WM shim 增至 **107 断言**：八手柄存在性、se/nw 拖拽尺寸与位置数学、最小
  尺寸钳制、全屏时手柄不响应、文本未保存编辑跨最小化保留、媒体类仍销毁重建。
  mini-DOM shim 补 `offset*` 与 style 同步（浏览器语义）。
- 文件 OS：集合/最近视图缓存命中与写操作失效回归；全套件通过（Windows 实机）。

## 0.8.31 — 2026-09-21（单迭代合入：HTMX 卡顿根治 + 插件/记忆装得上用得起 + 文件管理器 OS 化基本盘）

### Added
- **文件管理器 OS 级基本盘**：重命名 / 软删除（`.trash/` 回收站，manifest 记原位置，
  侧栏新入口「回收站（可还原）」）/ 复制副本（`xxx-副本` 自动后缀）/ 新建文件夹·文件 /
  多选（Ctrl 点选 + Shift 范围）与批量删除·复制·归档 / 拖拽移动（进文件夹、面包屑、
  侧栏位置）/ 右键菜单补全（重命名·复制副本·删除·新建，空白云右键也可新建）/
  键盘 Delete·F2·搜索框 Enter 直达全局搜索。
- **全局搜索**：`GET /files?q=` 按名字子串匹配整个工作区（隐藏目录与 `.trash` 除外，
  rglob 凑满即停），搜索结果独立视图 + 写操作后原地重渲染。
- **目录变更自动刷新**：`GET /files/watch`（SSE，2.5s 单层签名对比，变化才推
  `changed`，30 分钟收尾；聚合视图直接 `bye`）。前端仅在文件页挂载、进对话框/
  有选中/输入中时抑制自动刷新。
- **记忆 provider 连接测试**：表单「测试连接」按钮 → `POST /memory/test/{pid}`，
  按 provider 探测（mem0/supermemory/hindsight/retaindb/honcho 官方端点，
  openviking 用自建地址占位替换，本地方案如实返回「无需探测」）；密钥表单值
  优先、回落 `.env`；401/403 明确报「密钥被拒绝」。`{FIELD}` 占位符通用替换
  （openviking 的端点占位此前绑死 probe_auth_field 永远替换不到）。
- **渠道稳定度分层**（社区调研落地）：`PlatformDef.stability`（官方 API /
  非官方桥·可能掉线 / 需自建服务 / 无需外部平台）+ 卡片徽标；飞书·Telegram·
  企业微信·钉钉标记推荐并排卡片墙前列，WhatsApp/Signal 如实标注掉线风险。
- **忘记密码自助重置**：`start.bat reset [用户名]` / `./start.sh reset [用户名]`
  直接进 `console_admin.py reset-password` 重置向导，不用再登服务器翻库；
  启动横幅加提示。
- **ByteRover CLI 安装改 npm 通道**：`npm install -g byterover-cli` 三平台通用，
  弃 curl|sh（Windows 无 sh）。agentmemory 装完收尾仍按需走 venv pip 兜底。

### Fixed
- **插件安装 Windows 必挂**（0.8.30 实机根因）：命令拼接从 shlex.quote（POSIX
  单引号，cmd.exe 不认）改为 win32 走 `subprocess.list2cmdline`、POSIX 走
  `shlex.join`；显式带 `--enable`（后台无 TTY，官方 "Enable now? [y/N]" 永远
  取默认 No，装完即隐身）；任务收尾兜底——CLI 不认参数时按命令回读插件名补
  白名单。
- **嵌套插件不可见**：插件页扫描从单层 glob 改递归两层（`plugins/memory/<name>`
  等官方豁免类目此前永远不显示）；豁免类目显示「自动加载 + 官方豁免白名单」，
  不再提供误导性启停开关；删除按真实相对目录定位。
- **记忆依赖安装 POSIX-only**：依赖安装改 Python 驱动脚本（自动探测 hermes
  venv 解释器，Windows `Scripts/python.exe` / POSIX `bin/python`），不再拼
  shell；hindsight 按 local/cloud 模式装对应依赖。
- **memory 路由补认证**：`/memory/*` 挂 router 级 csrf_guard（此前仅 samesite
  cookie 单防线，与其余写路由不一致）。
- **HTMX 操作卡顿四连修**：
  1. `supervisor.status/version` 加进程内 TTL 缓存（10s/300s，按 home 键控），
     顶栏 pill（15s 轮询）与服务页（8s 轮询）不再每次现起 `hermes` 子进程；
     Gateway 动作/安装类任务收尾（`jobs._finish_job`）与动作提交前主动失效，
     体检页 `force=True` 仍真实时探测；
  2. 文件工作台 category/location/inspect 计数加 30s 缓存，上传/归档/重命名/
     删除/复制/新建/移动/保存后主动失效（此前每次 boosted 导航都全树递归）；
  3. 侧栏导航 `hx-boost` 化，换页不再整页白屏；
  4. app.js `afterSwap` 里未定义的 `$id` ReferenceError 修复（换页后工作台
     半瘫的根因）；模态/抽屉/侧栏全部改事件委托，boost 换 DOM 后不再失灵。
- **审计注水**：`/files/raw` 只在 `dl=1`（真下载）记 `files_download`——此前
  每个缩略图 `<img>` 都写一条 INSERT，开一个图片目录几十上百条。
- **DB 快照句柄泄漏**：`sqlite3` 连接的 with 只管事务不关句柄，Windows 上
  轮转 unlink 旧快照报 WinError 32（全套件因此红一条，pre-existing）；
  改 `closing()` 显式关闭。
- **页脚版本号失真**：`app/__init__.__version__` 停在 0.8.0，UI 显示与
  pyproject 脱节；两处统一随本次抬到 0.8.31。

### Tests
- 新 `tests/test_iteration_0831.py` 25 项：安装命令跨平台与 `--enable`、嵌套
  插件可见/自动加载/删除、`_finish_job` 白名单兜底、状态/版本缓存命中与失效、
  工作区基本盘全操作与护栏、计数缓存失效、全局搜索、路由回路（新建→重命名→
  复制→批量删→回收站→还原）、move dest、搜索页、watch 聚合视图、连接测试
  （本地跳过/拒绝连接/非法协议）、渠道稳定度与推荐排序、memory 路由 CSRF。
- `test_memory.py` 驱动脚本断言重写（落盘脚本含 pip 调用与双平台 venv 布局）
  + ByteRover npm 通道断言。
- 全套件通过（Windows 实机）+ WM shim 83 断言全过。

## 0.8.30 — 2026-09-20（WebOS 窗口体验五项：PDF/编辑/多任务/不可预览/HTML 沙盒）

### Added
- **HTML 沙盒预览三档**（WI-19/B）：纯静态 → 脚本开 → 完整预览。
  新 `html` kind（`.html/.htm` 从 text 拆出）+ `sandbox` iframe；完整预览
  进档前显式确认，走 `/files/raw-html` 宽松源（`preview_csp` 短名单：
  jsdelivr/unpkg/cdnjs/Google Fonts/Tailwind，可配 `preview_cdn_allowlist`
  增补）；顶层文档同样被 `sandbox`（无身份）。未命中名单的资源逐个
  降级，整页不白屏。
- **文本编辑保存**（WI-16）：viewer 工具条编辑/保存/另存副本；新
  `POST /files/save`（覆盖记 `files_save_overwrite`，副本自动唯一化记
  `files_save_copy`）；截断大文件禁覆盖防丢数据；`win.onBeforeClose`
  未保存提醒（通用钩子）。
- **多任务栏**（WI-17）：窗口计数徽标（N 个窗口/M 已最小化）+ 全部
  还原/关闭 + chip 横向容器 + 同文件重复双击聚焦已有窗口。
- **按需加载机制**（WI-15）：`WM.loadScript()`（promise 缓存、失败摘除
  可重试），重型 viewer 依赖只在首次打开对应类型时注入。

### Fixed
- **PDF 打不开**（WI-15）：根因是全局 `X-Frame-Options: DENY` +
  `frame-ancestors 'none'` 拦了同源 iframe。`/files/raw`（inline）豁免
  为 `SAMEORIGIN` + `'self'`，其余页面保持；框内文档继承全局 CSP，
  raw 分支 `script-src` 加 `'unsafe-inline'`（执行门仍在 sandbox）。
- **窗口不可缩放**（WI-16）：`.wm-win` 加 `resize: both`（Windows 式右下
  手柄，全屏/最小化禁用）。
- **换页选中残留 ReferenceError**：`afterSwap` 经 `window.__wbClearSel`
  守卫调用。
- **归档 `htmx.ajax` 静默丢弃**：source 须是已挂载节点，改挂隐藏表单。
- **exe 等不可预览**（WI-18）：双击不建窗不渲染，toast + 直接下载。

### Tests
- shim 83 断言（任务条/单例/按需加载/不可预览/HTML 三档）+ 
  `test_files_save.py` 6 项 + viewer/raw/raw-html 回归；全套件 245
  passed。E2E 实锤：PDF 原生渲染、窗口编辑保存落盘、双开双最小化
  计数与一键还原、exe 无窗下载、沙盒开关与 CDN 对照。

## 0.8.29 — 2026-09-20（文件管理器 HTMX 补完：上传/归档/导航无整页刷新）

### Added
- **上传 HTMX 化**（WI-A/A2）：表单 `hx-post` + `hx-swap="none"`，服务端
  HTMX 请求回 200 + `HX-Trigger`（`console:toast` + `osfm:nav`），前端
  `osfm:nav` 监听走 boosted 导航到目标目录——无白屏、有历史记录。
  教训：HTMX 1.9 没有 `hx-target="none"`（2.x 语法），会按 CSS 选择器
  查不到目标而静默弃请求；且 1.9 的 `HX-Redirect` 是整页跳转。
- **toast 跨 boosted 换页存活**：`showToast` 每次取当前 `#toast-stack`
 （旧节点换页后已游离），换页毁掉的 toast 在 `htmx:afterSwap` 后于新
  页面重放；片段换页不动 toast 栈，不重复弹。

### Fixed
- **归档 `htmx.ajax` 静默不发请求**：`htmx.ajax` 的 source 须是已挂载
  节点（1.9 内部连通性检查，未挂载表单直接丢弃）。现挂隐藏表单，
  `htmx:afterRequest` 后移除（+ 30s 兜底）。归档后网格原地重渲染 +
  侧栏 OOB + toast，不整页刷新。
- **换页/局部刷新后选中残留**：通用 `afterSwap` 处理器误调工作台
  IIFE 内的 `wbSelect`（跨作用域 ReferenceError）。现经
  `window.__wbClearSel` 守卫调用，归档/换页后详情面板与状态栏正确
  复位。
- **上传目标目录计算**：根目录文件 `rsplit` 回退到 `/files`，不再产出
  `/files?path=<文件名>` 错误导航。

### Tests
- `test_upload_htmx_returns_nav_trigger`（200 + HX-Trigger
  toast/osfm:nav、无 HX-Redirect）+ `test_upload_non_htmx_redirects`
  （303）。E2E 实锤：上传自动进 downloads、plan.md 开窗/最小化/
  还原、boosted 导航任务条存活、两次归档原地消失 + 侧栏计数。

## 0.8.28 — 2026-09-20（窗口管理器前端回归测试 + 文件内容缓存再验证）

### Added
- **window-manager.js 前端回归测试**：零依赖 Node mini-DOM shim 直跑
  `tests/js/wm-test.js`（47 断言：开/最小化/还原/关闭/全屏/多窗口
  z-order、任务条 chip 归属、媒体续播、viewer 注册表、openFile 扁平
  描述符、标题 XSS 转义、工厂异常降级）；`tests/test_window_manager_js.py`
  pytest 包装（无 Node 时跳过）。消除 spec R2 声明的"无自动化前端
  测试"边界。

### Fixed
- **`/files/raw`、`/files/zip` 加 `Cache-Control: no-cache`**：工作区文件
  可变（Agent 随时产出），此前浏览器启发式缓存会让 viewer/缩略图拿到
  更新前的旧内容（E2E 实锤：换图后窗口里还是坏图）。现强制按 ETag
  再验证。

## 0.8.27 — 2026-09-20（WebOS 窗口管理器：文件双击开窗在线查看）

### Added
- **WebOS 窗口管理器**（WI-10/11）：新增 `window-manager.js` 通用 WM——
  文件管理器双击文件在 OS 风格窗口中打开（标题栏：图标 + 名称 +
  最小化/全屏/关闭；可拖拽、级联定位、z-order 焦点）。底部任务条
  （chip）：最小化只留 chip，点 chip 还原窗口并重建内容；关闭销毁
  DOM 与 chip。`#wm-root` 在 `htmx:afterSwap` 后重挂，窗口跨页面
  导航存活（OS 语义）。多个窗口可同时打开。
- **六个内置 viewer**（WI-11）：image（适配/原始大小切换）、video
  （原生控件、默认 78% 宽、还原时续播保存位置）、audio（大图标 +
  原生控件、续播）、pdf（iframe 浏览器原生渲染）、text/code（pre +
  截断提示）、none（不支持在线打开 + 下载）。新增文件类型 = 后端
  一行分类 + `WM.register` 一个条目。
- **Viewer 描述符 API**（WI-9）：`GET /files/viewer?path=...` 返回
  `{ok, kind, rel, name, size, text?, truncated?}`；文本文件带 512KB
  截断内容；记 `files_open` 审计。
- **测试**（WI-12）：`tests/test_files_viewer.py`——六类 kind 映射、
  文本截断、缺失文件 404、需登录。

### Changed
- 文件管理器双击文件由跳预览碎片改为开窗在线查看（保留旧
  `wbPreviewContent` 作降级回退）。

### Fixed
- 任务条 chip 误 append 到 `#wm-root`（丢失定位 + `pointer-events:none`
  不可点）→ 改 append 到 `#wm-taskbar`。
- `window-manager.js` 漏声明 `var viewers = {}`（加载即 ReferenceError）。
- `WM.openFile` 改扁平描述符契约（`{title, kind, rel, name, size, text,
  truncated, width}`），viewer 工厂直接读 `d.name/d.size/d.text`。

## 0.8.26 — 2026-07-21（Linux sudo 安装流 + 服务页 HTMX + agentmemory 跨平台 + 结构拆分）

### Added
- **Linux sudo 安装流**（WI-1）：`detect_privilege` 五态探测（windows/root/
  passwordless/password/nosudo，30s 缓存）；需要密码时表单收密码，经 0600
  passfile 注入 `sudo -S`（不进命令行、不进 job_runs.command、不进审计
  details），浏览器组件补装同款通道。
- **服务页 HTMX 局部刷新**（WI-2）：所有动作表单改 `hx-post` +
  `hx-target=#job-panel`；SSE 重挂幂等（`attachJobStream`）；确认弹窗走
  `htmx.ajax` 提交；操作完成 toast 提示。
- **agentmemory 插件安装跨平台**（WI-3）：POSIX 一行命令换成 Python 驱动
  脚本（GitHub 直连 → ghproxy.cn 镜像双通道，gzip 魔数校验，分步进度，
  真实 hermes home 落位），Windows 实机 8s 装完 30MB。
- **插件装完"重启 Gateway 生效"提醒**（WI-4）：任务收尾置
  `plugin_restart_pending` 标记，记忆页任务碎片 3s 轮询自动显示横幅，
  提交 Gateway 动作即清除。

### Changed
- **结构拆分**（WI-5）：`installer.py`（856 行）拆出 `jobs.py`（通用后台
  任务引擎：submit/取消/历史/日志/收尾副作用）+ 新增 `write_job_script`
  驱动脚本统一落盘助手（网关动作、agentmemory 两处复用，不再各拼引号）；
  `memory_service.py` 拆出 `memory_providers.py`（方案定义目录，新增
  provider 只改这里）。旧调用路径（`installer.submit` 等）重导出兼容，
  行为零变化。

### Fixed
- **conftest 双重导入拆数据目录**（测试基建真 bug）：pytest 以顶层
  `conftest`、测试文件又以 `tests.conftest` 各导一次，模块级
  `HERMES_CONSOLE_DATA` 被重置——已建立的 DB 连接跨两个目录，登录
  401/任务状态错乱。环境初始化改幂等（`setdefault` 哨兵）。

## 0.8.25 — 2026-09-17（Linux 状态查询 500 修复：_pid_alive 误用 signal.kill）

### Fixed
- **Linux 上一切状态查询 500**（实机 traceback 实锤）：`_pid_alive` 的 POSIX 分支
  误写 `signal.kill(pid, 0)`——**signal 模块没有 kill 这个函数**（正确是
  `os.kill(pid, 0)`）。Windows 走 ctypes 分支掩盖了它；Linux 上一旦
  `gateway_state.json` 带 pid 出现，`/service` 页面、状态碎片、所有操作
  全部 500——"点了没反应"的又一个真凶。现改 `os.kill(pid, 0)`，并按
  ProcessLookupError→死 / PermissionError→活 / 兜底不抛异常 三段处理，
  该函数绝不允许再把状态页拖垮。
- 回归测试：当前进程判活、已退出进程判死（POSIX；修复前该用例直接
  AttributeError）。

## 0.8.24 — 2026-09-17（后台任务 stdin 断开：交互提问不再卡死网关安装）

### Fixed
- **`hermes gateway install` 弹交互提问卡死**（实机实锤）：hermes CLI 用
  `sys.stdin.isatty()` 判断"有人交互"——后台任务继承了终端的 TTY，于是弹出
  "Start the gateway now after installing the service? [Y/n]:" 等回车，无人应答、
  卡满 180 秒超时报错。现在**所有后台任务与之调用的 hermes CLI 一律 stdin 断开**
  （`subprocess.DEVNULL`），hermes 自动进入非交互模式、采用安全默认值
  （立即启动 + 开机自启）——客户无需、也无法"回车"。

### Tests
- 两处 stdin=DEVNULL 断言（任务子进程 / supervisor CLI 调用）。

## 0.8.23 — 2026-09-17（网关任务进度提示 + 任务日志首帧去重）

### Fixed
- **任务日志重复显示**：面板首屏由服务端渲染、SSE 又从位移 0 推送同一批行，
  日志头会出现两遍；现在 SSE 首帧先清空再追加，不重不漏。
- **网关启停任务中途全静默**：驱动脚本改为**先打印进度提示**
  （"正在执行 hermes gateway start（首次自动注册系统服务，最长约 3 分钟）"），
  执行期间不再看起来像卡死；结束后照常输出结果或错误原因。

## 0.8.22 — 2026-09-17（确认弹窗提交重写：确认后原生直提，"点了没反应"根除）

### Fixed
- **确认弹窗 → 提交链路重写**：此前确认后走 `requestSubmit()` 的二次 submit 事件往返，
  在真实浏览器 / 快速操作时序下会**静默失败**（实机复现：弹窗点"确认执行"后无 POST、
  无任务、无任何提示）。现改为**确认后直接原生提交**（`form.submit()`），整个二次
  事件往返删除；连点防抖（dialog 已打开不再重复 showModal）；任何异常弹红色
  Toast——"点了没反应"不可能再静默发生。
- **确认词字段接上**：网关停止/重启表单此前缺 `data-confirm-field` 隐藏字段，
  且旧 JS 取字段的调用是死代码（对函数对象调 `querySelector`）——确认词永远到不了
  服务端，必然报"请输入正确的确认词"。现字段补齐 + 确认词（typed）正确写入；
  技能 / MCP / 供应商 / 插件删除的确认词链路一并复活。

### Verified
- 真浏览器（Windows Chromium）实测：此前**必挂的"快速连点"序列**（点开始安装 →
  立即点确认执行）现在稳定 `POST /service/install 200` → 任务 ok →
  SSE 流建立 → 日志完整；常规节奏同样通过。

## 0.8.21 — 2026-09-17（运行日志占位文案分场景 + 全新机安装链路浏览器实测）

### Fixed
- 「运行日志」空状态不再只有一句含糊的"日志文件为空或不存在"：未安装时明说
  "装好并启动 Gateway 后自动出现（gateway.log / errors.log 由 Gateway 进程创建）"；
  errors.log 为空时提示"没有报错是好事"。首装用户不再疑惑"文件为什么没有"。

### Notes
- 全新机（零安装）全链路浏览器实测通过：伪装"什么都没装"的隔离实例（清空数据 +
  屏蔽 hermes 检测）→ 点击「开始安装」→ 确认 → `POST /service/install 200` →
  任务面板实时推送（SSE，`GET /service/job/stream 200`）→ 完成后「成功」标签
  原地更新、取消按钮自动收起 → jobs 日志与 job_runs(ok) 双落盘。

## 0.8.20 — 2026-09-17（任务日志 SSE 实时推送 + 任务面板常驻）

### Added
- **任务日志 SSE 推送**（`GET /service/job/stream`）：点安装/修复/补装组件后，执行输出
  逐行**实时推送**到页面顶部「任务面板」——毫秒级到达、无需刷新、不靠轮询；
  任务结束推送 done，成功/失败标签原地更新，取消按钮自动收起。
- **「任务面板」常驻**：没有任务时也展示（并说明日志将出现在哪里、同步到终端与
  jobs 文件）——不再出现"页面上连个看日志的元素都没有"。

### Tests
- SSE 事件流生成器级单测（job → lines → done，无网络依赖）；
  增量日志读取（字节精度、半行不重复）；面板片段断言更新（data-stream、轮询移除）。

### Notes
- 真实浏览器 + 隔离实例端到端演练通过：点击 → `POST /service/install 200` →
  任务创建 → 面板实时滚动（uv/Git/Python 各阶段输出）→「取消任务」→
  状态 failed + 日志留痕「任务已被手动取消」。

## 0.8.19 — 2026-09-17（官方源全量安装 + 点完必见日志：预检拦截移除）

### Changed
- **大陆镜像源移除**：安装源固定为官方源（GitHub / Nous Research），**全量安装**
  （不跳过任何组件；仅保留后台无 TTY 必需的 `--skip-setup` / `-SkipSetup
  -NonInteractive`）。测速选源、CN 镜像、`HERMES_CONSOLE_INSTALL_SOURCE` 全部下线。
- **预检拦截移除**：点「确认执行」**必然生成任务**——成败与原因全在任务日志里
  （curl/pip 的真实报错就是最好的诊断），不再出现"点了没反应"（错误横幅在页顶，
  用户只看到下半页空表格）。update 同理。

### Added
- **任务日志首屏即刻可见**：任务创建时**同步**写入「任务已启动（时间）+ 要执行的命令 +
  提示语」——POST 返回的首屏（页顶任务面板 / 最近任务行）立刻有字，
  不再有"curl 下载脚本几十秒全静默"的空白期；输出随后实时追加（终端同步打印）。

### Tests
- E2E（隔离实例）：点击→首屏含面板/已启动/命令/查看输出 → 任务执行三段输出全部落盘
  → 结束后常驻面板可回看；全量回归绿。
- 命令契约/离线不拦截/模板文案等用例同步更新。

## 0.8.18 — 2026-09-17（自动测速选源：海外走官方全量、大陆走镜像）

### Changed
- **安装源从"镜像优先"改为"自动测速"**：国内镜像与官方源**并发探测、快者优先**
  ——大陆服务器自动走镜像（全链路国内源），海外服务器自动走官方源；
  无需任何配置（`HERMES_CONSOLE_INSTALL_SOURCE=cn|official` 仍可固定）。
  此前"镜像可达即用镜像"会让海外服务器也默认走镜像。
- **官方源 = 全量安装**：官方脚本不再带 `--skip-browser`（浏览器引擎/组件由官方
  脚本一次装齐）；国内镜像源保持 core-only + 装后组件接力（镜像脚本本身跳过浏览器段）。
- 安装卡片文案同步（"自动测速…示例为国内镜像源"）。

### Tests
- 测速取快者（镜像快/官方快/全挂三态）、官方全量命令不含 --skip-browser、
  离线拦截改依测速结果断言。

## 0.8.17 — 2026-09-17（组件补齐全覆盖：每个被跳过的组件都有国内通道）

### Added
- 「安装 / 补装组件」按钮范围扩大（原"补装浏览器组件"）：CN 镜像 core-only 模式跳过的
  组件，逐个对照官方安装器实现国内折中通道（**全部 best-effort，不阻塞主流程**）：
  - **camofox 浏览器服务**：`npm install -g @askjo/camofox-browser` + npmmirror registry；
  - **语音/唤醒依赖（onnxruntime / faster-whisper）**：`uv pip install -e ".[wake,voice]"`
    + 清华 PyPI；
  - **系统件（build-essential / ripgrep / ffmpeg）**：apt（服务器自带国内镜像）+ 无密码
    sudo 探测，可装则装；
  - **Computer Use 驱动（cua-driver）**：上游只在 GitHub raw 分发——直连失败自动走
    ghfast / gh-proxy 加速镜像兜底；失败不影响浏览器自动化，可事后
    `hermes computer-use install` 重试；
  - **npx 缓存预热**（playwright / agent-browser）：首次使用不再现场下载。
- 脚本统一导出国内通道：`NPM_CONFIG_REGISTRY=npmmirror`、
  `PIP_INDEX_URL`/`UV_DEFAULT_INDEX=清华`。
- 至此组件矩阵：Node（npmmirror）→ 浏览器引擎（npmmirror）→ Browser Use CLI（uv+清华）
  → camofox（npm+npmmirror）→ 语音（uv+清华）→ 系统件（apt）→ CU 驱动（镜像兜底）。

### Tests
- 补装脚本内容契约扩展（camofox / wake,voice / build-essential / ghfast 兜底各断言）；
  bash -n 语法校验通过；全量回归绿。

## 0.8.16 — 2026-09-17（CN 镜像最小模式的 Node 缺口修复）

### Fixed
- **国内镜像脚本是 "core only" 最小模式**（源码明牌 `install_tier "core only (China
  mirror minimal mode)"`）：核心全装（uv/Python/git→cnb.cool 镜像/venv/依赖/TUI），
  但跳过 Node、Playwright/agent-browser、Browser Use CLI、Computer Use 驱动等可选件；
  且其 `--skip-browser` 会**连 Node 一起跳过**——而我们的浏览器补装脚本依赖 `npx`，
  全新 CN 安装会在此断链。现补装脚本自检：**缺 npx 时自动从 npmmirror 补装
  Node（v22.14 → v20.19 依次尝试，x64/arm64、Linux/macOS）**，补完再装
  Playwright 引擎与 Browser Use CLI。
- 组件盘点（终态）：核心（镜像）＋浏览器引擎（npmmirror）＋Browser Use CLI（uv tool）
  = 浏览器自动化核心能力齐备；Computer Use 驱动、语音/唤醒依赖仍属官方脚本附加项
  （服务器/消息渠道场景用不到，需要时可在终端跑一次官方脚本补齐）。

### Tests
- 补装脚本必须包含 npmmirror Node 自愈段（断言锁死）；bash 语法校验通过。

## 0.8.15 — 2026-09-17（国内镜像安装源：大陆用户全链路提速）

### Added
- **国内镜像安装源（默认优先，官方自动兜底）**：官方为中国大陆提供镜像站
  （`res1.hermesagent.org.cn`，全链路换国内源：uv/Python/git→cnb.cool、
  pip→清华、npm/node→npmmirror）。控制台现在自动选择：**镜像可达走镜像、
  否则回退官方 GitHub**；两者都不可达时给出人话提示 +「仍要执行」口子。
  依据腾讯云开发者社区《Hermes Agent 2026 最新安装教程》实测推荐。
- `HERMES_CONSOLE_INSTALL_SOURCE=cn|official` 可显式固定安装源（固定源时不跨源回退）。
- 更新预检放宽：GitHub 或 cnb.cool（镜像安装的 agent 走这里）任一可达即可，
  国内装机不再被 GitHub 预检误拦。
- 安装卡片明示当前安装源（"国内镜像源（hermesagent.org.cn，大陆优先）"）。

### Tests
- 双源命令形态、自动选源优先级与回退、显式源固定行为（3 项新增）。

## 0.8.14 — 2026-09-17（网关启停改为后台任务：每次操作都有流水）

### Changed
- **启动/停止/重启 Gateway 全部转成后台任务**：点击后页面即时返回（不再整页
  "卡死"等待），输出实时三处可见——控制台终端、页面顶部任务面板、
  `jobs/job-N.log`；可取消、可在「最近任务」回查。此前是同步调用：阻塞页面、
  且没有任何流水日志（实机踩坑）。
- 任务历史新增「启动 / 停止 / 重启 Gateway」中文标签。
- 启动按钮的确认弹窗注明"首次自动注册服务，最长约 1 分钟，请勿重复点击"。

### Fixed
- **命令超时不再只有一句话**：超时错误带出已捕获的输出（它当时在做什么）；
  且慢命令单独放宽——`gateway install` 180 秒、`start/stop/restart` 120 秒、
  `update` 300 秒。此前统一 20 秒硬限制，systemd 用户会话冷启动被误判为卡死。

### Tests
- 启停走后台任务、危险动作确认词、驱动脚本可编译（3 项）；
  超时消息带输出、慢命令分级超时（2 项）。

## 0.8.13 — 2026-09-17（任务输出双通道：终端实时打印 + 网页/文件可查）

### Changed
- **后台任务输出实时回显到控制台终端**（tee 语义）：在跑控制台的那个终端窗口里，
  安装 / 更新 / 补装浏览器 / 扫码等任务的输出会**实时打印**——"有没有在执行、
  执行到哪一步"当场可见，不再只落文件。
- 输出仍然三处在：① 终端实时（本次新增）② 网页「任务面板」实时刷新
  ③ 文件 `<数据目录>/jobs/job-N.log`（例如 `~/.hermes-console/jobs/`）。

### Tests
- 任务输出必须同时到达终端与日志文件（双通道断言）；既有用例适配 PIPE 读取。

## 0.8.12 — 2026-09-17（安装完整性体检：装到哪一步、缺什么、怎么补）

### Fixed
- **"已安装"判定过浅**：此前只要 hermes 可执行文件存在就显示"已安装"——而该文件
  在安装早期就会生成，下载中断 / venv 半成品 / 网关未注册都会被说成"装好了"。
  现改为**「安装完整性」逐项体检**：可执行文件 / 源码与虚拟环境 / 网关服务（Linux）/
  浏览器引擎 / Browser Use CLI，每项如实显示 完成/未完成 + 怎么补；并提供两个幂等
  按钮：「继续 / 修复安装」与「安装 / 补装浏览器组件」。

### Tests
- 半成品状态（有二进制、缺 venv）不得报"全部完成"；服务页必含体检表与两个按钮。

## 0.8.11 — 2026-09-17（Linux 首启网关自动注册服务）

### Fixed
- **Linux 服务器首启 Gateway 报「✗ Gateway service is not installed」**：
  官方网关在 Linux 上是 systemd 服务，"先 `hermes gateway install` 注册、再 start"
  是必需步骤——但这套术语不该丢给小白。现在控制台点「启动 Gateway」检测到该状态会
  **自动执行 install（幂等）再重试 start**；restart 的降级路径同样兜底
  （stop 报未注册时忽略，交给 start 自动注册）。

### Tests
- 自动注册→重试链路、注册失败的人话报错、restart 降级兜底（3 项新增）。

## 0.8.10 — 2026-09-17（组件状态常驻可见：浏览器装没装、去哪装，一眼可见）

### Fixed
- 「补装浏览器组件」按钮此前只在“检测到缺失”时才出现——已装或状态不明时
  用户找不到入口、不知道装没装。现改为**「组件状态」卡片常驻安装页**：
  浏览器引擎（Chromium）、Browser Use CLI 各自显示 已安装/未安装，
  「安装 / 补装浏览器组件」按钮**永远都在**，随时可点（幂等，已装会快速跳过）。

### Tests
- 已装状态下页面必含组件状态卡片与按钮（断言锁死）。

## 0.8.9 — 2026-09-17（浏览器组件回归默认必装：主安装后自动接力）

### Fixed
- **修正 0.8.8 的过正**：浏览器组件（Agent 核心能力，Browser Use）不默认跳过、
  不等用户手动点击——主安装完成（`--skip-browser` 快速收口）后**自动接力**
  补装任务，最终状态 = 全量安装。
- 接力脚本补齐 **Browser Use CLI**（浏览器自动化默认后端，`uv tool install browser-use`，
  PyPI 国内镜像兜底）；浏览器引擎增加 **Ubuntu 新版兼容构建重试**
  （`PLAYWRIGHT_HOST_PLATFORM_OVERRIDE`，对齐官方安装器逻辑）。
- 安装卡片与补装卡片文案同步（装完自动补装；失败可点按钮重试）。

### Changed
- 安装表单移除「跳过浏览器组件」勾选：核心能力不设"默认缺失"路径；
  带宽极紧时可对接力任务点「取消任务」。

## 0.8.8 — 2026-09-17（浏览器组件：默认跳过 + 一键补装自动切国内镜像）

### Changed
- **主安装默认跳过浏览器组件**（Linux/macOS）：官方安装器里的 Playwright 下载
  （Chromium + FFmpeg + Headless Shell，约 270MB，源为 cdn.playwright.dev）在国内
  时通时断、常卡死；安装表单的「跳过浏览器组件」改为默认勾选，主流程快而稳。
- **补装浏览器组件改为双源脚本**（不再重跑整个安装）：官方源最多等 5 分钟，
  慢/卡自动切 npmmirror 镜像（已实测该镜像含 cft 构建文件），全自动，用户只点一下；
  未装 Hermes 时给中文人话提示而非报错页。
- 补装卡片文案同步：「官方源优先，慢或不通会自动切国内镜像，无需手动配置」。

### Tests
- 补装脚本内容契约（镜像域名/双源超时/环境变量）、跳过项默认勾选、
  未安装时的拒绝提示。

## 0.8.7 — 2026-09-17（卡住能撤 + 跳过浏览器 + 一键补装）

### Added
- **取消任务**：运行中的后台任务面板新增「卡住了？取消任务」按钮；服务端按
  进程树整体终止（POSIX 进程组 /Windows taskkill /T），状态与日志自动收尾，
  不再出现"任务卡死只能重启控制台"。
- **跳过浏览器组件**（Linux/macOS）：安装表单新增勾选项——网络过不去时先装主流程
  （跳过 Playwright Chromium 约 170MB 下载）。
- **补装浏览器组件**：已装 Hermes 但浏览器引擎缺失时，安装页自动出现
  「补装浏览器组件」按钮（重跑官方安装，幂等补齐）。
- 常规表单提交（安装/更新/取消）成功后在页面顶部显示绿色提示横幅
  （此前非 HTMX 提交的成功信息不可见）。

### Tests
- 取消真实子进程（整树终止 + 状态收尾 + 日志留痕）、跳过浏览器命令拼装、
  补装/取消端点、浏览器引擎探测、运行中面板片段。

## 0.8.6 — 2026-09-17（安装进度实时可见 + 预检误报自救）

### Fixed
- **运行中任务面板藏在「安装与更新」页签里**：点完安装页面回到概览页签，
  进度面板不可见，看起来像"什么都没发生"。现移到页签外，任何页签都能看到进度。
- **任务面板只刷新一次就停**：面板根节点没有自带轮询属性，outerHTML 换掉自己后
  触发链断裂（此后靠手动刷新才更新）。现由面板根节点自带 2s 轮询、结束后自动定格。
- **日志只看得到尾巴 60 行**：改为最多 500 行 + 新行自动滚动到底（日志区固定高度）。
- **网络预检 HEAD 误报**：部分网络能 GET 不能 HEAD，预检失败直接拦下安装，
  而手动 `curl` 明明能跑。现 HEAD 失败自动回退 GET 再探一次。
- **预检拦截后无路可走**：拦截页现在给出「仍要执行安装」按钮（force=1），
  探针只是参谋，决策权留给用户。

### Tests
- 新增：GET 回退探测、拦截给 force 口子、force 绕过预检。
- 全量回归绿（除 1 个预存 Windows 文件锁旧疾）。

## 0.8.5 — 2026-09-17（默认监听所有接口：开箱即用于服务器）

### Changed
- **默认监听地址改为 0.0.0.0（含本机回环，开箱即用）**：`127.0.0.1` 与对外访问
  不再二选一，开一个全通。`start.sh` / `start.bat` / 应用默认 / `.env` 示例同步；
  仍可用 `HOST` / `HERMES_CONSOLE_HOST` 改回回环。非回环启动继续显式警告
  密钥/HTTPS/白名单；浏览器自动打开回环地址（部分新版浏览器打不开 0.0.0.0）。

### Tests
- tests/test_start_scripts.py 同步默认断言 + BROWSE 行为 + 应用默认绑定。

## 0.8.4 — 2026-09-17（启动脚本支持监听地址覆盖：服务器对外服务）

### Added
- **start.sh / start.bat 支持 HOST / PORT 覆盖**：`HOST`（默认 127.0.0.1）、`PORT`
  （默认 8420）均可经环境变量覆盖，并回退读取 `HERMES_CONSOLE_HOST/PORT`
  （与应用设置同名）；服务器上 `HERMES_CONSOLE_HOST=0.0.0.0 ./start.sh` 即可对外提供服务；
  非回环监听时启动脚本显式警告密钥/HTTPS/白名单三件套。

### Tests
- 新增 tests/test_start_scripts.py（2 项，锁启动脚本的变量契约）。

## 0.8.3 — 2026-09-17（root 安装的 bin 包裹脚本解析 + 仓库目录显示修正）

### Fixed
- **官方 root 安装的 bin 是 bash 包裹脚本而非软链**：`resolve()` 解不出仓库，
  反推仍落空（Ubuntu 干净容器 root 安装实测复现）。现增加两层兜底：解析包裹脚本
  `exec` 行（带仓库标记校验，防误判），以及官方固定落点 `/usr/local/lib/hermes-agent`、
  `/opt/hermes-agent`。
- **venv 解释器同样吃反推仓库**：FHS 下直连 venv 不存在，`agent_python` 会报
  "未找到 hermes venv"；现与仓库反推共用候选，扫码/依赖链路在 root 机上可达。
- **服务页"安装目录"显示修正**：改用反推结果，FHS 机器不再显示错误的家目录拼接。

### Tests
- tests/test_agent_repo.py 补到 10 项（软链/FHS 用例部分仅 POSIX）。

## 0.8.2 — 2026-09-17（Linux root 安装布局兼容 + start.sh 可执行位）

### Fixed
- **start.sh 没有可执行位**：git 记录为 100644，全新 clone 后直接运行报
  Permission denied（Ubuntu 干净容器实测复现）。已置 100755，与 scripts/*.sh 一致。
- **root/FHS 安装下源码目录找不到**：root 安装把 hermes-agent 放到
  `/usr/local/lib`（而非家目录），且 `/usr/local/bin/hermes` 多为软链；
  直连 `paths.agent_repo` 落空，连带 MCP/技能/插件目录、venv 探测、依赖安装一起失效。
  现收敛到 `paths.resolve_agent_repo()`（直连优先，否则解软链逐级上找），
  MCP/技能/插件目录与扫码/依赖链路共用；并删掉 `install_deps` 里用直连路径
  覆盖反推结果的一行。

### Tests
- 新增 tests/test_agent_repo.py（10 项；软链/FHS 用例部分仅 POSIX）。
- Ubuntu 24.04 干净容器端到端验证：一键安装 exit 0（约 3.5 分钟），检测链
  `installed=True`，`hermes update --check` 只读验证通过。

## 0.8.1 — 2026-09-17（Windows 一键安装走原生通道 + 全新机默认家目录修正）

### Fixed
- **Windows 一键安装曾跑 Linux 安装脚本**：任何平台都执行 `curl | bash` 装 `install.sh`
  （官方头注仅支持 Linux/macOS/Termux）；Windows 上 `bash` 解析到 WSL 存根，
  装不出原生版，且任务台账从无成功记录。现按平台选择安装器：Windows → 官方
  `install.ps1`（带 `-SkipSetup -NonInteractive`，后台无 TTY 不会挂死），其余平台不变；
  安装前网络预检、页面复制框、体检探测同步使用同源 URL。
- **全新 Windows 机默认家目录错位**：默认 `~/.hermes`，而官方安装器落到
  `%LOCALAPPDATA%\hermes`，配置读写会进错目录。现默认与官方落点一致
  （DB/环境变量显式配置仍优先）；检测新增 `%LOCALAPPDATA%\hermes\bin\hermes.exe`
  绝对路径兜底——安装后用户 PATH 未刷新也不用重启控制台。
- **安装失败无提示**：`/service` 模板从未渲染 `error` 变量，预检拒绝白白返回 400。
  现页面顶部显示错误横幅；安装方式与安装目录改为平台相关变量，不再写死。
- **运行体检**：新增 Git 检查（一键安装需 Git 下拉仓库）；Windows 探测清单改为
  PS 安装器域名 + GitHub + astral + PyPI；成功提示不再写死条数。
- **安装/更新任务改用 jobs_dir 为工作目录**：此前继承控制台进程目录（即仓库根），
  实测出现 PowerShell 模块缓存（`Microsoft/`）落到仓库里。官方安装器全用绝对路径，
  换目录不影响安装结果。

### Notes
- `hermes update`（官方 CLI 子命令，`--check/--plan` 可只读试运行）与扫码/依赖链路未动。
- 已知后续：个别记忆方案的后台安装命令仍是 POSIX 写法（本机曾失败 exit 2），排期下一批。

### Tests
- 新增 tests/test_install_platform.py（7 项）；全量回归除 `test_backup_keeps_last_five`
  （Windows 文件锁旧疾，主分支同样失败，与本期无关）外全绿。
- 另在隔离沙盒（独立控制台数据目录 + 独立家目录）触发真实一键安装：exit 0，
  安装目录/venv/CLI 俱全；冷缓存 `uv sync` 与 `check_live` 全页自检同样通过。

## 0.8.0 — 2026-09-17（扩展生态：Skill 管理 + MCP 服务管理 + 插件与 Hook）

### Added
- **MCP 服务管理（`/mcp`）**：`mcp_servers.*` 的 CRUD 与启停——stdio（command/args/env）
  与远程 HTTP/SSE（url/headers/OAuth 2.1）两类，信任分级（untrusted = 写操作走审批）、
  超时可调；**密钥只写 `.env`（600 权限），配置中以 `${VAR}` 占位符引用**（官方
  secret-scope 语义，连接期解析）；编辑时自动保留存量密钥引用；删除时联动清理该服务
  写入 `.env` 的密钥变量；`agentmemory` 打「记忆系统托管」标签并拒绝在本页删除。
  **官方热门目录一键添加**：解析本机 `agent_repo/optional-mcps/*/manifest.yaml`
  （64 个 Nous 审核条目），热门条目徽标置顶，安装结果与官方 `hermes mcp install` 等效
  （不落多余键，安装提示透传）。
- **Skill 管理（`/skills`）**：`~/.hermes/skills/` 目录可视化——解析 SKILL.md
  frontmatter（name/description/version/tags），来源标签（工程规范托管 / 官方目录 /
  自定义）、缺 SKILL.md 的无效目录标记；启停写入 `skills.disabled`
  （`hermes-agent` 为官方 ESSENTIAL_SKILLS 拒绝禁用）；删除需输确认词且保护工程托管技能；
  **官方热门技能库**：解析 `agent_repo/optional-skills/`（24 类）按类目折叠展示，
  一键安装 = copytree（与 `hermes skills install` 等效，防路径穿越与重名）；
  **skills 配置域表单**：external_dirs（兼容 list 与 JSON 字符串两种存量写法）、
  project_discovery / template_vars / inline_shell(+timeout) / guard_agent_created
  （默认值不落键）、trusted_project_dirs 展示与取消信任（等效 `hermes skills untrust`）。
- **插件与 Hook 管理（`/plugins`）**：
  - *插件*：扫 `~/.hermes/plugins/*/plugin.yaml`，启停即增删 `plugins.enabled`
    白名单（官方信任模型：默认禁用）；白名单残留（目录已删）识别与一键清理；
    `plugins.hook_callback_timeout` 调优；官方策展目录 `plugin-catalog/*.yaml`
    展示（tier/capabilities/需注入的 env）；安装走官方 CLI
    （`hermes plugins install`，sha pin 与黑名单校验全部交给官方实现），
    复用后台任务台账（新 kind：安装插件）。
  - *Shell hooks*：`hooks.<event>[]` 的 CRUD，校验对齐官方——事件须在 VALID_HOOKS
    （37 个全集随源码内置）、matcher 仅 pre/post_tool_call 且须为合法正则、
    fail_closed 仅 pre_tool_call、timeout 1-300；同 (event, command) 覆盖即编辑；
    `hooks_auto_accept` 开关；信任白名单（shell-hooks-allowlist.json）展示与
    单条撤销（等效 `hermes hooks revoke`）。
  - *只读盘点*：gateway hooks 目录（HOOK.yaml 的 name/events）、outbound webhooks。
- **调研沉淀**：[docs/PLUGINS_AND_HOOKS.md](docs/PLUGINS_AND_HOOKS.md)——Hermes 四套
  Hook 体系（shell hooks / plugin hooks / gateway hooks / outbound webhooks）与插件
  信任模型的完整结论，即本次「插件机制调研」的交付物。

### Changed
- 侧栏新增「扩展生态」分组（技能管理 / MCP 服务 / 插件与 Hook），新增三个内联 SVG 图标；
- `docs/CONFIG_CATALOG.md` 与「配置项全景」页：`mcp_servers`、`skills` 移入「已集成」，
  新增 `plugins.enabled`、`hooks` 两域条目（已集成 6 → 11）；`docs/ARCHITECTURE.md`
  扩展点同步勾掉 MCP；README 功能总览、目录结构、写入契约与测试计数更新；
- `check_live.py` 增加三个新页面的探针。

### Notes
- 写入纪律不变：全部走 `config_store`（文件锁 → 备份 → 原子替换，注释保留），
  默认值不落键，真实状态一律以 config.yaml / 文件系统为准（控制台不说谎）；
- 本期不做：技能/插件远端 marketplace 搜索、per-platform 技能禁用
  （`skills.platform_disabled`）、outbound webhook 编辑、gateway hooks 的
  Python handler 编辑。

## 0.7.1 — 2026-09-17（对外面收敛：一份首页 + 真实截图）

### Added
- **README 配图**：`scripts/make_screenshots.py` 拉起一个**隔离演示实例**（独立
   `HERMES_CONSOLE_DATA` / `HERMES_HOME`，`HERMES_BIN` 指向不可执行的空桩）后用 Playwright 截图，
   配方可重现（`docs/SCREENSHOTS.md`）；入库的是 `docs/assets/*.webp`（单张 <200 KB），
   原图落 `shots/`（已 gitignore）。截图不读也不写机器上真实的 `~/.hermes`，也不会调用真实 hermes CLI。
- **文档分层**：新增 `docs/dev/`（工程内部文档）与 `docs/SCREENSHOTS.md`；
   `docs/` 目录清单补齐了两份之前没列进 README 的规格（渠道接入助手、文件管理器）。

### Changed
- **对外只留一份首页 = README**。文档正文在 GitHub 直接渲染阅读，不再自建文档站。
- **`docs/SECURITY.md` 重写为“给部署者的安全说明”**：保留已内置防护清单 + 首次部署加固清单，
   新增「发现漏洞怎么办」（走 GitHub 私有漏洞报告，不开公开 Issue）；
   原来的「资产与威胁 / 控制矩阵 / 已知边界」迁入 `docs/dev/THREAT_MODEL.md`，
   并把「已知边界」改写成中性的「部署边界」（剔除 CSP 指令细节与缺口清单式表述，
   排期信息归 `CONFIG_CATALOG.md`），另补一节「对外表述纪律」（首页/UI/仓库分别能写什么）。

### Removed
- **`site/` 单页与 `.github/workflows/deploy-site.yml` 已删除**（GitHub Pages 未启用，
   且 `rexai.top` 已不再指向本项目 —— 该域名与相关 DNS/部署步骤全部从文档中移除，不要再引用）。
   原首页把 `docs/SECURITY.md` 的威胁矩阵与 `docs/CONFIG_CATALOG.md` 的**未实现功能排期**
   直链给任意访客，相当于递出攻击面清单；这是本次收敛的直接动因。

## 0.7.0 — 2026-09-17（扫码修复 + 全渠道接入引导）

### Added
- **扫码回填小白默认两件**：① 自动设 `platforms.weixin.home_channel` 为号主私聊，
  消除反复出现的「📬 No home channel is set… Type /sethome」催促；② 自动把
  `display.busy_input_mode/busy_text_mode` 设为 queue，忙时发新消息不再打断当前
  任务（消除莫名其妙的「↪ Redirected current run」）。
- **Windows 配置保存重试**：`os.replace` 目标被 Gateway 监控句柄/杀软短暂占用时
  抛 WinError 5，三处原子写入统一退避重试，偶发锁不再变成「保存失败」。
- **扫码回填后自动重启 Gateway**：每次扫码都会作废上一个 iLink 会话，运行中的
  Gateway 若持旧 token 会 `Session expired` 静默丢消息；现在面板检测到新账号回填
  且 Gateway 在运行时自动重启，并在面板提示重启结果。
- **任务台账（操作记录）**：「安装与更新」页新增「最近任务」卡片——后台任务（安装/更新/扫码接入等）
  不再跑完就消失；历史列表带中文标签、状态、起止时间，点「查看输出」展开任意任务的完整日志。

### Fixed
- **网关无法启动 / 状态永远「未知」**（用户反馈，实机三连环）：
  1. `supervisor._child_env()` 用 POSIX keep 白名单过滤环境变量，Windows 子进程缺
     SYSTEMROOT/USERPROFILE → hermes CLI 直接 `RuntimeError: Could not determine home
     directory`。现改为继承完整环境、仅剔除 `HERMES_CONSOLE_*` 敏感键；
  2. `_path_with_common_bins()` 用 `:` 拼 PATH（Windows 是 `;`）→ 子进程找不到 node；
     另补 Windows 常见 npm 全局目录；
  3. CLI 输出按本地 GBK 解码崩溃（中文 Windows）→ 固定 `encoding=utf-8`；
     `signal.kill` 在 Windows 不存在 → `_pid_alive` 改 OpenProcess 查询（绝不可用
     os.kill(pid,0)，Windows 上会直接杀掉目标进程）；
  4. 真正的启动拦路虎：扫码回填后 weixin `dm_policy: open` 且无白名单，hermes 安全
     护栏拒绝启动（Refusing to start）——**现在扫码成功即自动把号主写入
     WEIXIN_ALLOWED_USERS 并把 dm_policy 收敛为 allowlist**（幂等、不覆盖已有名单）；
     护栏拦截时页面给出「【控制台解读】」人话提示，不再静默显示未知；
  5. 状态关键词补充 `no gateway process detected` 等实机输出，不再误判未知。
- **有任务运行时 `/service` 页 500 无法使用**（用户反馈）：整页上下文只传 `active_job`，
  而内嵌的 `_job_panel.html` 读的是 `job`/`job_lines`/`done`——扫码任务真能跑满几分钟
  之后这个潜伏 bug 必现（此前任务秒挂所以从未触发）。整页与 HTMX 片段改用同一个
  上下文构造函数；无任务时面板返空壳不再抛 UndefinedError。
- **孤儿任务永久卡 running**：服务被强杀时正在跑的 job 行停在 running，既堵死后续
  `submit`（「已有任务在执行中」）又让 /service 反复轮询旧面板。现在启动时
  `reap_orphan_jobs()` 自动回收（标 failed/-9，日志追加中断说明）。
- **扫码任务在 Windows 上必挂、二维码永远不显示**（用户反馈：「任务已结束但未产生二维码」）：
  `installer.submit` 把子进程环境整体替换为硬编码 POSIX PATH，导致 hermes venv 的 python
  无法初始化 Winsock（WinError 10106），扫码驱动 `import asyncio` 即崩溃。现改为继承完整环境，
  同时修复安装/更新/依赖类任务在 Windows 下的同类问题；真机验证驱动已能稳定输出二维码。
- **接入助手错误不再吞日志**：失败时面板直接展示驱动真实报错（log_tail，EVENT 机器行过滤）
  + 一键反馈链接，小白无需翻 `data/jobs/*.log`。

### Added
- **小白一键启动**：`start.bat`（Windows 双击即用）/ `start.sh`（macOS · Linux）——
  自动检测并安装 uv、自动 `uv sync`、启动后 3 秒自动打开浏览器，关窗即停；
  README 快速开始改为「双击优先」，原命令行步骤折叠为开发者选项。
- **全渠道「接入引导」步骤卡**（小程序化理解成本目标）：`PlatformDef.guide_steps` 声明化，
  飞书/Telegram/Discord/Slack/QQ/企业微信/钉钉/Email/WhatsApp/Signal/Matrix/Webhook/API Server
  逐渠道内嵌分步指引，每步能直达的就给可点击链接（如 @BotFather、飞书开放平台、
  Discord Developer Portal），官方文档降为「补充阅读」；单测断言除 weixin（走接入助手）外
  所有渠道必须有带链接的引导。

## 0.6.0 — 2026-09-16（更名 + 图标体系）

### Changed
- **文件工作台 → 文件管理器**（导航、页面标题、顶栏同步更名；路由 /files 不变）。
- **弃用 emoji 图标**（跨系统渲染不一致）：新增内联 SVG 彩色文件类型图标宏 `wbicon`
  （folder/image/video/audio/document/code/archive/file/home/clock/drive/flask/archivebox，
  造型参考 Win11 Fluent，零依赖零构建）；侧栏、网格、列表、详细信息面板、
  导航箭头（chevron/arrow/rotate/sort/grid 线框图标）全部 SVG 化。

## 0.5.0 — 2026-09-16（文件工作台 OS 风格重构）

### Changed
- **文件工作台按 Finder/Explorer 交互重做**（用户反馈：不接受表格型页面）：
  - 侧栏 = 位置（工作区根 + 7 个标准目录含文件数）+ 智能集合
    （最近使用 / 图片 / 视频 / 音频 / 文档 / 代码 / 压缩包，全工作区按扩展名聚合，虚拟视图不复制文件）；
  - 主区 = 图标网格（图片真缩略图、视频首帧 `<video preload=metadata>`、类型 emoji 字形 + 扩展名徽标、
    目录子项计数）⇄ 详细列表一键切换；工具条含面包屑、筛选框（前端即时过滤）、排序（名称/时间/大小）；
  - 交互 = 单击选中（选中操作条：打开/下载/打包/归档）、双击打开（目录进入/文件右侧 Quick Look 预览）、
    右键上下文菜单、Esc 取消、Enter 打开、Backspace 上一级、选完文件即上传；
  - 导航改 hx-boost 整页（URL 即状态，浏览器前进/后退原生可用）。
- 工作区目录体系扩展：`documents/ pictures/ videos/` 成为标准目录（init 幂等补齐，
  散乱文件巡检白名单同步）；上传仍固定落 downloads/。
- 预览面板支持视频播放与音频播放（原生 `<video>/<audio>`，零依赖）。

### Added
- workspace_service：CATEGORIES 类型体系、list_collection / list_recent /
  category_counts / location_counts、Entry.count（目录子项数）、排序参数。

### Removed
- 旧三栏表格 `files/_list.html`。

### Tests
- 新增智能集合/布局/排序/目录计数 2 项；106 passed, 3 skipped。

## 0.4.0 — 2026-09-16（渠道接入助手 · weixin 浏览器内扫码）

### Added
- **接入助手（onboarding）**：微信渠道配置不再要求用户读文档、开终端。
  - 依赖检测 + 一键安装（`uv/pip install -e ".[messaging]"` 后台任务，日志实时进面板）；
  - 二维码直接渲染在网页里（vendored qrcode.min.js，CSP 不破），扫码状态 2s 轮询，
    过期自动刷新（≤3 次），全程零终端；
  - 登录成功自动回填 `platforms.weixin.extra.account_id` 并启用渠道，
    凭据由 hermes 自身写入 `~/.hermes/weixin/accounts/`（复用 `gateway.platforms.weixin`）。
- 新端点：`GET /channels/{name}/onboard`（HTMX 片段）、`POST qr-start`、`POST deps-install`
 （后两者 admin only）；审计事件 channel_qr_start / channel_deps_install / channel_onboard_backfill。
- 设计文档 `docs/CHANNEL_ONBOARDING_SPEC.md`：五原则（零终端/QR进浏览器/自动回填/
  引导代替甩链接/复用hermes本体）+ M2 计划（白名单免查ID、表单渠道分步指引）。

### Changed
- weixin 详情页：接入助手置顶，隐藏"填凭证三步"引导条；`account_id` 不再必填，
  帮助文案改为"扫码后自动回填"。

### Tests
- 新增 tests/test_onboarding.py（7 项）：EVENT 解析、confirmed 自动回填、
  非 weixin 404、登录门槛、operator 403。共 104 passed, 3 skipped。

## 0.3.1 — 2026-09-16（按钮样式修复）

### Fixed
- **链接型主按钮白底白字（隐形）**：基态选择器 `a.btn`（特异性 0,1,1）压过
  `.btn-primary` 等变体（0,1,0），叠加 `:visited` 后文字变白 —— 「前往供应商」
  「添加第一个供应商」等按钮渲染成空白块，hover 才短暂正常。基态改回 `.btn`。
- **空状态按钮图标被撑成 40px**：`.empty-state .icon` 后代选择器泄漏到按钮内，
  改为 `.empty-state > .icon` 只作用于容器直属装饰图标。
- 新增回归测试 `test_css_button_specificity_guard` 防止两条规则回退。

## 0.3.0 — 2026-09-17（W3：文件工作台体验版）

### Added
- **文件工作台**（`/files`，对应 FILE_WORKBENCH_SPEC M1+M3 骨架）：三栏式（目录树 / 列表 / 预览），
  浏览、内联预览（md/代码/图片/pdf）、单文件下载、目录打包 zip、上传（固定落 downloads/，
  同名自动加后缀永不覆盖）、一键归档（mv → archive/）。
- `app/hermes/workspace_service.py`：jail 化文件域服务 —— 根目录永远读 `EngSettings.workspace`
  （与工程注入层同源，无第二配置）；所有路径 resolve 后校验在根内，symlink 逃逸同样拦截。
- S2 防劫持：`validate_root` 拒绝过浅路径与 home 本身；`save_settings`/`init_workspace` 保存前强制校验。
- 归位巡检摘要（页头徽章）：根部散乱文件 / scratch 超期 / 项目缺 requirements.md（只读提示，执法 M2 再补）。
- 安全（S4/S5）：读=登录、写=管理员+CSRF；越狱尝试落 `files_denied` 审计；浏览/预览/下载/打包/上传/归档全量审计。
- 设置项 `workbench_upload_max_mb`（默认 50）。
- 新测试 9 项（jail、登录门槛、角色拦截、上传不覆盖、归档、zip、审计）；全套 99 项绿。

## 0.2.0 — 2026-09-16（W1 + W2）

### Fixed
- **Windows 可运行**：`config_store` 的 fcntl 硬依赖改为跨平台文件锁
  （`app/core/filelock.py`：POSIX flock / Windows msvcrt / 降级原子替换）；CI 增加 windows-latest。
- 时间基准统一 UTC（DB schema 默认值、sessions、ratelimit、job_runs、provider_meta），
  审计页列头标注 UTC；消除时区/夏令时导致的会话过期边界错误。
- 登录会话固定（session fixation）：登录成功后吊销旧 session id，另发新 id。

### Security
- TOTP 种子静态加密落库（`enc:v1:`，HMAC-CTR + encrypt-then-MAC，密钥派生自
  `HERMES_CONSOLE_SECRET`）；未设置固定密钥时降级明文并在启动日志/设置页显著警告。
- 用户读取统一走 `appsettings`（Row→dict + 透明解密），`deps.current_user` 同步收敛。

### Added
- `/healthz` 免认证探针；`/static`、`/healthz` 不再创建匿名会话行（防 sessions 注水）。
- DB 每日快照：`app/core/backup.py`（stdlib sqlite3 backup API，保留 5 份）+ 设置页列表与手动备份。
- 周期维护循环：`app/core/maintenance.py`（15 分钟：过期会话/limiter 修剪/到期快照）。
- 服务器端紧急工具 `scripts/console_admin.py`（重置密码/解 2FA/列会话/吊销/首建管理员）。

### Tests
- 新增 6 项（静态加密、UTC 基准、healthz 豁免、快照轮转、会话轮换）；
  POSIX-only 桩测加 skipif。Windows 本机 88 passed / 3 skipped。

## 0.1.0 — initial release
