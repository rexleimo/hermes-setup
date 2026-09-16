"""渠道接入助手（onboarding）：把"要用户读文档+敲命令"的流程变成程序代办。

设计原则（见 CHANNEL_ONBOARDING_SPEC.md）：
  1. 二维码直接显示在浏览器里，不在终端找；
  2. 依赖缺失一键安装，不让用户敲 pip/uv；
  3. 扫码成功后 account_id/token 自动回填 config.yaml，用户零理解成本。

weixin 扫码登录复用 hermes 自己的模块（gateway.platforms.weixin），
驱动脚本跑在 hermes venv 里，通过 job_runs 日志行传递机器事件：
    EVENT {"type":"qr","url":...} / {"type":"status","status":...} /
    {"type":"success","account_id":...} / {"type":"error","msg":...}
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.core import audit
from app.core.settings import settings
from app.hermes import channels_service, installer
from app.hermes.paths import HermesPaths, detect

QR_JOB_KIND = "weixin_qr_login"
DEPS_JOB_KIND = "install_messaging"
DEPS_MODULES = ("aiohttp", "cryptography")


# ---------------------------------------------------------------------------
# hermes venv 解释器与依赖状态
# ---------------------------------------------------------------------------

def agent_python(paths: HermesPaths | None = None) -> Path | None:
    paths = paths or detect()
    repo = paths.agent_repo
    cands = [repo / "venv" / "Scripts" / "python.exe",   # Windows 标准布局
             repo / "venv" / "bin" / "python"]           # POSIX 标准布局
    if paths.bin:  # 非标准安装位：从 hermes 可执行文件反推 venv
        b = Path(paths.bin)
        cands += [b.parent / "python.exe", b.parent / "python"]
    for cand in cands:
        if cand.exists():
            return cand
    return None


def agent_repo(paths: HermesPaths | None = None) -> Path | None:
    """hermes-agent 仓库目录（安装依赖的 cwd）。"""
    paths = paths or detect()
    if paths.agent_repo.exists():
        return paths.agent_repo
    if paths.bin:
        guess = Path(paths.bin).parent.parent.parent  # venv/Scripts/hermes → repo
        if (guess / "pyproject.toml").exists() or (guess / "gateway").is_dir():
            return guess
    return None


def deps_status(paths: HermesPaths | None = None, *, use_cache: bool = True) -> dict:
    """检查 messaging 扩展依赖是否就绪（在 hermes venv 内 import 探测，60s 缓存）。"""
    import time as _t
    global _DEPS_CACHE
    if use_cache and _DEPS_CACHE and _t.monotonic() - _DEPS_CACHE[0] < 60:
        return _DEPS_CACHE[1]
    py = agent_python(paths)
    if py is None:
        return {"checked": False, "ok": False,
                "reason": "未找到 hermes venv（请先安装 Hermes Agent）"}
    code = "import importlib.util,sys;" + ";".join(
        f"sys.exit(11) if importlib.util.find_spec('{m}') is None else None"
        for m in DEPS_MODULES)
    try:
        r = subprocess.run([str(py), "-c", code], capture_output=True, timeout=20)
        if r.returncode == 0:
            out = {"checked": True, "ok": True, "missing": []}
        else:
            out = {"checked": True, "ok": False, "missing": list(DEPS_MODULES)}
    except (OSError, subprocess.TimeoutExpired) as exc:
        out = {"checked": False, "ok": False, "reason": f"探测失败：{exc}"}
    if out["checked"]:
        _DEPS_CACHE = (_t.monotonic(), out)
    return out


_DEPS_CACHE: tuple | None = None


def invalidate_deps_cache() -> None:
    global _DEPS_CACHE
    _DEPS_CACHE = None


def install_deps(paths: HermesPaths | None = None) -> int:
    """一键安装 `.[messaging]`（uv 优先，退回 pip），后台 job + 日志流。"""
    paths = paths or detect()
    py = agent_python(paths)
    repo = agent_repo(paths)
    if py is None or repo is None:
        raise RuntimeError("未找到 hermes venv / hermes-agent 仓库，无法安装依赖")
    import shutil
    uv = shutil.which("uv")
    repo = paths.agent_repo
    if uv:
        cmd = f'"{uv}" pip install --python "{py}" -e ".[messaging]"'
    else:
        cmd = f'"{py}" -m pip install -e ".[messaging]"'
    job_id = installer.submit(DEPS_JOB_KIND, cmd, cwd=str(repo))
    invalidate_deps_cache()  # 安装结束后面板重新探测（首次轮询时缓存已过期再刷）
    return job_id


# ---------------------------------------------------------------------------
# 浏览器内扫码登录（weixin / iLink）
# ---------------------------------------------------------------------------

_DRIVER = '''
"""console-managed: iLink QR login driver (reuses hermes weixin module)."""
import json, sys, time, asyncio
from pathlib import Path

def ev(d):
    print("EVENT " + json.dumps(d, ensure_ascii=False), flush=True)

try:
    from gateway.platforms import weixin as wx
except Exception as exc:
    ev({"type": "error", "msg": f"无法加载微信模块（依赖未装？）：{exc}"})
    sys.exit(1)

HERMES_HOME = sys.argv[1]
TIMEOUT = 420

async def main():
    import aiohttp
    async with aiohttp.ClientSession(trust_env=wx.gateway_trust_env(),
                                     connector=wx._make_ssl_connector()) as s:
        deadline = time.monotonic() + TIMEOUT
        refresh = 0
        while time.monotonic() < deadline and refresh <= 3:
            try:
                r = await wx._api_get(s, base_url=wx.ILINK_BASE_URL,
                                      endpoint=f"{wx.EP_GET_BOT_QR}?bot_type=3",
                                      timeout_ms=wx.QR_TIMEOUT_MS)
            except Exception as exc:
                ev({"type": "error", "msg": f"获取二维码失败：{exc}"}); return
            qv = str(r.get("qrcode") or "")
            qu = str(r.get("qrcode_img_content") or "")
            if not qv:
                ev({"type": "error", "msg": "iLink 未返回二维码"}); return
            ev({"type": "qr", "url": qu or qv})
            base = wx.ILINK_BASE_URL
            while time.monotonic() < deadline:
                try:
                    st = await wx._api_get(s, base_url=base,
                                           endpoint=f"{wx.EP_GET_QR_STATUS}?qrcode={qv}",
                                           timeout_ms=wx.QR_TIMEOUT_MS)
                except Exception:
                    await asyncio.sleep(2); continue
                status = str(st.get("status") or "wait")
                if status == "scaned":
                    ev({"type": "status", "status": "scaned"})
                elif status == "scaned_but_redirect":
                    rh = str(st.get("redirect_host") or "")
                    if rh:
                        base = f"https://{rh}"
                elif status == "expired":
                    ev({"type": "status", "status": "expired"})
                    refresh += 1
                    break
                elif status == "confirmed":
                    aid = str(st.get("ilink_bot_id") or "")
                    tok = str(st.get("bot_token") or "")
                    bu = str(st.get("baseurl") or wx.ILINK_BASE_URL)
                    uid = str(st.get("ilink_user_id") or "")
                    if not (aid and tok):
                        ev({"type": "error", "msg": "登录成功但凭据不完整"}); return
                    try:
                        wx.save_weixin_account(HERMES_HOME, account_id=aid,
                                               token=tok, base_url=bu, user_id=uid)
                    except Exception as exc:
                        ev({"type": "error", "msg": f"保存凭据失败：{exc}"}); return
                    ev({"type": "success", "account_id": aid})
                    return
                await asyncio.sleep(2)
        ev({"type": "error", "msg": "二维码多次过期或超时，请重新开始扫码"})

asyncio.run(main())
'''


def start_qr_login(paths: HermesPaths | None = None) -> int:
    """启动扫码登录后台任务；返回 job id。"""
    paths = paths or detect()
    py = agent_python(paths)
    repo = agent_repo(paths)
    if py is None or repo is None:
        raise RuntimeError("未找到 hermes venv")
    if not deps_status(paths)["ok"]:
        raise RuntimeError("缺少 messaging 依赖，请先点击「安装依赖」")
    driver = settings.jobs_dir / "weixin_qr_driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    cmd = f'"{py}" "{driver}" "{paths.home}"'
    return installer.submit(QR_JOB_KIND, cmd, cwd=str(repo))


def deps_job_state() -> dict | None:
    """最近一次依赖安装任务（含日志尾部，供面板展示进度）。"""
    from app.core import db
    row = db.query_one(
        "SELECT * FROM job_runs WHERE kind = ? ORDER BY id DESC LIMIT 1",
        (DEPS_JOB_KIND,))
    if row is None:
        return None
    job = dict(row)
    job["log_tail"] = installer.job_log(job["id"], tail=12)
    return job


def _latest_qr_job() -> dict | None:
    from app.core import db
    row = db.query_one(
        "SELECT * FROM job_runs WHERE kind = ? ORDER BY id DESC LIMIT 1",
        (QR_JOB_KIND,))
    return dict(row) if row else None


def qr_state() -> dict:
    """解析最近一次扫码任务 → 面板状态。"""
    job = _latest_qr_job()
    if job is None:
        return {"phase": "idle"}
    state = {"phase": "starting", "job_id": job["id"], "qr_url": None,
             "account_id": None, "error": None, "job_status": job["status"]}
    log = installer.job_log(job["id"], tail=400)
    for line in log:
        if not line.startswith("EVENT "):
            continue
        try:
            d = json.loads(line[6:])
        except ValueError:
            continue
        t = d.get("type")
        if t == "qr":
            state["qr_url"] = d.get("url")
            state["phase"] = "qr"
        elif t == "status" and d.get("status") == "scaned":
            state["phase"] = "scaned"
        elif t == "success":
            state["phase"] = "confirmed"
            state["account_id"] = d.get("account_id")
        elif t == "error":
            state["phase"] = "error"
            state["error"] = d.get("msg")
    if state["phase"] == "starting" and job["status"] != "running":
        state["phase"] = "error"
        state["error"] = state["error"] or "任务已结束但未产生二维码"
    return state


def apply_weixin_account(account_id: str, *, username: str = "system") -> list[str]:
    """扫码成功 → 自动回填 config.yaml 并启用渠道（幂等：值相同则无变更）。"""
    changes = channels_service.save_channel(
        "weixin", enabled=True, extra_values={"account_id": account_id})
    if changes:
        audit.record("channel_onboard_backfill", username=username,
                     target="weixin", detail=f"account_id={account_id}; {changes}")
    return changes


def current_account_id() -> str:
    try:
        view = channels_service.get_channel("weixin")
        return str(view.extra.get("account_id") or "")
    except channels_service.ChannelError:
        return ""
