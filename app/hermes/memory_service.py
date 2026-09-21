"""记忆系统管理：内置记忆调优 + 外置记忆方案（provider）自动配置。

背景（官方文档与社区方案，2026-09）：
- 内置记忆 MEMORY.md/USER.md 默认仅 2,200/1,375 字符（约 1,300 token），
  大项目下容量紧张，写入超限会直接报错；
- 外置方案通过 `memory.provider` 启用，同一时刻只能激活一个，内置记忆仍然并存；
- 社区方案 agentmemory（github.com/rohitg00/agentmemory）提供跨 Agent 记忆共享与
  混合检索，接入方式 A：MCP（零代码）；方式 B：Provider 插件（深度集成）。

本模块把「选方案 → 填参数 → 写配置」收敛为一次表单提交：
写 config.yaml（memory.* / mcp_servers / plugins.*）+ 各 provider 的 JSON 配置文件 + .env 密钥。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from app.core import db
from app.core.settings import settings
from app.hermes.config_store import (
    EnvStore, ensure_path, get_path, load_config, load_json_opt, save_config,
    save_json,
)
from app.hermes.paths import HermesPaths, detect
from app.hermes.schema import FieldDef
from app.hermes.memory_providers import (  # noqa: F401  # 定义目录（重导出兼容）
    AGENTMEMORY_PLUGIN_JOB, BYTEROVER_CLI_JOB, MEMORY_PROVIDERS,
    MemoryError, MemoryProviderDef, get_provider_def, list_providers,
)

PROVIDER_CONFIG_FILES = {
    "honcho": "honcho.json",
    "mem0": "mem0.json",
    "supermemory": "supermemory.json",
    "hindsight": "hindsight/config.json",
}

# 内置记忆容量预设（label -> (memory_char_limit, user_char_limit)）
BUILTIN_PRESETS: dict[str, tuple[int, int]] = {
    "官方默认": (2200, 1375),
    "进阶": (8000, 4000),
    "重度使用": (16000, 8000),
}


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

@dataclass
class MemoryStatus:
    provider: str = ""                       # memory.provider；空 = 仅内置
    builtin: dict = field(default_factory=dict)
    context_file_max_chars: int | None = None
    mcp_agentmemory: bool = False
    plugin_agentmemory: bool = False
    config_files: dict = field(default_factory=dict)   # name -> bool
    memory_files: dict = field(default_factory=dict)   # MEMORY.md/USER.md -> 字符数


def _builtin_values(config: CommentedMap) -> dict:
    return {
        "memory_enabled": bool(get_path(config, "memory.memory_enabled", True)),
        "user_profile_enabled": bool(get_path(config, "memory.user_profile_enabled", True)),
        "memory_char_limit": get_path(config, "memory.memory_char_limit", 2200),
        "user_char_limit": get_path(config, "memory.user_char_limit", 1375),
        "nudge_interval": get_path(config, "memory.nudge_interval", 10),
        "write_approval": bool(get_path(config, "memory.write_approval", False)),
    }


def get_status(paths: HermesPaths | None = None) -> MemoryStatus:
    paths = paths or detect()
    config = load_config(paths)
    st = MemoryStatus(
        provider=str(get_path(config, "memory.provider", "") or ""),
        builtin=_builtin_values(config),
        context_file_max_chars=get_path(config, "context_file_max_chars", None),
    )
    mcp = get_path(config, "mcp_servers.agentmemory", None)
    st.mcp_agentmemory = isinstance(mcp, dict)
    st.plugin_agentmemory = (paths.home / "plugins" / "memory" / "agentmemory").is_dir()
    for name, rel in PROVIDER_CONFIG_FILES.items():
        st.config_files[name] = bool(load_json_opt(paths, rel))
    for filename in ("MEMORY.md", "USER.md"):
        p = paths.home / filename
        st.memory_files[filename] = p.stat().st_size if p.exists() else 0
    return st


# ---------------------------------------------------------------------------
# 内置记忆调优
# ---------------------------------------------------------------------------

def save_builtin(*, memory_enabled: bool, user_profile_enabled: bool,
                 memory_char_limit: int, user_char_limit: int,
                 nudge_interval: int, write_approval: bool,
                 context_file_max_chars: int | None) -> list[str]:
    if memory_char_limit < 500 or memory_char_limit > 200_000:
        raise MemoryError("memory_char_limit 需在 500 – 200,000 之间")
    if user_char_limit < 300 or user_char_limit > 100_000:
        raise MemoryError("user_char_limit 需在 300 – 100,000 之间")
    config = load_config()
    node = ensure_path(config, "memory")
    node["memory_enabled"] = bool(memory_enabled)
    node["user_profile_enabled"] = bool(user_profile_enabled)
    node["memory_char_limit"] = int(memory_char_limit)
    node["user_char_limit"] = int(user_char_limit)
    node["nudge_interval"] = max(0, int(nudge_interval))
    node["write_approval"] = bool(write_approval)
    if context_file_max_chars:
        if not (20_000 <= context_file_max_chars <= 500_000):
            raise MemoryError("context_file_max_chars 官方允许区间为 20,000 – 500,000")
        config["context_file_max_chars"] = int(context_file_max_chars)
    else:
        config.pop("context_file_max_chars", None)
    save_config(config)
    return ["memory.* 已更新", "context_file_max_chars 已更新"]


# ---------------------------------------------------------------------------
# agentmemory 插件安装驱动（跨平台 Python 脚本）。
# 旧的 AGENTMEMORY_PLUGIN_JOB 是 POSIX 一行命令：Windows 上 curl 与 tar 对 /tmp 的
# 解析不一致（文件下到 E:\tmp 而 tar 找 C:\tmp），cmd 里 $HOME 也不展开，任务必挂。
# 驱动脚本用 paths.detect() 取真实 hermes home，下载/解压/复制全在 Python 里做，
# 下载双通道（GitHub 直连 → 镜像回退），每步有进度输出，面板不再静默数分钟。
AGENTMEMORY_PLUGIN_DRIVER = '''# console-managed: agentmemory Provider 插件安装驱动（跨平台）
import pathlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request

sys.path.insert(0, __ROOT__)

try:
    # 双保险：防止 Windows GBK 控制台代码页把任务崩掉（同 gateway_action 一类问题）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from app.hermes.paths import detect  # noqa: E402

REPO = "rohitg00/agentmemory"
BRANCH = "main"
URLS = (
    f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.tar.gz",
    f"https://ghproxy.cn/https://github.com/{REPO}/archive/refs/heads/{BRANCH}.tar.gz",
)
TIMEOUT = 120


def download(url: str, dest: pathlib.Path) -> None:
    print(f"[console] 下载：{url}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-console"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        with dest.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    size = dest.stat().st_size
    print(f"[console] 下载完成（{size // 1024} KB）", flush=True)
    with dest.open("rb") as fh:
        magic = fh.read(2)
    if magic != bytes((0x1f, 0x8b)):
        raise RuntimeError(f"非 gzip 文件（{size} B），疑似错误页或镜像失效")


def main() -> None:
    paths = detect()
    if not paths.home:
        print("[console] 未探测到 Hermes 主目录，请先完成 Hermes 安装再重试。")
        sys.exit(2)
    home = pathlib.Path(paths.home)
    dest_dir = home / "plugins" / "memory" / "agentmemory"
    print(f"[console] 安装位置：{dest_dir}", flush=True)

    work = pathlib.Path(tempfile.mkdtemp(prefix="agentmemory-"))
    try:
        tgz = work / "agentmemory.tgz"
        last_err = None
        for url in URLS:
            try:
                download(url, tgz)
                break
            except Exception as exc:
                last_err = exc
                tgz.unlink(missing_ok=True)
                print(f"[console] 通道失败：{exc}；换下一通道…", flush=True)
        else:
            print(f"[console] 下载失败（已尝试 {len(URLS)} 个通道），最后错误：{last_err}")
            print(f"[console] 可手动：git clone https://github.com/{REPO}，"
                  f"再把 integrations/hermes 复制到 {dest_dir}")
            sys.exit(3)

        print("[console] 解压中…", flush=True)
        extract = work / "extract"
        extract.mkdir()
        with tarfile.open(tgz, "r:gz") as tf:
            tf.extractall(extract, filter="data")

        src = None
        for top in sorted(extract.iterdir()):
            cand = top / "integrations" / "hermes"
            if cand.is_dir():
                src = cand
                break
        if src is None:
            print("[console] 压缩包内未找到 integrations/hermes，仓库结构可能已变，"
                  f"请检查 {URLS[0]}")
            sys.exit(4)

        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.copytree(src, dest_dir)
        print(f"[console] 插件已安装：{dest_dir}", flush=True)
        print("[console] 若 Gateway 正在运行，重启后生效。", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
'''


def agentmemory_plugin_job() -> str:
    """生成 agentmemory 插件安装的后台任务命令（跨平台 Python 驱动脚本）。"""
    import json

    from app.hermes.jobs import write_job_script

    root = str(Path(__file__).resolve().parents[2])
    return write_job_script(
        "agentmemory_plugin.py",
        AGENTMEMORY_PLUGIN_DRIVER.replace("__ROOT__", json.dumps(root)),
    )

# ---------------------------------------------------------------------------
# 连接测试（W9）：此前 API Key 只写 .env、从不校验——填错 Key 也提示"启用成功"，
# 要回 Agent 会话里才发现记忆不动。这里做通用探测：端点可达性 + 密钥是否被拒
# （401/403）。不是完整鉴权，但能挡住"Key 填错""网络不通"这两类最高频问题。
# ---------------------------------------------------------------------------

_PROBE_TIMEOUT = 8


def _probe_key(d, values: dict) -> str:
    """密钥取值：表单当前值优先，回落已保存的 .env（用户可能没重填表单）。"""
    if d.probe_auth_field:
        raw = str(values.get(d.probe_auth_field) or "").strip()
        if not raw:
            raw = (EnvStore.load().get(d.probe_auth_field) or "").strip()
        return raw
    return ""


def test_connection(pid: str, values: dict | None = None) -> dict:
    values = values or {}
    d = get_provider_def(pid)
    probe = d.probe
    if pid == "hindsight" and values.get("mode") == "local":
        return {"level": "skip", "title": "本地模式",
                "message": "本地模式连接本机 Hindsight 服务，无云端端点可探测；"
                           "重启 Gateway 后在会话中验证。"}
    if pid == "agentmemory":
        url = values.get("agentmemory_api_url") or ""
        if not url:
            return {"level": "skip", "title": "本机模式",
                    "message": "MCP 本机模式由 Gateway 经 npx 拉起，无需探测；"
                               "已有服务端模式填入服务端地址后可测试。"}
        probe = url
    elif pid == "mem0" and values.get("host"):
        probe = values["host"].rstrip("/") + "/v1/ping/"
    elif pid == "honcho" and values.get("baseUrl"):
        probe = values["baseUrl"]
    if not probe:
        return {"level": "skip", "title": "纯本地方案",
                "message": f"{d.label} 不依赖外部服务，无需探测；重启 Gateway 后生效。"}
    # {FIELD} 占位符通用替换（不绑死 probe_auth_field：openviking 的探针
    # 占位符是端点地址而非密钥字段，绑死的话永远替换不掉）
    url = probe
    if "{" in url:
        import re as _re

        for fld in _re.findall(r"\{([A-Za-z0-9_]+)\}", url):
            url = url.replace("{%s}" % fld, str(values.get(fld) or "").strip())
    if not url.startswith(("http://", "https://")):
        return {"level": "error", "title": "地址不合法",
                "message": f"探测地址 {url!r} 不是 http(s) URL"}
    headers = {"User-Agent": "hermes-console"}
    key = _probe_key(d, values)
    if key and d.probe_auth_field:
        headers["Authorization"] = f"{d.probe_auth_scheme} {key}"
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except Exception as exc:
        return {"level": "error", "title": "无法连接",
                "message": f"{url} 连接失败：{exc}。检查服务是否启动、地址与代理设置。"}
    if code in (401, 403):
        return {"level": "error", "title": "密钥被拒绝",
                "message": f"端点可达但返回 {code}——API Key 无效或权限不足，请核对后重填。"}
    return {"level": "ok", "title": f"连接正常（HTTP {code}）",
            "message": "端点可达，密钥未被拒绝。启用并重启 Gateway 后在会话中验证记忆读写。"}


def _set_provider(config: CommentedMap, pid: str | None) -> None:
    node = ensure_path(config, "memory")
    if pid:
        node["provider"] = pid
    else:
        node.pop("provider", None)


def _read_form(form, d: MemoryProviderDef) -> dict:
    out = {}
    for f in d.form_fields:
        raw = form.get(f.name)
        if raw is None:
            continue
        raw = str(raw).strip()
        if f.kind == "bool":
            out[f.name] = raw.lower() in ("1", "true", "on", "yes")
        elif f.kind == "int":
            out[f.name] = int(raw) if raw.isdigit() else None
        else:
            out[f.name] = raw
    return out


def apply_provider(pid: str, form: dict) -> list[str]:
    """按方案写入全部所需配置；返回变更说明（供审计与 toast）。"""
    d = get_provider_def(pid)
    values = _read_form(form, d)
    _require(values, d)
    changes: list[str] = []
    config = load_config()

    if pid == "agentmemory":
        changes += _apply_agentmemory(config, values)
    elif pid == "mem0":
        changes += _apply_mem0(config, values)
    elif pid == "supermemory":
        changes += _apply_supermemory(config, values)
    elif pid == "openviking":
        changes += _apply_env_provider(config, d, values, "openviking")
    elif pid == "hindsight":
        changes += _apply_hindsight(config, values)
    elif pid == "holographic":
        changes += _apply_holographic(config, values)
    elif pid == "retaindb":
        changes += _apply_env_provider(config, d, values, "retaindb")
    elif pid == "byterover":
        _set_provider(config, "byterover")
        changes.append("memory.provider = byterover")
    elif pid == "honcho":
        changes += _apply_honcho(config, values)
    else:  # pragma: no cover
        raise MemoryError(f"方案未实现：{pid}")

    save_config(config)
    return changes


def _require(values: dict, d: MemoryProviderDef) -> None:
    for f in d.form_fields:
        if f.required and not values.get(f.name):
            raise MemoryError(f"缺少必填项：{f.label}")


def _write_env(**kv: str) -> None:
    env = EnvStore.load()
    touched = False
    for key, value in kv.items():
        if value:
            env.set(key, value)
            touched = True
    if touched:
        env.save()
        env.chmod_600()


def _apply_agentmemory(config: CommentedMap, values: dict) -> list[str]:
    changes: list[str] = []
    mode = values.get("mode", "mcp")
    mcp_node = ensure_path(config, "mcp_servers")
    if mode == "mcp":
        deployment = values.get("deployment", "local")
        if deployment == "remote" and not values.get("agentmemory_api_url"):
            raise MemoryError("连接已有服务端需要填写服务端地址（或改回「本机」模式）")
        entry = CommentedMap()
        entry["command"] = "npx"
        entry["args"] = ["agentmemory-mcp"]
        api_url = values.get("agentmemory_api_url") if deployment == "remote" else ""
        if api_url:
            entry["env"] = {"AGENTMEMORY_API_URL": api_url}
        mcp_node["agentmemory"] = entry
        changes.append("mcp_servers.agentmemory 已写入（MCP 零代码接入）")
    else:
        if "agentmemory" in mcp_node:
            mcp_node.pop("agentmemory")
            changes.append("已移除 mcp_servers.agentmemory（切换为插件形态）")
    _set_provider(config, "agentmemory")
    changes.append("memory.provider = agentmemory（插件目录由安装任务写入 plugins/memory/agentmemory）")
    return changes


def _apply_mem0(config: CommentedMap, values: dict) -> list[str]:
    mode = values.get("mode", "platform")
    updates: dict = {"user_id": values.get("user_id") or "hermes-user"}
    if mode == "selfhosted":
        if not values.get("host"):
            raise MemoryError("自建服务端模式需要填写服务端地址")
        updates["host"] = values["host"]
        if values.get("MEM0_API_KEY"):
            updates["api_key"] = values["MEM0_API_KEY"]
    else:
        updates.pop("host", None)
    save_json(None, "mem0.json", updates)
    _write_env(**{"MEM0_API_KEY": values.get("MEM0_API_KEY", "")})
    _set_provider(config, "mem0")
    return [f"mem0.json 已写入（{mode} 模式）", ".env 写入 MEM0_API_KEY",
            "memory.provider = mem0"]


def _apply_supermemory(config: CommentedMap, values: dict) -> list[str]:
    updates: dict = {}
    if values.get("base_url"):
        updates["base_url"] = values["base_url"]
    if values.get("container_tag"):
        updates["container_tag"] = values["container_tag"]
    for key in ("auto_recall", "auto_capture"):
        if key in values:
            updates[key] = bool(values[key])
    if updates:
        save_json(None, "supermemory.json", updates)
    _write_env(**{"SUPERMEMORY_API_KEY": values.get("SUPERMEMORY_API_KEY", "")})
    _set_provider(config, "supermemory")
    return ["supermemory.json 已更新" if updates else "supermemory.json 未变更",
            ".env 写入 SUPERMEMORY_API_KEY", "memory.provider = supermemory"]


def _apply_env_provider(config: CommentedMap, d: MemoryProviderDef,
                        values: dict, pid: str) -> list[str]:
    # 该形态的全部配置都是 .env 环境变量（全大写命名字段）
    kv = {f.name: str(values.get(f.name) or "") for f in d.form_fields
          if f.name == f.name.upper()}
    kv = {k: v for k, v in kv.items() if v}
    _write_env(**kv)
    _set_provider(config, pid)
    return [f".env 写入 {', '.join(kv)}" if kv else ".env 无变更",
            f"memory.provider = {pid}"]


def _apply_hindsight(config: CommentedMap, values: dict) -> list[str]:
    updates = {
        "mode": values.get("mode", "cloud"),
        "bank_id": values.get("bank_id") or "hermes",
        "recall_budget": values.get("recall_budget", "mid"),
        "memory_mode": values.get("memory_mode", "hybrid"),
    }
    save_json(None, "hindsight/config.json", updates)
    _write_env(**{"HINDSIGHT_API_KEY": values.get("HINDSIGHT_API_KEY", "")})
    _set_provider(config, "hindsight")
    return ["hindsight/config.json 已写入", ".env 写入 HINDSIGHT_API_KEY",
            "memory.provider = hindsight"]


def _apply_holographic(config: CommentedMap, values: dict) -> list[str]:
    node = ensure_path(config, "plugins.hermes-memory-store")
    if "auto_extract" in values:
        node["auto_extract"] = bool(values["auto_extract"])
    trust = values.get("default_trust")
    if trust:
        try:
            node["default_trust"] = max(0.0, min(1.0, float(trust)))
        except ValueError:
            pass
    _set_provider(config, "holographic")
    return ["plugins.hermes-memory-store 已写入", "memory.provider = holographic"]


def _apply_honcho(config: CommentedMap, values: dict) -> list[str]:
    host_block: dict = {"enabled": True, "aiPeer": "hermes"}
    if values.get("peerName"):
        host_block["peerName"] = values["peerName"]
    if values.get("workspace"):
        host_block["workspace"] = values["workspace"]
    updates: dict = {"hosts": {"hermes": host_block}}
    if values.get("apiKey"):
        updates["apiKey"] = values["apiKey"]
    if values.get("baseUrl"):
        updates["baseUrl"] = values["baseUrl"]
    save_json(None, "honcho.json", updates)
    _set_provider(config, "honcho")
    return ["honcho.json 已写入（保留既有配置合并更新）", "memory.provider = honcho"]


def disable_provider(keep_mcp: bool = True) -> list[str]:
    """关闭外置方案，回到纯内置记忆。"""
    config = load_config()
    _set_provider(config, None)
    changes = ["已移除 memory.provider（回到纯内置记忆）"]
    if not keep_mcp:
        mcp_node = get_path(config, "mcp_servers", None)
        if isinstance(mcp_node, dict) and "agentmemory" in mcp_node:
            mcp_node.pop("agentmemory")
            changes.append("已移除 mcp_servers.agentmemory（AgentMemory MCP 配置）")
    save_config(config)
    return changes


# ---------------------------------------------------------------------------
# 依赖安装任务：把 Provider 所需的 Python 包装进 Hermes 的运行环境
# ---------------------------------------------------------------------------
# 跨平台 Python 驱动（同 agentmemory 插件驱动的模式）：旧的 POSIX 一行命令
# （$HOME/.hermes/.../pip + $PIP）在 Windows cmd.exe 下必挂——cmd 不认单引号、
# 不展开 $HOME，路径分隔符也不对。驱动里用 paths.detect() 定位 Hermes 虚拟
# 环境的解释器（Windows 是 .venv/Scripts/python.exe，POSIX 是 .venv/bin/python），
# 全部经列表参数 subprocess.run，不再拼 shell 字符串。
PIP_DRIVER = '''# console-managed: 记忆 provider 依赖安装驱动（跨平台）
import pathlib
import subprocess
import sys

sys.path.insert(0, __ROOT__)

try:
    # 防 Windows GBK 控制台代码页把任务崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from app.hermes.paths import detect  # noqa: E402

PKGS = __PKGS__


def venv_python() -> pathlib.Path | None:
    paths = detect()
    if not paths.home:
        return None
    repo = pathlib.Path(paths.home) / "hermes-agent" / ".venv"
    exe = "python.exe" if sys.platform == "win32" else "python"
    for cand in (repo / ("Scripts" if sys.platform == "win32" else "bin") / exe,):
        if cand.exists():
            return cand
    return None


def main() -> None:
    py = venv_python()
    if py is None:
        print("[console] 未找到 Hermes 虚拟环境（~/.hermes/hermes-agent/.venv）。")
        print("[console] 请先完成 Hermes 安装，再回来执行依赖安装。")
        sys.exit(2)
    print(f"[console] 使用解释器：{py}", flush=True)
    for pkg in PKGS:
        print(f"[console] pip install {pkg} …", flush=True)
        proc = subprocess.run([str(py), "-m", "pip", "install", "--quiet", pkg])
        if proc.returncode != 0:
            print(f"[console] 安装失败：{pkg}（exit {proc.returncode}）"
                  "——网络需要代理时请先配好代理再重试")
            sys.exit(proc.returncode or 1)
    print(f"[console] deps installed: {' '.join(PKGS)}", flush=True)


if __name__ == "__main__":
    main()
'''


def deps_command(pid: str, values: dict | None = None) -> str:
    """生成依赖安装命令（跨平台 Python 驱动）；返回空串表示无需安装。"""
    import json

    from app.hermes.jobs import write_job_script

    values = values or {}
    d = get_provider_def(pid)
    if pid == "hindsight" and d.deps_dynamic:
        pkgs = ("hindsight-all",) if values.get("mode") == "local" else ("hindsight-client",)
    else:
        pkgs = d.deps_pip
    if not pkgs:
        return ""
    root = str(Path(__file__).resolve().parents[2])
    return write_job_script(
        f"memory_deps_{pid}.py",
        PIP_DRIVER.replace("__ROOT__", json.dumps(root))
                  .replace("__PKGS__", json.dumps(list(pkgs))),
    )


BYTEROVER_DRIVER = '''# console-managed: ByteRover CLI 安装驱动（跨平台）
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def main() -> None:
    if shutil.which("brv"):
        print("[console] brv CLI 已存在，跳过安装", flush=True)
        return
    npm = shutil.which("npm")
    if npm is None:
        print("[console] 未找到 npm（需要 Node.js）。")
        print("[console] 安装 Node.js 后执行：npm install -g byterover-cli")
        sys.exit(2)
    print("[console] npm install -g byterover-cli …", flush=True)
    proc = subprocess.run([npm, "install", "-g", "byterover-cli"])
    if proc.returncode != 0:
        print("[console] npm 安装失败（exit %d）——检查网络/代理后重试" % proc.returncode)
        sys.exit(proc.returncode or 1)
    print("[console] brv CLI 安装完成（npm 通道，三平台通用）", flush=True)


if __name__ == "__main__":
    main()
'''


def byterover_cli_job() -> str:
    """ByteRover CLI 安装命令：跨平台 npm 通道（官方 curl|sh 不支持 Windows）。"""
    import json

    from app.hermes.jobs import write_job_script

    root = str(Path(__file__).resolve().parents[2])
    return write_job_script("byterover_cli.py", BYTEROVER_DRIVER.replace("__ROOT__", json.dumps(root)))


def submit_install_job(pid: str, values: dict | None = None) -> int:
    from app.hermes import installer

    if pid == "byterover":
        command = byterover_cli_job()
    else:
        command = deps_command(pid, values)
    if not command and pid == "agentmemory":
        # 跨平台驱动：旧的 POSIX 一行命令在 Windows 上必挂（见 AGENTMEMORY_PLUGIN_DRIVER 注释）
        command = agentmemory_plugin_job()
    if not command:
        command = get_provider_def(pid).job_command
    if not command:
        raise MemoryError(f"{get_provider_def(pid).label} 无需额外安装任务")
    return installer.submit(f"memory_{pid}", command)


def job_active_for(pid: str) -> bool:
    from app.hermes import installer

    job = installer.active_job()
    return bool(job and job["kind"] == f"memory_{pid}")
