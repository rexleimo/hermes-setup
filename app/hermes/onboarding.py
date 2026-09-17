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
from app.hermes.paths import HermesPaths, detect, resolve_agent_repo

QR_JOB_KIND = "weixin_qr_login"
DEPS_JOB_KIND = "install_messaging"
DEPS_MODULES = ("aiohttp", "cryptography")


# ---------------------------------------------------------------------------
# hermes venv 解释器与依赖状态
# ---------------------------------------------------------------------------

def agent_python(paths: HermesPaths | None = None) -> Path | None:
    paths = paths or detect()
    repos = [paths.agent_repo]
    resolved = resolve_agent_repo(paths)
    if resolved is not None and resolved != paths.agent_repo:
        repos.append(resolved)
    cands: list[Path] = []
    for repo in repos:
        cands += [repo / "venv" / "Scripts" / "python.exe",   # Windows 标准布局
                  repo / "venv" / "bin" / "python"]           # POSIX 标准布局
    if paths.bin:  # 非标准安装位：从 hermes 可执行文件反推 venv（先解软链）
        b = Path(paths.bin).resolve()
        cands += [b.parent / "python.exe", b.parent / "python"]
    for cand in cands:
        if cand.exists():
            return cand
    return None


def agent_repo(paths: HermesPaths | None = None) -> Path | None:
    """hermes-agent 仓库目录（安装依赖的 cwd）。逻辑收敛到 paths.resolve_agent_repo，
    本函数保留作调用方兼容。"""
    paths = paths or detect()
    return resolve_agent_repo(paths)


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
                    ev({"type": "success", "account_id": aid, "user_id": uid})
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
             "account_id": None, "user_id": None, "error": None, "log_tail": [],
             "job_status": job["status"]}
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
            state["user_id"] = d.get("user_id") or state["user_id"]
        elif t == "error":
            state["phase"] = "error"
            state["error"] = d.get("msg")
    if state["phase"] == "starting" and job["status"] != "running":
        state["phase"] = "error"
        state["error"] = state["error"] or "扫码任务异常退出，未产生二维码"
    if state["phase"] == "error":
        # 小白不会去翻日志：把驱动崩溃的真实输出（traceback 尾部）直接摆进面板，
        # 避免只剩一句无法定位的兑底文案。
        state["log_tail"] = [ln for ln in log if not ln.startswith("EVENT ")][-8:]
    return state


def apply_weixin_account(account_id: str, user_id: str = "", *,
                         username: str = "system") -> list[str]:
    """扫码成功 → 自动回填 config.yaml 并启用渠道（幂等：值相同则无变更）。

    关键护栏：hermes Gateway 对 dm_policy=open 且无白名单的配置会直接拒绝启动
    （Refusing to start: ... without allow-all opt-in），小白完全无法理解也不会改。
    扫码登录既然拿到了号主自己的 ilink_user_id，就顺手：
      - 把号主写进 WEIXIN_ALLOWED_USERS（去重追加，不覆盖已有名单）；
      - 把 dm_policy 收敛为 allowlist。
    效果：网关能启动、只有号主本人能聊，零理解成本。
    """
    env_values: dict[str, str] = {}
    config_values: dict[str, str] = {}
    if user_id:
        from app.hermes.config_store import EnvStore
        current = EnvStore.load().get("WEIXIN_ALLOWED_USERS") or ""
        ids = [s.strip() for s in current.split(",") if s.strip()]
        if user_id not in ids:
            ids.append(user_id)
            env_values["WEIXIN_ALLOWED_USERS"] = ",".join(ids)
    view = channels_service.get_channel("weixin")
    if str(view.config_values.get("dm_policy") or "open") == "open":
        config_values["dm_policy"] = "allowlist"
    extra_values = {} if view.extra.get("account_id") == account_id \
        else {"account_id": account_id}

    changes = channels_service.save_channel(
        "weixin", enabled=True, env_values=env_values,
        config_values=config_values, extra_values=extra_values)
    changes += _ensure_home_channel(user_id)
    changes += _beginner_busy_mode()
    if changes:
        audit.record("channel_onboard_backfill", username=username,
                     target="weixin", detail=f"account_id={account_id}; {changes}")
    return changes


def _ensure_home_channel(user_id: str) -> list[str]:
    """微信私聊的 chat_id 就是号主本人 ID。自动设 home_channel，消除
    Gateway 每次会话都催的「📬 No home channel is set… Type /sethome」——
    小白不知道那是什么，更不该被反复提示。"""
    if not user_id:
        return []
    from app.hermes import config_store
    cfg = config_store.load_config()
    node = config_store.ensure_path(cfg, "platforms.weixin")
    home = node.get("home_channel")
    if isinstance(home, dict) and home.get("chat_id"):
        return []
    node["home_channel"] = {"platform": "weixin", "chat_id": user_id,
                            "name": "Home"}
    config_store.save_config(cfg)
    return ["home_channel 已设为号主私聊（不再提示设置家庭频道）"]


def _beginner_busy_mode() -> list[str]:
    """hermes 默认 busy 模式是 interrupt：Agent 干活时发新消息会打断/接管当前
    任务（用户看到「↪ Redirected current run」却不知道发生了什么）。小白连发
    几条消息时，排队（queue）逐条处理更可预测，也不会误杀进行中的任务。"""
    from app.hermes import config_store
    cfg = config_store.load_config()
    disp = config_store.ensure_path(cfg, "display")
    if str(disp.get("busy_input_mode") or "") == "queue":
        return []
    disp["busy_input_mode"] = "queue"
    disp["busy_text_mode"] = "queue"
    config_store.save_config(cfg)
    return ["busy 模式已设为排队（忙时新消息不再打断当前任务）"]


def current_account_id() -> str:
    try:
        view = channels_service.get_channel("weixin")
        return str(view.extra.get("account_id") or "")
    except channels_service.ChannelError:
        return ""
