"""Shell Hooks 管理：config.yaml `hooks.<event>[]` 的 CRUD 与信任白名单盘点。

对齐 Hermes 官方语义（hermes-agent/agent/shell_hooks.py 与
website/docs/user-guide/features/hooks.md 的 Configuration schema）：
- 每个 hook：command（必填，shlex.split 执行）/ matcher（仅 pre/post_tool_call）/
  timeout（默认 60、上限 300）/ fail_closed（仅 pre_tool_call 可阻断）；
- 事件名必须是官方 VALID_HOOKS 子集（hermes_cli/plugins.py），拼错会被官方跳过；
- 首次使用需要 per-(event, command) 同意（hooks_auto_accept 或 TTY 确认），
  白名单落盘 ~/.hermes/agent-hooks/shell-hooks-allowlist.json；
- gateway hooks（~/.hermes/hooks/<name>/）与 outbound webhooks 本模块只读展示。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

from app.hermes.config_store import ensure_path, get_path, load_config, save_config
from app.hermes.paths import HermesPaths, detect

# 官方 VALID_HOOKS 全集（hermes-agent/hermes_cli/plugins.py，随上游更新需同步）
VALID_HOOKS = frozenset({
    "pre_tool_call", "post_tool_call", "transform_terminal_output", "transform_tool_result",
    "transform_llm_output", "pre_llm_call", "post_llm_call",
    "on_stream_start", "on_stream_delta", "on_stream_end", "on_interim_message",
    "pre_verify", "pre_api_request", "post_api_request", "api_request_error",
    "transform_api_error_classification", "on_session_start", "on_session_end",
    "on_session_finalize", "on_session_reset",
    "on_skill_lifecycle", "subagent_start", "subagent_stop",
    "pre_gateway_dispatch", "pre_approval_request", "post_approval_response",
    "pre_transcription",
    "kanban_task_claimed", "kanban_task_completed", "kanban_task_blocked",
    "on_kanban_worker_spawned", "on_kanban_worker_exited", "on_kanban_worker_stale_claim",
    "on_kanban_task_updated", "on_kanban_dispatch_tick",
    "gateway_platform_event",
})
TOOL_EVENTS = frozenset({"pre_tool_call", "post_tool_call"})       # matcher 仅这两个事件有意义
BLOCKING_EVENTS = frozenset({"pre_tool_call"})                     # 仅此事件可阻断/fail_closed

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300
ALLOWLIST_REL = "agent-hooks/shell-hooks-allowlist.json"

_yaml = YAML(typ="safe", pure=True)


class HookError(Exception):
    pass


# ---------------------------------------------------------------------------
# Shell hooks CRUD
# ---------------------------------------------------------------------------

@dataclass
class ShellHookView:
    event: str
    command: str
    matcher: str = ""
    timeout: int | None = None       # None = 官方默认 60
    fail_closed: bool = False
    allowlisted: bool = False

    @property
    def can_block(self) -> bool:
        return self.event in BLOCKING_EVENTS

    @property
    def allowlist_label(self) -> str:
        if self.allowlisted:
            return "已信任"
        return "待确认"


def _hooks_node(config) -> dict:
    node = get_path(config, "hooks", {})
    return node if isinstance(node, dict) else {}


def list_hooks(paths: HermesPaths | None = None) -> list[ShellHookView]:
    config = load_config(paths)
    approved = {f"{e.get('event')}\n{e.get('command')}"
                for e in allowlist(paths)}
    out: list[ShellHookView] = []
    for event, entries in _hooks_node(config).items():
        if event == "outbound" or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("command"):
                continue
            timeout = entry.get("timeout")
            out.append(ShellHookView(
                event=str(event),
                command=str(entry["command"]),
                matcher=str(entry.get("matcher") or ""),
                timeout=int(timeout) if isinstance(timeout, (int, float)) else None,
                fail_closed=bool(entry.get("fail_closed") or entry.get("failClosed")),
                allowlisted=f"{event}\n{entry['command']}" in approved,
            ))
    out.sort(key=lambda h: (h.event, h.command))
    return out


def create_hook(*, event: str, command: str, matcher: str = "",
                timeout: str = "", fail_closed: bool = False,
                paths: HermesPaths | None = None) -> list[str]:
    event = event.strip()
    command = command.strip()
    matcher = matcher.strip()
    _validate(event, command, matcher, timeout, fail_closed)
    paths = paths or detect()
    config = load_config(paths)
    node = ensure_path(config, "hooks")

    entries = node.get(event)
    if not isinstance(entries, list):
        entries = []
        node[event] = entries
    # 同 (event, command) 视为编辑覆盖
    entries[:] = [e for e in entries
                  if not (isinstance(e, dict) and str(e.get("command")) == command)]

    entry: dict = {}
    if matcher and event in TOOL_EVENTS:
        try:
            re.compile(matcher)
        except re.error as exc:
            raise HookError(f"matcher 不是合法的正则：{exc}") from exc
        entry["matcher"] = matcher
    entry["command"] = command
    if timeout.strip():
        entry["timeout"] = int(timeout.strip())
    if fail_closed and event in BLOCKING_EVENTS:
        entry["fail_closed"] = True
    entries.append(entry)
    save_config(config, paths)
    return [f"hooks.{event} 已更新（{len(entries)} 条）"]


def _validate(event: str, command: str, matcher: str, timeout: str,
              fail_closed: bool) -> None:
    if event not in VALID_HOOKS:
        raise HookError(f"事件名不在官方 VALID_HOOKS 内：{event}")
    if not command:
        raise HookError("command 不能为空")
    if matcher and event not in TOOL_EVENTS:
        raise HookError("matcher 仅支持 pre_tool_call / post_tool_call 事件")
    if fail_closed and event not in BLOCKING_EVENTS:
        raise HookError("fail_closed 仅支持 pre_tool_call 事件（只有它能阻断）")
    if timeout.strip():
        if not timeout.strip().isdigit():
            raise HookError("timeout 必须是正整数（官方默认 60，上限 300）")
        if not 1 <= int(timeout.strip()) <= MAX_TIMEOUT:
            raise HookError(f"timeout 需在 1-{MAX_TIMEOUT} 秒之间")


def delete_hook(event: str, command: str,
                paths: HermesPaths | None = None) -> None:
    paths = paths or detect()
    config = load_config(paths)
    node = get_path(config, "hooks", None)
    entries = node.get(event) if isinstance(node, dict) else None
    if not isinstance(entries, list):
        raise HookError(f"事件 {event} 下没有配置 hook")
    remaining = [e for e in entries
                 if not (isinstance(e, dict) and str(e.get("command")) == command)]
    if len(remaining) == len(entries):
        raise HookError("未找到该 hook（事件与命令需完全匹配）")
    if remaining:
        node[event] = remaining
    else:
        node.pop(event)
    if isinstance(node, dict) and set(node.keys()) == {"outbound"}:
        # 只剩 outbound 时保留；整节点为空才摘除
        if not node.get("outbound"):
            config.pop("hooks", None)
    save_config(config, paths)


def get_auto_accept(paths: HermesPaths | None = None) -> bool:
    return bool(get_path(load_config(paths), "hooks_auto_accept", False))


def set_auto_accept(enabled: bool, paths: HermesPaths | None = None) -> None:
    config = load_config(paths)
    if enabled:
        config["hooks_auto_accept"] = True
    else:
        config.pop("hooks_auto_accept", None)
    save_config(config, paths)


# ---------------------------------------------------------------------------
# 信任白名单（shell-hooks-allowlist.json）
# ---------------------------------------------------------------------------

def _allowlist_path(paths: HermesPaths) -> Path:
    return paths.home / ALLOWLIST_REL


def allowlist(paths: HermesPaths | None = None) -> list[dict]:
    paths = paths or detect()
    target = _allowlist_path(paths)
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    approvals = data.get("approvals") if isinstance(data, dict) else None
    return [e for e in approvals if isinstance(e, dict)] if isinstance(approvals, list) else []


def revoke_allowlist_entry(command: str, paths: HermesPaths | None = None) -> int:
    """等效 `hermes hooks revoke`：删除该 command 的全部白名单条目。"""
    paths = paths or detect()
    target = _allowlist_path(paths)
    if not target.exists():
        raise HookError("信任白名单为空")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise HookError(f"白名单文件读取失败：{exc}") from exc
    approvals = data.get("approvals") if isinstance(data, dict) else None
    if not isinstance(approvals, list):
        raise HookError("白名单文件结构异常")
    kept = [e for e in approvals
            if not (isinstance(e, dict) and e.get("command") == command)]
    removed = len(approvals) - len(kept)
    if removed == 0:
        raise HookError("未找到匹配的白名单条目")
    data["approvals"] = kept
    import os
    import tempfile

    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".allowlist-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp_name, target)
    return removed


# ---------------------------------------------------------------------------
# 只读盘点：gateway hooks 与 outbound webhooks
# ---------------------------------------------------------------------------

def gateway_hooks(paths: HermesPaths | None = None) -> list[dict]:
    """扫描 ~/.hermes/hooks/<name>/HOOK.yaml（gateway 生命周期 hook，只读）。"""
    paths = paths or detect()
    base = paths.home / "hooks"
    if not base.is_dir():
        return []
    out: list[dict] = []
    for hook_yaml in sorted(base.glob("*/HOOK.yaml")):
        try:
            data = _yaml.load(hook_yaml.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        out.append({
            "name": str(data.get("name") or hook_yaml.parent.name),
            "description": str(data.get("description") or ""),
            "events": [str(e) for e in data.get("events", [])]
            if isinstance(data.get("events"), list) else [],
            "dir": hook_yaml.parent.name,
        })
    return out


def outbound_webhooks(paths: HermesPaths | None = None) -> list[dict]:
    """读取 config.yaml hooks.outbound（签名事件推送，只读）。"""
    config = load_config(paths)
    raw = _hooks_node(config).get("outbound")
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict) and e.get("url")]
