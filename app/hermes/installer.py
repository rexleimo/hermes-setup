"""安装 / 更新任务执行器。

长时间命令（官方安装脚本、hermes update）在后台线程运行，输出落盘到
data/jobs/<id>.log；前端轮询 `/service/job` 片段展示进度。同一时刻只允许
一个任务在跑。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime

from app.core import db
from app.core.settings import settings

_lock = threading.Lock()

# 平台原生的官方安装通道（service.py 的「开始安装」按钮与页面复制框共用）。
# Windows 绝不能用 `curl | bash`：bash 在 Windows 上解析到 WSL 存根（或根本没有），
# 官方 install.sh 头部也写明只支持 Linux/macOS/Termux；后台任务无 TTY，
# 必须带 -SkipSetup -NonInteractive，否则交互式安装向导会把任务挂死到超时。
INSTALL_CMD_LINUX = (
    "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"
    " | bash -s -- --skip-setup"
)
INSTALL_CMD_WINDOWS = (
    "powershell -ExecutionPolicy Bypass -NoProfile -Command "
    "\"& ([scriptblock]::Create((irm 'https://hermes-agent.nousresearch.com/install.ps1'))) -SkipSetup -NonInteractive\""
)
INSTALL_CMD = INSTALL_CMD_WINDOWS if sys.platform == "win32" else INSTALL_CMD_LINUX

INSTALL_METHOD_LABEL = (
    "官方 PowerShell 安装脚本（Windows 原生）" if sys.platform == "win32"
    else "官方脚本（curl | bash）"
)

# 安装前网络预检必须与实际安装源同域，否则出现"预检通过、执行必败"。
INSTALL_PREFLIGHT_URL = (
    "https://hermes-agent.nousresearch.com/install.ps1" if sys.platform == "win32"
    else "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"
)
UPDATE_CMD = "hermes update"


class JobBusy(Exception):
    pass


def network_reachable(url: str = "https://raw.githubusercontent.com",
                      timeout: float = 5.0) -> bool:
    """安装/更新前的网络预检：官方脚本与更新包都从 GitHub 下载，
    国内网络直连常失败——提前 5 秒探测，避免小白盯着必败的任务跑几分钟。"""
    import urllib.request

    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "hermes-console"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


NETWORK_HINT = (
    "网络无法访问 GitHub（安装/更新都要从 GitHub 下载）。"
    "请开代理或换网络后重试；诊断详情见「运行体检」页。")


def active_job() -> dict | None:
    row = db.query_one("SELECT * FROM job_runs WHERE status = 'running' ORDER BY id DESC LIMIT 1")
    return dict(row) if row else None


def reap_orphan_jobs() -> int:
    """服务启动时回收孤儿任务：上次进程被杀时正在跑的任务会永久停在 running，
    既堵死 submit（，已有任务在执行中），又让 /service 页反复轮询旧面板。
    在进程启动、线程必然不存在时调用，把它们标为失败。"""
    rows = db.query("SELECT id FROM job_runs WHERE status = 'running'")
    for r in rows:
        db.execute(
            "UPDATE job_runs SET status = 'failed', exit_code = -9, "
            "finished_at = datetime('now') WHERE id = ?", (r["id"],))
        path = settings.jobs_dir / f"job-{r['id']}.log"
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n[console] 服务重启，任务已中断\n")
        except OSError:
            pass
    return len(rows)


def last_job() -> dict | None:
    row = db.query_one("SELECT * FROM job_runs ORDER BY id DESC LIMIT 1")
    return dict(row) if row else None


# kind → 小白能看懂的中文标签（新增后台任务时在这里补一行）
JOB_KIND_LABELS = {
    "install": "安装 Hermes",
    "update": "检查并更新",
    "install_messaging": "安装微信依赖",
    "weixin_qr_login": "微信扫码接入",
    "plugin_install": "安装插件",
}


def job_history(limit: int = 15) -> list[dict]:
    """最近后台任务（含已结束）：此前任务一结束面板就消失，无任何历史可回溯。"""
    rows = db.query(
        "SELECT id, kind, command, status, started_at, finished_at, exit_code "
        "FROM job_runs ORDER BY id DESC LIMIT ?", (int(limit),))
    out = []
    for r in rows:
        d = dict(r)
        d["label"] = JOB_KIND_LABELS.get(d["kind"], d["kind"])
        out.append(d)
    return out


def job_log(job_id: int, tail: int = 60) -> list[str]:
    row = db.query_one("SELECT log_path FROM job_runs WHERE id = ?", (job_id,))
    if row is None:
        return []
    path = settings.jobs_dir / f"job-{job_id}.log"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-tail:]
    except OSError:
        return []


def submit(kind: str, command: str, *, shell: bool = True, cwd: str | None = None) -> int:
    """提交后台任务；返回 job id。"""
    with _lock:
        if active_job():
            raise JobBusy("已有任务在执行中，请等待完成")
        job_id = db.execute(
            "INSERT INTO job_runs (kind, command, log_path) VALUES (?,?,?)",
            (kind, command, f"jobs/job-LOG.log"),
        )
        log_path = settings.jobs_dir / f"job-{job_id}.log"
        db.execute("UPDATE job_runs SET log_path = ? WHERE id = ?",
                   (str(log_path.relative_to(settings.data_dir)), job_id))

    def _worker() -> None:
        # 继承控制台进程的完整环境（PATH/HOME/SYSTEMROOT 等）。
        # 不能整体替换成硬编码 POSIX PATH：Windows 下会导致子进程 python
        # 找不到 System32，Winsock 初始化失败（WinError 10106），
        # 扫码驱动在 import asyncio 时即崩溃、二维码永远出不来。
        env = dict(os.environ)
        env.setdefault("HOME", os.path.expanduser("~"))
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                proc = subprocess.run(
                    command, shell=shell, stdout=fh, stderr=subprocess.STDOUT,
                    env=env, timeout=1800, cwd=cwd,
                )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = -1
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("\n[console] 任务超时（30 分钟）\n")
        except OSError as exc:
            code = -2
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n[console] 启动失败：{exc}\n")
        db.execute(
            "UPDATE job_runs SET status = ?, exit_code = ?, finished_at = datetime('now') "
            "WHERE id = ?",
            ("ok" if code == 0 else "failed", code, job_id),
        )

    threading.Thread(target=_worker, name=f"job-{job_id}", daemon=True).start()
    return job_id
