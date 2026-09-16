# README 配图怎么来的（以及怎么重拍）

首页那两张图**不是**在开发机上直接截的，而是由 `scripts/make_screenshots.py` 拉起一个
**隔离演示实例**截出来的。原因很简单：控制台会如实渲染你机器上的真实路径、真实供应商、
真实渠道账号 —— 那些东西不该进一个公开仓库。

## 重拍一次

```bash
# 依赖临时装，不进项目依赖（playwright + pillow）
uv run --with playwright --with pillow python scripts/make_screenshots.py

# 想换个更干净的演示目录（会显示在仪表盘的「配置目录」里）
uv run --with playwright --with pillow python scripts/make_screenshots.py \
    --work "C:/Users/Public/HermesDemo"
```

产出：`shots/*.png`（原图，gitignore）与 `docs/assets/*.webp`（README 引用，默认只导
`dashboard` 与 `channels` 两张，用 `--only` 调整）。

## 隔离是怎么保证的

| 手段 | 作用 |
|---|---|
| `HERMES_CONSOLE_DATA` 指向临时目录 | 演示实例用自己的 SQLite，不碰仓库 `data/`，也不碰真实账号/审计 |
| `HERMES_HOME` 指向临时 `.hermes` | 合成 `config.yaml` / `.env` 落在这里，真实 `~/.hermes` 完全不被读写 |
| `HERMES_BIN` 指向一个**空桩文件** | 界面显示「已安装」，但 `_run_cli` 因无法执行而报错被吞 —— 机器上真实的 `hermes` CLI 绝不会被调用，启停更不可能 |
| 网关状态来自 `gateway_state.json` | 里面的 PID 属于脚本自己拉起的临时 uvicorn，所以「运行中」是真的、但和本机服务无关 |
| 密钥一律 `sk-demo-*` / `*-placeholder` | 图里没有任何可用凭证 |
| 演示目录带 `.hermes-demo-instance` marker | 脚本只清理自己创建的目录，缺 marker 直接拒绝删除 |

## 演示数据长什么样

- 3 个官方预设供应商（OpenRouter / 智谱 GLM / Kimi）+ 主模型 + 2 个别名 + 2 条降级备选链；
- 启用 3 个渠道（飞书 / Telegram / Discord），其余保持关闭，Telegram 带一条工具集覆盖；
- 管理员账号 `demo-admin`，会话由脚本直接写入数据库（截图不需要走 2FA 界面）。

## 改图规范

- 宽度 1280、WebP quality 78，单张控制在 **200 KB 以内**（当前两张 ~30 KB）；
- 图名与页面同名：`dashboard` / `providers` / `channels`；
- 新增配图时同步更新 README 的「界面」一节，并在 alt 文本里写清这张图证明了什么能力。
