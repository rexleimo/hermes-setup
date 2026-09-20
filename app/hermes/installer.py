"""安装 / 更新域：安装源、权限探测、浏览器组件补装、Gateway 动作驱动。

「通用后台任务引擎」已拆到 jobs.py（submit/取消/历史/日志/收尾副作用）；
本模块保留安装域逻辑，并向下兼容重导出引擎 API（installer.submit 等旧调用
路径不变）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid

from app.core import db
from app.core.settings import settings

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

# ---------------------------------------------------------------------------
# 权限探测与 sudo 通道
# ---------------------------------------------------------------------------
# 背景：部分 Linux 账号不是 root、也没有免密 sudo，而是「sudo 要输密码」。
# 后台任务没有 TTY（stdin 已断开），任何交互式 sudo 密码提示都会让任务卡死或
# 静默失败。因此：
#   1) 页面加载时探测当前账号权限，如实展示（root/免密/需密码/无 sudo）；
#   2) 「需密码」的账号在表单里填 sudo 密码 → 任务通过 sudo -S 一次性非交互
#      认证（密码只进 0600 临时文件，不进命令行/日志/数据库，用完即删）；
#   3) 认证失败 1 秒内明确报错退出（exit 3），不再卡在密码提示上。

_PRIV_LABELS = {
    "root": "当前以 root 运行，安装无需 sudo",
    "passwordless": "当前账号可免密 sudo，安装可自动完成系统组件",
    "password": "当前账号安装系统组件需要 sudo 密码（在下方输入）",
    "nosudo": "当前账号无 sudo——浏览器系统依赖将跳过，不影响主流程",
    "windows": "Windows 原生安装通道，无需 sudo",
}
_priv_cache: tuple[float, dict] | None = None
_PRIV_TTL = 30.0


def _classify_privilege(euid: int, sudo_available: bool,
                        passwordless: bool) -> str:
    """权限分类纯函数（便于单测）：root > 免密 sudo > 需密码 sudo > 无 sudo。"""
    if euid == 0:
        return "root"
    if not sudo_available:
        return "nosudo"
    return "passwordless" if passwordless else "password"


def detect_privilege(force: bool = False) -> dict:
    """探测当前运行账号的提权能力（缓存 30s，避免页面轮询反复 fork）。"""
    global _priv_cache
    if sys.platform == "win32":
        return {"mode": "windows", "label": _PRIV_LABELS["windows"],
                "needs_password": False}
    now = time.monotonic()
    if not force and _priv_cache and now - _priv_cache[0] < _PRIV_TTL:
        return dict(_priv_cache[1])
    euid = os.geteuid() if hasattr(os, "geteuid") else -1
    sudo = shutil.which("sudo")
    passwordless = False
    if sudo:
        try:
            passwordless = (
                subprocess.run([sudo, "-n", "true"], capture_output=True,
                               timeout=5).returncode == 0)
        except Exception:
            passwordless = False
    mode = _classify_privilege(euid, bool(sudo), passwordless)
    info = {"mode": mode, "label": _PRIV_LABELS[mode],
            "needs_password": mode == "password"}
    _priv_cache = (now, info)
    return dict(info)


def _write_sudo_passfile(password: str):
    """sudo 密码落 0600 临时文件（jobs 目录内）：密码不进命令行/日志/数据库，
    任务脚本认证完即删。返回文件路径。"""
    path = settings.jobs_dir / f"sudo-pass-{uuid.uuid4().hex[:12]}"
    path.write_text(password + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


# sudo 安装驱动：先非交互认证一次（sudo 凭据缓存随后几分钟内有效，官方脚本
# 内部的 sudo 调用不再提问），认证失败立即退出并给出人话原因；成功则 exec 安装命令。
SUDO_DRIVER_TEMPLATE = """#!/usr/bin/env bash
# console-managed: 安装 sudo 驱动（后台任务无 TTY，密码提示会卡死任务）
set -u
PASSFILE="__PASSFILE__"
CMD="__CMD__"
if [ ! -f "$PASSFILE" ]; then
  echo "[console] sudo 凭据文件丢失，按免密方式继续（系统组件可能跳过）"
else
  if printf '%s\\n' "$(cat "$PASSFILE")" | sudo -S -p '' true 2>/dev/null; then
    echo "[console] sudo 认证成功，继续安装"
  else
    rm -f "$PASSFILE"
    echo "[console] sudo 认证失败：密码不正确，或该账号不在 sudoers 中。"
    echo "[console] 请在「安装与更新」页重新输入 sudo 密码；或由管理员在终端执行安装。"
    exit 3
  fi
  rm -f "$PASSFILE"
fi
exec bash -c "$CMD"
"""


def install_command(sudo_pass: str | None = None) -> str:
    """安装任务命令：默认裸官方命令；提供了 sudo 密码（POSIX）时包一层
    sudo 驱动脚本——先认证后执行，密码不出现在命令行与日志里。"""
    base = preferred_install()["cmd"]
    if not sudo_pass or sys.platform == "win32":
        return base
    import json as _json

    passfile = _write_sudo_passfile(sudo_pass)
    script = settings.jobs_dir / "sudo_install.sh"
    script.write_text(
        SUDO_DRIVER_TEMPLATE.replace("__PASSFILE__", str(passfile))
                            .replace("__CMD__", _json.dumps(base)),
        encoding="utf-8")
    return f'bash "{script}"'


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
PASSFILE="${HERMES_SUDO_PASS_FILE:-}"
# sudo 密码文件（控制台任务提交时注入；0600，退出即删）——有了它，
# 非 root 账号的系统依赖也能装上；没有就退回免密探测，再不行才跳过。
if [ -n "$PASSFILE" ] && [ -f "$PASSFILE" ]; then trap 'rm -f "$PASSFILE"' EXIT; fi
run_sudo() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; return; fi
  if [ -n "$PASSFILE" ] && [ -f "$PASSFILE" ]; then
    printf '%s\\n' "$(cat "$PASSFILE")" | sudo -S -p '' "$@"
  else
    sudo -n "$@" 2>/dev/null
  fi
}

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
if [ "$(id -u)" -eq 0 ] || run_sudo true; then
  DEPS="--with-deps"
  # 国内镜像脚本省掉的系统件（编译器/搜索/音频工具），apt 走服务器自带国内镜像
  echo "[console] 安装系统依赖（build-essential / ripgrep / ffmpeg，best-effort）..."
  run_sudo env DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
  if run_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
       build-essential ripgrep ffmpeg >/dev/null 2>&1; then
    echo "[console] 系统依赖就绪"
  else
    echo "[console] 系统依赖安装跳过/失败（非致命）"
  fi
else
  echo "[console] 提示：sudo 不可用（或未提供密码），跳过系统依赖；如浏览器启动报缺库，管理员执行："
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


def browser_install_job(sudo_pass: str | None = None) -> str | None:
    """生成浏览器组件补装命令（脚本写入 jobs 目录）；平台不支持/未安装时返回 None。

    仅 POSIX：Windows 官方安装器里浏览器步骤是 best-effort（失败只警告、不阻塞），
    且 exec 沿用官方通道即可，无需本脚本。提供 sudo_pass 时，脚本内 run_sudo
    用它完成系统依赖安装（密码经 0600 临时文件注入，脚本退出即删）。"""
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
    command = f'bash "{script}" "{repo}"'
    if sudo_pass:
        passfile = _write_sudo_passfile(sudo_pass)
        command = f'HERMES_SUDO_PASS_FILE="{passfile}" {command}'
    return command


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


# 网关动作驱动脚本：由控制台以「后台任务」方式运行（输出实时进终端 + 任务面板 + 日志文件，
# 可取消、可回查）。此前是同步调用——阻塞页面、且没有任何流水日志。
GATEWAY_ACTION_DRIVER = '''# console-managed: 网关动作驱动（start / stop / restart）
import sys

try:
    # 双保险：即便没继承 PYTHONIOENCODING，也不允许 GBK 代码页崩掉任务
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, __ROOT__)

from app.hermes import supervisor  # noqa: E402
from app.hermes.paths import detect  # noqa: E402

action = sys.argv[1] if len(sys.argv) > 1 else ""
if action not in ("start", "stop", "restart"):
    print(f"[console] 未知动作：{action}")
    sys.exit(2)

# 先给一行进度：网关首次启动会先注册系统服务（最长约 3 分钟），
# 免得用户盯着"没有输出"以为卡死。
print(f"[console] 正在执行 hermes gateway {action}（首次会自动注册系统服务，"
      "最长约 3 分钟，请稍候）...", flush=True)
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

    from app.hermes.jobs import write_job_script   # 延迟导入避免循环

    root = str(Path(__file__).resolve().parents[2])   # 控制台仓库根
    return write_job_script(
        "gateway_action.py",
        GATEWAY_ACTION_DRIVER.replace("__ROOT__", json.dumps(root)),
        args=(action,),
    )



# ---------------------------------------------------------------------------
# 通用后台任务引擎（现居 jobs.py）——重导出保持旧调用路径（installer.submit 等）
# ---------------------------------------------------------------------------
from app.hermes.jobs import (  # noqa: E402,F401
    JOB_KIND_LABELS,
    JobBusy,
    _chain_after,
    _finish_job,
    _procs,
    active_job,
    cancel,
    job_history,
    job_log,
    job_log_delta,
    last_job,
    reap_orphan_jobs,
    submit,
)
