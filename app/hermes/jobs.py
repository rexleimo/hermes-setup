"""通用后台任务引擎：submit / 实时输出 / 取消 / 历史 / 日志 / 收尾副作用。

从 installer.py 拆出（安装域逻辑仍在那里）：任何模块都可以通过 submit()
跑一个「后台任务」（安装、Gateway 动作、插件安装、浏览器组件……），输出
实时进终端 + 任务面板 + 日志文件，可取消、可回查，同一时刻只允许一个。
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
_procs: dict[int, subprocess.Popen] = {}


def write_job_script(name: str, content: str, *, args: tuple[str, ...] = ()) -> str:
    """驱动脚本统一落盘 jobs 目录，返回跨平台可执行命令（Python 解释器）。

    各安装/动作域只需给「脚本名 + 内容 + 参数」，不再各自拼
    `shlex/引号/路径`（此前四处各写一遍，Windows 引号问题踩过坑）。"""
    script = settings.jobs_dir / name
    script.write_text(content, encoding="utf-8")
    joined = " ".join(f'"{a}"' for a in args)
    return f'"{sys.executable}" "{script}"' + (f" {joined}" if joined else "")


class JobBusy(Exception):
    pass




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
    "browser_install": "补装浏览器组件",
    "gateway_start": "启动 Gateway",
    "gateway_stop": "停止 Gateway",
    "gateway_restart": "重启 Gateway",
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


def job_log_delta(job_id: int, offset: int) -> tuple[list[str], int]:
    """从字节偏移增量读取任务日志（SSE 推送用）：返回 (新增完整行, 新偏移)。
    只交付以换行结束的完整行；未完结的半行留给下一次，避免前端重复/半截。"""
    path = settings.jobs_dir / f"job-{job_id}.log"
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            if size < offset:
                offset = 0
            if size == offset:
                return [], offset
            fh.seek(offset)
            chunk = fh.read()
    except OSError:
        return [], offset
    nl = chunk.rfind(b"\n")
    if nl == -1:
        return [], offset
    lines = chunk[:nl].decode("utf-8", "replace").splitlines()
    return lines, offset + nl + 1


def _kill_tree(proc: subprocess.Popen) -> None:
    """终止整个进程树：POSIX 用独立进程组 + killpg（sh → curl/bash 子孙一起走）；
    Windows 用 taskkill /T /F。"""
    if os.name == "posix":
        import signal as _signal

        try:
            os.killpg(os.getpgid(proc.pid), _signal.SIGTERM)
        except OSError:
            return
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), _signal.SIGKILL)
            except OSError:
                pass
    else:
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()


def cancel(job_id: int) -> str:
    """取消运行中的任务（连同它的子进程）。返回面向用户的中文结果。"""
    row = db.query_one("SELECT status FROM job_runs WHERE id = ?", (job_id,))
    if row is None:
        return "任务不存在"
    if row["status"] != "running":
        return "任务已结束，无需取消"
    with _lock:
        proc = _procs.get(job_id)
    if proc is None or proc.poll() is not None:
        # 进程已退出但状态行还没收尾（竞态）：直接把状态纠正掉
        db.execute(
            "UPDATE job_runs SET status = 'failed', exit_code = -15, "
            "finished_at = datetime('now') WHERE id = ? AND status = 'running'",
            (job_id,))
        return "任务已停止"
    try:
        with open(settings.jobs_dir / f"job-{job_id}.log", "a", encoding="utf-8") as fh:
            fh.write("\n[console] 任务已被手动取消\n")
    except OSError:
        pass
    _kill_tree(proc)
    return "已取消任务"


def _chain_after(kind: str, ok: bool) -> None:
    """安装成功后自动接力浏览器组件任务（Agent 核心能力，默认必装）。

    拆两步的目的不是"跳过"，而是：浏览器下载（cdn.playwright.dev，约 270MB）
    在国内常卡死——主安装先快速收口（可取消），浏览器组件随后自动接力，
    官方源优先、自动切镜像、可单独重试；失败了主安装也不受影响。"""
    if not ok or kind != "install" or sys.platform == "win32":
        return
    from app.hermes import installer   # 延迟导入（installer 重导出本模块）
    if installer.browser_installed():
        return
    try:
        command = installer.browser_install_job()
    except OSError:
        return
    if not command:
        return
    try:
        installer.submit("browser_install", command)
    except JobBusy:
        pass


def _echo_console(text: str) -> None:
    """把任务输出同时打到控制台终端（和 uvicorn 日志并排），终端编码不支持时静默跳过。"""
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def _pump_output(stream, fh) -> None:
    """读子进程输出：写日志文件 + 实时回显到终端（tee 语义）。"""
    try:
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", "replace")
            try:
                fh.write(text)
                fh.flush()
            except OSError:
                pass
            _echo_console(text)
    finally:
        try:
            stream.close()
        except OSError:
            pass


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

    # 同步写"已启动 + 命令"：POST 返回的首屏（任务面板/最近任务）就能看到，
    # 绝不出现空白期（此前 curl 下载脚本的几十秒全静默，像"点了没反应"）。
    started = datetime.now().strftime("%H:%M:%S")
    header = (f"[console] 任务已启动（{started}）\n"
              f"[console] $ {command}\n"
              "[console] —— 以下为实时输出（首段下载 / 环境检查可能需要一两分钟）——\n")
    try:
        log_path.write_text(header, encoding="utf-8")
        _echo_console(header)
    except OSError:
        pass

    def _worker() -> None:
        # 继承控制台进程的完整环境（PATH/HOME/SYSTEMROOT 等）。
        # 不能整体替换成硬编码 POSIX PATH：Windows 下会导致子进程 python
        # 找不到 System32，Winsock 初始化失败（WinError 10106），
        # 扫码驱动在 import asyncio 时即崩溃、二维码永远出不来。
        env = dict(os.environ)
        env.setdefault("HOME", os.path.expanduser("~"))
        # Windows 子进程 stdout 默认走控制台代码页（GBK）：任务脚本里打一个
        # “✓/—”就 UnicodeEncodeError 崩掉整个任务（实机踩过的 gateway restart）。
        # 强制 Python 子进程用 UTF-8 输出，父端 _pump_output 按 UTF-8 解码。
        env.setdefault("PYTHONIOENCODING", "utf-8")
        code = -2
        fh = None
        try:
            fh = open(log_path, "a", encoding="utf-8")
            kwargs: dict = {}
            if os.name == "posix":
                # 独立进程组：取消 / 超时时能整树终止（sh → curl/bash 子孙进程）
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(
                command, shell=shell, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # stdin 断开：后台任务绝不继承终端的 TTY。否则 hermes CLI 用
                # sys.stdin.isatty() 判定"有人在交互"，弹出 Y/n 提问等回车——
                # 后台无人应答，卡满超时（实机踩过：gateway install 的
                # "Start the gateway now? [Y/n]"）。断开后自动走非交互默认值。
                stdin=subprocess.DEVNULL,
                env=env, cwd=cwd, **kwargs,
            )
            with _lock:
                _procs[job_id] = proc
            pump = threading.Thread(target=_pump_output,
                                    args=(proc.stdout, fh), daemon=True)
            pump.start()
            try:
                code = proc.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                fh.write("\n[console] 任务超时（30 分钟），已终止\n")
                fh.flush()
                code = -1
            pump.join(timeout=5)
        except OSError as exc:
            with open(log_path, "a", encoding="utf-8") as fh2:
                fh2.write(f"\n[console] 启动失败：{exc}\n")
            _echo_console(f"[console] 任务 {job_id} 启动失败：{exc}\n")
        finally:
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass
            with _lock:
                _procs.pop(job_id, None)
        _finish_job(job_id, kind, code)

    threading.Thread(target=_worker, name=f"job-{job_id}", daemon=True).start()
    return job_id




def _finish_job(job_id: int, kind: str, code: int) -> None:
    """任务收尾：写状态 → 链式副作用。独立成函数便于单测（不用起真线程）。"""
    db.execute(
        "UPDATE job_runs SET status = ?, exit_code = ?, finished_at = datetime('now') "
        "WHERE id = ?",
        ("ok" if code == 0 else "failed", code, job_id),
    )
    # 主安装成功 → 自动接力浏览器组件（核心能力，默认必装；详见 _chain_after）
    _chain_after(kind, code == 0)
    # agentmemory 插件装好 → 记住「重启后生效」提醒（提交 Gateway 操作任务时清除）
    if kind == "memory_agentmemory" and code == 0:
        try:
            from app.core import appsettings

            appsettings.set_setting("plugin_restart_pending", str(job_id))
        except Exception:  # 提醒失败不影响任务结果
            pass
    # Gateway 动作/安装/更新结束 → 状态探测缓存立刻失效（否则顶栏 pill 要等 TTL）
    if kind.startswith(("gateway_", "update", "install")):
        try:
            from app.hermes import supervisor

            supervisor.invalidate_status_cache()
        except Exception:
            pass
    # 官方 CLI 带 --enable 装插件；万一所装版本不认该参数，收尾兜底补白名单，
    # 不让"装完即隐身"发生（白名单里有名无目录时插件页会如实显示「目录缺失」）
    if kind == "plugin_install" and code == 0:
        try:
            import re as _re

            row = db.query_one("SELECT command FROM job_runs WHERE id = ?", (job_id,))
            m = _re.search(r"plugins install ([A-Za-z0-9._-]+)", row["command"] or "") \
                if row else None
            if m:
                from app.hermes import plugins_service as psvc

                name = m.group(1)
                if (psvc.plugins_dir() / name / "plugin.yaml").exists() \
                        and name not in psvc.enabled_names():
                    psvc.set_enabled(name, True)
        except Exception:  # 兜底失败不改变任务结果，页面仍可手动启用
            pass

