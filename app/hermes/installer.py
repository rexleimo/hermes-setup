"""安装 / 更新任务执行器。

长时间命令（官方安装脚本、hermes update）在后台线程运行，输出落盘到
data/jobs/<id>.log；前端轮询 `/service/job` 片段展示进度。同一时刻只允许
一个任务在跑。
"""
from __future__ import annotations

import subprocess
import threading
from datetime import datetime

from app.core import db
from app.core.settings import settings

_lock = threading.Lock()
INSTALL_CMD = (
    "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"
    " | bash -s -- --skip-setup"
)
UPDATE_CMD = "hermes update"


class JobBusy(Exception):
    pass


def active_job() -> dict | None:
    row = db.query_one("SELECT * FROM job_runs WHERE status = 'running' ORDER BY id DESC LIMIT 1")
    return dict(row) if row else None


def last_job() -> dict | None:
    row = db.query_one("SELECT * FROM job_runs ORDER BY id DESC LIMIT 1")
    return dict(row) if row else None


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


def submit(kind: str, command: str, *, shell: bool = True) -> int:
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
        env = {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
               "HOME": __import__("os").path.expanduser("~")}
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                proc = subprocess.run(
                    command, shell=shell, stdout=fh, stderr=subprocess.STDOUT,
                    env=env, timeout=1800,
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
            "UPDATE job_runs SET status = ?, exit_code = ?, finished_at = datetime('now','localtime') "
            "WHERE id = ?",
            ("ok" if code == 0 else "failed", code, job_id),
        )

    threading.Thread(target=_worker, name=f"job-{job_id}", daemon=True).start()
    return job_id
