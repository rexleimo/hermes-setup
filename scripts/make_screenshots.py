#!/usr/bin/env python3
"""生成 README 配图：隔离演示实例 + Playwright 截图。

设计约束（务必保持）：
1. **绝不接触真实环境**：演示实例用独立的 HERMES_CONSOLE_DATA 与 HERMES_HOME，
   并把 HERMES_BIN 指向一个不存在的桩路径 —— 状态探测因此不会调用机器上真实的
   hermes CLI，截图里不会出现任何真实账号、路径、供应商或密钥。
2. **数据全部合成**：供应商用官方预设 id + 明显的假密钥（`sk-demo-…`），
   渠道令牌是占位串，网关状态里的 PID 属于本脚本自己拉起的临时进程。
3. 原图落 `shots/`（gitignore），入库的是压缩后的 `docs/assets/*.webp`。

用法（需要 playwright + pillow，不必进项目依赖）：

    uv run --with playwright --with pillow python scripts/make_screenshots.py

首次运行若无浏览器内核：`playwright install chromium`（或设 PLAYWRIGHT_BROWSER 指向已有 chrome.exe）。

产物：shots/*.png（原图） + docs/assets/*.webp（README 引用）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = ".hermes-demo-instance"

# 演示实例的固定身份：脚本会先写 marker，之后只认这个 marker，绝不误删别的目录
SHOTS = [
    ("dashboard", "/", "仪表盘"),
    ("providers", "/providers", "模型供应商"),
    ("channels", "/channels", "消息渠道"),
]


def log(msg: str) -> None:
    print(f"[shots] {msg}", flush=True)


def prepare_workdir(work: Path) -> tuple[Path, Path]:
    if work.exists():
        if not (work / MARKER).exists():
            raise SystemExit(f"拒绝清理非演示目录：{work}（缺少 {MARKER}）")
        shutil.rmtree(work)
    data, home = work / "data", work / ".hermes"
    (home / "logs").mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    # 空桩文件：只让「已安装」判定成立（存在即可），_run_cli 会因无法执行而报错被吞，
    # 因此状态只由 gateway_state.json 给出 —— 绝不执行机器上真实的 hermes CLI。
    stub = work / "bin"
    stub.mkdir(parents=True, exist_ok=True)
    (stub / "hermes.exe").touch()
    (work / MARKER).write_text("safe to delete\n", encoding="utf-8")
    return data, home


def seed(home: Path, username: str, password: str) -> str:
    """写入合成配置，返回一个已登录会话 id。"""
    sys.path.insert(0, str(ROOT))
    from app.core import appsettings, db, sessions as ss
    from app.hermes import channels_service as cs
    from app.hermes import providers_service as ps
    from app.hermes.schema import PLATFORMS

    db.init_db()

    # --- 供应商：3 个官方预设 + 主模型 + 别名 + 降级链 ---
    presets = [
        ("openrouter", "anthropic/claude-sonnet-4.5",
         [("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5", 200000),
          ("openai/gpt-5.1-codex", "GPT-5.1 Codex", 272000),
          ("deepseek/deepseek-chat", "DeepSeek Chat", 64000)]),
        ("zai", "glm-4.6", [("glm-4.6", "GLM-4.6", 200000),
                            ("glm-4.5-air", "GLM-4.5-Air", 128000)]),
        ("kimi-coding", "kimi-k2-0905-preview",
         [("kimi-k2-0905-preview", "Kimi K2", 256000)]),
    ]
    for preset_id, main_model, models in presets:
        ps.create_provider(
            pid="", name="", kind="preset", preset_id=preset_id, protocol="",
            base_url="", env_key="", api_key=f"sk-demo-{preset_id}-000000",
            default_model=main_model, note="",
        )
        for mid, disp, ctx in models:
            ps.upsert_model(preset_id, mid, disp, ctx, "")
    ps.set_main_model("openrouter", "anthropic/claude-sonnet-4.5", 200000)
    ps.publish_alias("zai", "glm-4.6", "glm")
    ps.publish_alias("kimi-coding", "kimi-k2-0905-preview", "kimi-fast")
    ps.set_fallback([
        ps.ChainEntry(provider="zai", model="glm-4.6"),
        ps.ChainEntry(provider="kimi-coding", model="kimi-k2-0905-preview"),
    ])

    # --- 渠道：启用 3 个，其余保持关闭；令牌一律占位串 ---
    for name in ("feishu", "telegram", "discord"):
        d = PLATFORMS[name]
        env_values = {}
        for f in d.env_fields:
            if f.kind in ("password", "text", "textarea"):
                env_values[f.name] = f"demo-{f.name.lower()}-placeholder"
            elif f.kind == "select":
                env_values[f.name] = f.default or (f.options[0] if f.options else "")
        cs.save_channel(name, enabled=True, env_values=env_values)
    cs.set_toolset("telegram", "hermes-telegram")  # 飞书/微信不在 platform_toolsets 支持列表内

    # --- 管理员账号 + 已登录会话（截图不需要过 2FA UI）---
    uid = appsettings.create_user(username, password, role="admin")
    session = ss.create(uid, ip="127.0.0.1", user_agent="screenshot-bot")
    ss.mark_two_fa(session.id)
    return session.id


def wait_up(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def fake_gateway_state(home: Path, proc_pid: int) -> None:
    (home / "gateway_state.json").write_text(
        json.dumps({"pid": proc_pid, "state": "running",
                    "platforms": ["feishu", "telegram", "discord"],
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    log_file = home / "logs" / "gateway.log"
    log_file.write_text(
        "[demo] gateway started (synthetic data for README screenshots)\n"
        "[demo] platform feishu connected (websocket)\n"
        "[demo] platform telegram connected (long polling)\n"
        "[demo] platform discord connected (gateway)\n",
        encoding="utf-8")
    os.utime(log_file, None)


def find_browser(override: str) -> str:
    if override:
        return override
    candidates: list[Path] = []
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ms-playwright"
    if base.exists():
        for d in sorted(base.glob("chromium-*"), reverse=True):
            candidates += [d / "chrome-win64" / "chrome.exe", d / "chrome-linux" / "chrome"]
    candidates += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                   Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                   Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")]
    for c in candidates:
        if c.exists():
            return str(c)
    return ""


def capture(base: str, cookie: str, shots: Path, names: list[str]) -> None:
    from playwright.sync_api import sync_playwright

    exe = find_browser(os.environ.get("PLAYWRIGHT_BROWSER", ""))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=exe or None,
                                    args=["--hide-scrollbars"])
        ctx = browser.new_context(viewport={"width": 1680, "height": 1000},
                                  device_scale_factor=2, locale="zh-CN")
        ctx.add_cookies([{"name": "hermes_console_session", "value": cookie, "url": base}])
        page = ctx.new_page()
        for i, (name, path, _label) in enumerate(SHOTS):
            page.goto(f"{base}{path}", wait_until="networkidle")
            page.wait_for_timeout(400 if i == 0 else 250)
            page.screenshot(path=str(shots / f"{name}.png"))
            names.append(name)
            log(f"captured {name}.png")
        browser.close()


def to_webp(shots: Path, assets: Path, width: int, quality: int, only: list[str]) -> None:
    from PIL import Image

    assets.mkdir(parents=True, exist_ok=True)
    for png in sorted(shots.glob("*.png")):
        if only and png.stem not in only:
            continue
        img = Image.open(png)
        ratio = width / img.width
        img = img.resize((width, int(img.height * ratio)), Image.LANCZOS)
        out = assets / f"{png.stem}.webp"
        img.save(out, "WEBP", quality=quality, method=6)
        log(f"{out.relative_to(ROOT)}  {out.stat().st_size / 1024:.0f} KB")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8423)
    ap.add_argument("--work", default=str(Path(os.environ.get("TEMP", "/tmp")) / "hermes-demo"))
    ap.add_argument("--shots", default=str(ROOT / "shots"))
    ap.add_argument("--assets", default=str(ROOT / "docs" / "assets"))
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--quality", type=int, default=78)
    ap.add_argument("--no-webp", action="store_true")
    ap.add_argument("--only", default="dashboard,channels",
                    help="只把这些页面转成 docs/assets 的 webp（逗号分隔；传空串 = 全部）")
    ap.add_argument("--keep-work", action="store_true",
                    help="跑完保留演示实例目录（默认自清，不在机器上留合成 .env）")
    args = ap.parse_args()

    work = Path(args.work).resolve()
    data, home = prepare_workdir(work)
    username, password = "demo-admin", "DemoScreenshot2026"

    os.environ.update({
        "HERMES_CONSOLE_DATA": str(data),
        "HERMES_HOME": str(home),
        # 空桩（存在但不可执行）：界面显示「已安装」，但不会真的跑起机器上的 hermes
        "HERMES_BIN": str(work / "bin" / "hermes.exe"),
        "HERMES_CONSOLE_SECRET": "demo-instance-only-never-a-real-secret",
        "HERMES_CONSOLE_HOST": "127.0.0.1",
        "HERMES_CONSOLE_PORT": str(args.port),
    })
    cookie = seed(home, username, password)
    log("seeded synthetic providers/channels")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(args.port), "--log-level", "warning"],
        cwd=str(ROOT), env=os.environ.copy())
    names: list[str] = []
    try:
        if not wait_up(args.port):
            raise SystemExit("演示实例未能启动")
        fake_gateway_state(home, proc.pid)
        shots = Path(args.shots)
        shots.mkdir(parents=True, exist_ok=True)
        capture(f"http://127.0.0.1:{args.port}", cookie, shots, names)
        if not args.no_webp:
            to_webp(shots, Path(args.assets), args.width, args.quality,
                    [s.strip() for s in args.only.split(",") if s.strip()])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not args.keep_work and (work / MARKER).exists():
            shutil.rmtree(work, ignore_errors=True)
            log(f"removed demo workdir {work}")
    log(f"done: {', '.join(names) or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
