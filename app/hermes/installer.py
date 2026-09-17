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
_procs: dict[int, subprocess.Popen] = {}

# ---------------------------------------------------------------------------
# 安装源：官方源，全量安装（不跳过任何组件）
# ---------------------------------------------------------------------------
# 官方脚本一次装齐：uv/Python/git/venv/依赖/Node/浏览器引擎/Browser Use CLI/
# camofox/语音依赖/Computer Use 驱动（best-effort）。大陆镜像源已移除（产品决策）。
# Windows 必须走 PowerShell 通道；后台任务无 TTY，需 -SkipSetup -NonInteractive。
_SOURCE_DEFS = {
    "official": {
        "label": "官方源（GitHub / Nous Research）",
        "sh": "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh",
        "ps1": "https://hermes-agent.nousresearch.com/install.ps1",
    },
}


def install_variant(source: str = "official") -> dict:
    """官方安装源在当前平台的可复制命令 / 预检 URL / 标签。

    **全量安装**：只带 --skip-setup / -SkipSetup -NonInteractive（后台任务无 TTY，
    交互式向导会挂死任务）；浏览器引擎等组件由官方脚本一次装齐，不做任何跳过。
    Windows 绝不能用 `curl | bash`：bash 在 Windows 上解析到 WSL 存根（或根本没有），
    官方 install.sh 头部也写明只支持 Linux/macOS/Termux。"""
    d = _SOURCE_DEFS[source]
    if sys.platform == "win32":
        cmd = ("powershell -ExecutionPolicy Bypass -NoProfile -Command "
               f"\"& ([scriptblock]::Create((irm '{d['ps1']}'))) -SkipSetup -NonInteractive\"")
        preflight = d["ps1"]
    else:
        cmd = f"curl -fsSL {d['sh']} | bash -s -- --skip-setup"
        preflight = d["sh"]
    return {"source": source, "label": d["label"], "cmd": cmd, "preflight": preflight}


def preferred_install() -> dict:
    """展示与提交用：官方源。不探测网络（预检拦截已移除——点了必生成任务，
    成败与原因全在任务日志里可见）。"""
    return install_variant("official")


# 展示与兼容入口
INSTALL_CMD = preferred_install()["cmd"]
INSTALL_METHOD_LABEL = "官方源（GitHub / Nous Research）· 全量安装"
INSTALL_PREFLIGHT_URL = preferred_install()["preflight"]
UPDATE_CMD = "hermes update"


def update_source_reachable() -> bool:
    """更新预检（供诊断/测试使用；任务提交已不做拦截）。"""
    return network_reachable("https://raw.githubusercontent.com")

# ---------------------------------------------------------------------------
# 浏览器组件（Playwright Chromium + Browser Use CLI）：Agent 核心能力，默认必装
# ---------------------------------------------------------------------------
# 主安装用 --skip-browser 跳过官方安装器里的浏览器段（cdn.playwright.dev 下载
# 约 270MB，国内时通时断、常卡死），改由本脚本作为独立任务自动接力：
#   1) 官方源最多等 5 分钟；2) 失败自动切 npmmirror 镜像（实测含 cft 构建）；
#   3) Ubuntu 新版（如 26.04）再用兼容构建重试；4) Browser Use CLI（uv tool 安装，
#      PyPI 镜像兜底）。浏览器引擎没装好才返回失败，CLI 属 best-effort。
BROWSER_MIRROR = "https://cdn.npmmirror.com/binaries/playwright"

BROWSER_SCRIPT = """#!/usr/bin/env bash
# console-managed: 浏览器组件安装（Agent 核心能力；官方源优先、国内镜像自动兜底）
# 用法：bash browser_install.sh <hermes-agent 仓库目录>
set -u
REPO="${1:-}"
MIRROR="__MIRROR__"
HERMES_DIR="${HERMES_HOME:-$HOME/.hermes}"

if [ -z "$REPO" ] || [ ! -d "$REPO" ]; then
  echo "[console] 找不到 Hermes 安装目录，无法安装浏览器组件"
  exit 2
fi
cd "$REPO" || exit 2

# hermes 托管的 node / npm / npx / uv 常在用户级目录，补进 PATH 再调用
for d in "$HERMES_DIR/bin" "$HERMES_DIR/node/bin" "$HOME/.local/bin"; do
  if [ -d "$d" ]; then PATH="$d:$PATH"; fi
done
export PATH

# 国内镜像通道统一出口：npm 走 npmmirror、pip/uv 走清华（best-effort 步骤共用）
export NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-https://registry.npmmirror.com}"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX:-$PIP_INDEX_URL}"

if ! command -v timeout >/dev/null 2>&1; then
  # macOS 自带没有 timeout：退化为不设上限
  timeout() { shift; "$@"; }
fi

# 0) 确保 node/npx：国内镜像脚本是 core-only 最小模式，--skip-browser 时连 Node 都会跳过；
#    浏览器补装靠 npx，这里缺啥补啥（npmmirror 大陆可达）。
if ! command -v npx >/dev/null 2>&1 && [ ! -x "$HERMES_DIR/node/bin/npx" ]; then
  echo "[console] 未找到 Node.js，从 npmmirror 补装（约 30MB）..."
  case "$(uname -s)" in
    Linux) NOS="linux" ;;
    Darwin) NOS="darwin" ;;
    *) NOS="" ;;
  esac
  case "$(uname -m)" in
    x86_64) NARCH="x64" ;;
    aarch64|arm64) NARCH="arm64" ;;
    *) NARCH="" ;;
  esac
  NODE_OK=0
  if [ -n "$NOS" ] && [ -n "$NARCH" ]; then
    for NVER in v22.14.0 v20.19.0; do
      TB="node-${NVER}-${NOS}-${NARCH}.tar.xz"
      if timeout 300 curl -fsSL --connect-timeout 20 \
           "https://npmmirror.com/mirrors/node/${NVER}/${TB}" -o /tmp/hermes-node.tar.xz \
         && mkdir -p "$HERMES_DIR/node" \
         && tar -xJf /tmp/hermes-node.tar.xz -C "$HERMES_DIR/node" --strip-components=1; then
        for b in node npm npx; do
          ln -sf "$HERMES_DIR/node/bin/$b" "$HERMES_DIR/bin/$b" 2>/dev/null || true
        done
        rm -f /tmp/hermes-node.tar.xz
        NODE_OK=1
        echo "[console] Node.js 就绪（npmmirror，$NVER）"
        break
      fi
    done
  fi
  if [ "$NODE_OK" != "1" ]; then
    echo "[console] Node 补装失败——浏览器引擎安装可能失败（可稍后重试）"
  fi
  PATH="$HERMES_DIR/node/bin:$PATH"
  export PATH
fi

DEPS=""
if [ "$(id -u)" -eq 0 ] || (command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null); then
  DEPS="--with-deps"
  SUDO=""
  [ "$(id -u)" -eq 0 ] || SUDO="sudo -n"
  # 国内镜像脚本省掉的系统件（编译器/搜索/音频工具），apt 走服务器自带国内镜像
  echo "[console] 安装系统依赖（build-essential / ripgrep / ffmpeg，best-effort）..."
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
  if $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
       build-essential ripgrep ffmpeg >/dev/null 2>&1; then
    echo "[console] 系统依赖就绪"
  else
    echo "[console] 系统依赖安装跳过/失败（非致命）"
  fi
else
  echo "[console] 提示：无密码 sudo，跳过系统依赖；如浏览器启动报缺库，管理员执行："
  echo "[console]   sudo npx playwright install-deps chromium"
fi

PW_OK=0
echo "[console] 第 1 步：官方源安装浏览器引擎（最多 5 分钟，慢/卡会自动切换）..."
if timeout 300 npx playwright install $DEPS chromium; then
  echo "[console] 浏览器引擎安装完成（官方源）"
  PW_OK=1
fi

if [ "$PW_OK" != "1" ]; then
  echo "[console] 第 2 步：切换国内镜像重试..."
  if PLAYWRIGHT_DOWNLOAD_HOST="$MIRROR" timeout 900 npx playwright install $DEPS chromium; then
    echo "[console] 浏览器引擎安装完成（国内镜像）"
    PW_OK=1
  fi
fi

if [ "$PW_OK" != "1" ] && [ -r /etc/os-release ] && grep -qi '^ID=ubuntu' /etc/os-release; then
  PW_ARCH=""
  case "$(uname -m)" in
    x86_64) PW_ARCH="x64" ;;
    aarch64|arm64) PW_ARCH="arm64" ;;
  esac
  if [ -n "$PW_ARCH" ]; then
    echo "[console] 第 3 步：用 Ubuntu 兼容构建重试（本机系统版本较新时必需）..."
    if PLAYWRIGHT_DOWNLOAD_HOST="$MIRROR" \\
       PLAYWRIGHT_HOST_PLATFORM_OVERRIDE="ubuntu24.04-$PW_ARCH" \\
       timeout 900 npx playwright install $DEPS chromium; then
      echo "[console] 浏览器引擎安装完成（Ubuntu 兼容构建）"
      PW_OK=1
    fi
  fi
fi

# --- Browser Use CLI：浏览器自动化默认后端（best-effort，引擎就绪即算成功） ---
UV_CMD="$(command -v uv || true)"
if [ -x "$HERMES_DIR/bin/browser-use" ]; then
  echo "[console] Browser Use CLI 已安装"
elif [ -n "$UV_CMD" ]; then
  echo "[console] 安装 Browser Use CLI（浏览器自动化后端）..."
  if UV_NO_CONFIG=1 UV_TOOL_BIN_DIR="$HERMES_DIR/bin" timeout 600 "$UV_CMD" tool install browser-use; then
    echo "[console] Browser Use CLI 安装完成"
  elif UV_NO_CONFIG=1 UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \\
       UV_TOOL_BIN_DIR="$HERMES_DIR/bin" timeout 600 "$UV_CMD" tool install browser-use; then
    echo "[console] Browser Use CLI 安装完成（PyPI 国内镜像）"
  else
    echo "[console] Browser Use CLI 安装失败（可重试；引擎已就绪时浏览器功能可用内置后端）"
  fi
else
  echo "[console] 未找到 uv，跳过 Browser Use CLI（可由 hermes tools 安装）"
fi

# --- npx 缓存预热：运行时按需解析（agent-browser/playwright），预热失败不致命 ---
if [ "$PW_OK" = "1" ]; then
  timeout 300 npx --yes playwright --version >/dev/null 2>&1 || true
  timeout 300 npx --yes agent-browser --version >/dev/null 2>&1 || true
fi

# --- camofox 浏览器服务：官方走 npm；国内 npmmirror 通道（best-effort） ---
if command -v npm >/dev/null 2>&1; then
  echo "[console] 安装 camofox 浏览器服务（npm + npmmirror）..."
  if timeout 600 npm install -g --prefix "$HERMES_DIR/node" --silent --ignore-scripts \\
       "@askjo/camofox-browser@^1.5.2"; then
    echo "[console] camofox 就绪"
  else
    echo "[console] camofox 安装失败（非致命）"
  fi
fi

# --- 语音 + 唤醒依赖：官方走 uv pip；国内清华源通道（best-effort） ---
if [ -n "$UV_CMD" ] && [ -d "$REPO/venv" ]; then
  echo "[console] 安装语音/唤醒依赖（onnxruntime / faster-whisper，清华源）..."
  if (cd "$REPO" && VIRTUAL_ENV="$REPO/venv" timeout 900 "$UV_CMD" pip install -e ".[wake,voice]" >/dev/null 2>&1); then
    echo "[console] 语音/唤醒依赖就绪"
  else
    echo "[console] 语音依赖安装失败（非致命，首次使用时会展期安装）"
  fi
fi

# --- Computer Use 驱动（cua-driver）：上游只给 GitHub raw；直连失败走加速镜像（best-effort） ---
echo "[console] 尝试安装 Computer Use 驱动（cua-driver，best-effort）..."
CUA_SRC="https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh"
CUA_OK=0
for CUA_PREFIX in "" "https://ghfast.top/" "https://gh-proxy.com/"; do
  if timeout 660 bash -c "curl -fsSL '${CUA_PREFIX}${CUA_SRC}' | bash" >/dev/null 2>&1; then
    CUA_OK=1
    break
  fi
done
if [ "$CUA_OK" = "1" ]; then
  echo "[console] Computer Use 驱动就绪"
else
  echo "[console] Computer Use 驱动未装上（不影响浏览器自动化；需要时在终端跑 hermes computer-use install）"
fi

if [ "$PW_OK" = "1" ]; then
  echo "[console] 组件补齐完成（浏览器引擎 + CLI + camofox + 语音 + 系统件）。"
  exit 0
fi

echo "[console] 浏览器引擎未能安装。稍后可在本页重试，或手动执行："
echo "[console]   cd $REPO && npx playwright install chromium"
exit 1
"""


def browser_cli_installed(hermes_home) -> bool:
    """Browser Use CLI（浏览器自动化默认后端）是否已装：探测 Hermes 托管 bin 目录。"""
    from pathlib import Path

    bindir = Path(hermes_home) / "bin"
    return any((bindir / name).exists() for name in
               ("browser-use", "browser-use.exe", "browser-use.cmd"))


def install_health(paths) -> list[dict]:
    """安装完整性体检：逐项给出「完成/未完成 + 怎么补」。

    背景：早前只用"hermes 可执行文件存在"判定"已安装"，而该文件在安装早期就会
    生成——下载中断、venv 半成品、网关未注册都会被说成"安装好了"。现在逐项如实
    呈现；修复动作 = 幂等重跑安装 / 补装浏览器 / 启动网关时自动注册服务。"""
    from app.hermes.paths import resolve_agent_repo

    checks: list[dict] = []

    def add(label: str, ok: bool, hint: str = "") -> None:
        checks.append({"label": label, "ok": bool(ok), "hint": hint})

    add("可执行文件（hermes）", paths.installed,
        "未找到——点「继续 / 修复安装」")
    repo = resolve_agent_repo(paths)
    venv_python = False
    if repo is not None:
        venv_python = any(c.exists() for c in (
            repo / "venv" / "bin" / "python",
            repo / "venv" / "Scripts" / "python.exe"))
    add("源码与虚拟环境", repo is not None and venv_python,
        "安装可能在下载 / 建环境阶段被中断——点「继续 / 修复安装」")
    if sys.platform == "linux":
        from pathlib import Path

        try:
            unit_ok = any((Path.home() / ".config" / "systemd" / "user")
                          .glob("hermes-gateway*.service"))
        except OSError:
            unit_ok = False
        add("网关服务已注册", unit_ok, "点「启动 Gateway」会自动注册并启动")
    add("浏览器引擎（Chromium）", browser_installed(),
        "点「安装 / 补装浏览器组件」（自动切国内镜像）")
    add("Browser Use CLI", browser_cli_installed(paths.home),
        "同上，补装脚本会一并安装")
    return checks


def browser_install_job() -> str | None:
    """生成浏览器组件补装命令（脚本写入 jobs 目录）；平台不支持/未安装时返回 None。

    仅 POSIX：Windows 官方安装器里浏览器步骤是 best-effort（失败只警告、不阻塞），
    且 exec 沿用官方通道即可，无需本脚本。"""
    if sys.platform == "win32":
        return None
    from app.hermes.paths import detect, resolve_agent_repo

    paths = detect()
    repo = resolve_agent_repo(paths)
    if repo is None or not paths.bin:
        return None
    script = settings.jobs_dir / "browser_install.sh"
    script.write_text(BROWSER_SCRIPT.replace("__MIRROR__", BROWSER_MIRROR),
                      encoding="utf-8")
    return f'bash "{script}" "{repo}"'


def browser_installed() -> bool:
    """Playwright 浏览器引擎（Chromium）是否已装：按各平台默认缓存目录探测。
    探测失败按"未安装"处理——补装按钮多显示一次无害，漏显示才有害。"""
    from pathlib import Path

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        base = Path.home() / ".cache" / "ms-playwright"
    try:
        return any(p.name.startswith("chromium") for p in base.iterdir())
    except OSError:
        return False


class JobBusy(Exception):
    pass


def network_reachable(url: str = "https://raw.githubusercontent.com",
                      timeout: float = 5.0) -> bool:
    """安装/更新前的网络预检：官方脚本与更新包都从 GitHub 下载，
    国内网络直连常失败——提前探测，避免小白盯着必败的任务跑几分钟。

    HEAD 优先（省流量）；部分网络对 HEAD 不友好（能 GET 不能 HEAD），
    失败时回退 GET 首字节再判一次，避免"网络明明通的，预检却报不通"。"""
    import urllib.request

    def _try(method: str) -> bool:
        try:
            req = urllib.request.Request(url, method=method,
                                         headers={"User-Agent": "hermes-console"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if method == "GET":
                    resp.read(1)
                return 200 <= resp.status < 400
        except Exception:
            return False

    return _try("HEAD") or _try("GET")


NETWORK_HINT = (
    "网络无法访问官方安装源（GitHub / raw.githubusercontent.com）。"
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
    "browser_install": "补装浏览器组件",
    "gateway_start": "启动 Gateway",
    "gateway_stop": "停止 Gateway",
    "gateway_restart": "重启 Gateway",
}

# 网关动作驱动脚本：由控制台以「后台任务」方式运行（输出实时进终端 + 任务面板 + 日志文件，
# 可取消、可回查）。此前是同步调用——阻塞页面、且没有任何流水日志。
GATEWAY_ACTION_DRIVER = '''# console-managed: 网关动作驱动（start / stop / restart）
import sys

sys.path.insert(0, __ROOT__)

from app.hermes import supervisor  # noqa: E402
from app.hermes.paths import detect  # noqa: E402

action = sys.argv[1] if len(sys.argv) > 1 else ""
if action not in ("start", "stop", "restart"):
    print(f"[console] 未知动作：{action}")
    sys.exit(2)

try:
    out = getattr(supervisor, action)(detect())
except supervisor.SupervisorError as exc:
    print(str(exc))
    sys.exit(1)
print(out or f"已执行 {action}")
'''


def gateway_action_job(action: str) -> str:
    """生成网关动作的后台任务命令（驱动脚本写入 jobs 目录）。"""
    import json
    from pathlib import Path

    root = str(Path(__file__).resolve().parents[2])   # 控制台仓库根
    script = settings.jobs_dir / "gateway_action.py"
    script.write_text(
        GATEWAY_ACTION_DRIVER.replace("__ROOT__", json.dumps(root)),
        encoding="utf-8")
    return f'"{sys.executable}" "{script}" {action}'


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
    if browser_installed():
        return
    try:
        command = browser_install_job()
    except OSError:
        return
    if not command:
        return
    try:
        submit("browser_install", command)
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
        db.execute(
            "UPDATE job_runs SET status = ?, exit_code = ?, finished_at = datetime('now') "
            "WHERE id = ?",
            ("ok" if code == 0 else "failed", code, job_id),
        )
        # 主安装成功 → 自动接力浏览器组件（核心能力，默认必装；详见 _chain_after）
        _chain_after(kind, code == 0)

    threading.Thread(target=_worker, name=f"job-{job_id}", daemon=True).start()
    return job_id
