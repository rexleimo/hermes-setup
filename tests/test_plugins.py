"""插件与 Hook 管理：service 级（白名单/hooks CRUD/白名单撤销）+ Web 级流程。"""
from __future__ import annotations

import json

from app.hermes import hooks_service as hsvc
from app.hermes import plugins_service as psvc
from app.hermes.config_store import get_path, load_config
from tests.conftest import csrf_of


# ---------------------------------------------------------------------------
# 插件 service 级
# ---------------------------------------------------------------------------

def _make_plugin(home, name: str, *, hooks: list[str] | None = None,
                 enabled: bool = False) -> None:
    d = home / "plugins" / name
    d.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}", 'version: "0.1.0"', f"description: {name} 插件"]
    if hooks:
        lines.append("hooks:")
        lines += [f"  - {h}" for h in hooks]
    (d / "plugin.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "__init__.py").write_text("", encoding="utf-8")
    if enabled:
        cfg = home / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8")
                       + f"plugins:\n  enabled:\n    - {name}\n", encoding="utf-8")


def test_plugin_list_whitelist_and_toggle(hermes_home):
    _make_plugin(hermes_home.home, "my-plugin", hooks=["pre_tool_call"])
    views = psvc.list_plugins()
    assert views[0].name == "my-plugin"
    assert views[0].enabled is False          # 默认禁用（官方信任模型）
    assert views[0].hooks == ["pre_tool_call"]

    psvc.set_enabled("my-plugin", True)
    assert get_path(load_config(hermes_home), "plugins.enabled") == ["my-plugin"]
    assert psvc.list_plugins()[0].enabled is True

    psvc.set_enabled("my-plugin", False)
    assert get_path(load_config(hermes_home), "plugins.enabled", None) is None

    # 白名单残留（目录已删）→ broken 视图
    hermes_home.config_yaml.write_text(
        "plugins:\n  enabled:\n    - ghost\n", encoding="utf-8")
    views = psvc.list_plugins()
    assert views[0].name == "ghost" and views[0].broken


def test_plugin_remove_and_timeout(hermes_home):
    _make_plugin(hermes_home.home, "doomed", enabled=True)
    notes = psvc.remove_plugin("doomed")
    assert not (hermes_home.home / "plugins" / "doomed").exists()
    assert any("白名单" in n for n in notes)

    psvc.set_hook_timeout(45)
    assert psvc.get_hook_timeout() == 45
    assert get_path(load_config(hermes_home), "plugins.hook_callback_timeout") == 45
    psvc.set_hook_timeout(30)   # 默认值不落键
    assert get_path(load_config(hermes_home), "plugins.hook_callback_timeout", None) is None

    import pytest
    with pytest.raises(psvc.PluginError):
        psvc.remove_plugin("nope")
    with pytest.raises(psvc.PluginError):
        psvc.set_enabled("nope", True)
    with pytest.raises(psvc.PluginError):
        psvc.set_hook_timeout(9999)


def test_plugin_catalog_and_install_job(hermes_home):
    catalog_dir = hermes_home.home / "hermes-agent" / "plugin-catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / "sample-plugin.yaml").write_text(
        "name: sample-plugin\n"
        "repo: https://github.com/x/y\n"
        "sha: " + "a" * 40 + "\n"
        "description: 官方策展示例插件\n"
        "tier: community\n"
        "capabilities:\n"
        "  provides_tools: [t1, t2]\n"
        "  provides_hooks: []\n"
        "  requires_env: [SAMPLE_TOKEN]\n", encoding="utf-8")
    (catalog_dir / "removed.yaml").write_text("removed: []\n", encoding="utf-8")

    entries = psvc.catalog()
    assert len(entries) == 1 and entries[0].name == "sample-plugin"
    assert entries[0].provides_tools == 2 and entries[0].requires_env == ["SAMPLE_TOKEN"]

    # 未装 hermes CLI → 明确报错
    import pytest
    with pytest.raises(psvc.PluginError, match="未找到 hermes"):
        psvc.install("sample-plugin")

    # bin 指向 stub → 后台任务提交成功
    from app.hermes.paths import HermesPaths
    from app.hermes import paths as paths_module

    stub = hermes_home.home / "bin" / "hermes"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    paths_module.set_override(HermesPaths(home=hermes_home.home, bin=str(stub)))
    try:
        job_id = psvc.install("sample-plugin")
        assert job_id > 0
        with pytest.raises(psvc.PluginError, match="已安装"):
            _make_plugin(hermes_home.home, "sample-plugin")
            psvc.install("sample-plugin")
    finally:
        paths_module.set_override(None)


# ---------------------------------------------------------------------------
# Shell hooks service 级
# ---------------------------------------------------------------------------

def test_shell_hook_crud_and_validation(hermes_home):
    hsvc.create_hook(event="pre_tool_call", command="/opt/guard.sh",
                     matcher="terminal", timeout="120", fail_closed=True)
    hooks = hsvc.list_hooks()
    assert len(hooks) == 1
    h = hooks[0]
    assert h.event == "pre_tool_call" and h.command == "/opt/guard.sh"
    assert h.matcher == "terminal" and h.timeout == 120 and h.fail_closed
    assert get_path(load_config(hermes_home), "hooks.pre_tool_call")[0]["fail_closed"] is True

    # 同 (event, command) 再次创建 = 覆盖编辑
    hsvc.create_hook(event="pre_tool_call", command="/opt/guard.sh", timeout="30")
    assert len(hsvc.list_hooks()) == 1
    assert hsvc.list_hooks()[0].timeout == 30

    hsvc.create_hook(event="post_tool_call", command="make fmt")
    hsvc.delete_hook("post_tool_call", "make fmt")
    assert len(hsvc.list_hooks()) == 1

    # 官方校验规则
    import pytest
    with pytest.raises(hsvc.HookError, match="VALID_HOOKS"):
        hsvc.create_hook(event="not_an_event", command="x")
    with pytest.raises(hsvc.HookError, match="matcher"):
        hsvc.create_hook(event="pre_llm_call", command="x", matcher="a|b")
    with pytest.raises(hsvc.HookError, match="fail_closed"):
        hsvc.create_hook(event="post_tool_call", command="x", fail_closed=True)
    with pytest.raises(hsvc.HookError, match="1-300"):
        hsvc.create_hook(event="pre_tool_call", command="x", timeout="999")
    with pytest.raises(hsvc.HookError, match="正则"):
        hsvc.create_hook(event="pre_tool_call", command="x", matcher="([bad")


def test_shell_hook_auto_accept_and_allowlist(hermes_home):
    assert hsvc.get_auto_accept() is False
    hsvc.set_auto_accept(True)
    assert hsvc.get_auto_accept() is True
    hsvc.set_auto_accept(False)
    assert get_path(load_config(hermes_home), "hooks_auto_accept", None) is None

    # 白名单文件不存在
    import pytest
    with pytest.raises(hsvc.HookError, match="为空"):
        hsvc.revoke_allowlist_entry("whatever")

    # 造一条白名单记录 → 展示 + 撤销
    allow_path = hermes_home.home / "agent-hooks" / "shell-hooks-allowlist.json"
    allow_path.parent.mkdir(parents=True, exist_ok=True)
    allow_path.write_text(json.dumps({
        "approvals": [{"event": "pre_tool_call", "command": "/opt/guard.sh",
                       "approved_at": "2026-09-16T00:00:00Z"}]}), encoding="utf-8")
    assert len(hsvc.allowlist()) == 1
    assert hsvc.list_hooks() == []  # 仅白名单有记录、未配置 hook

    hsvc.create_hook(event="pre_tool_call", command="/opt/guard.sh")
    assert hsvc.list_hooks()[0].allowlisted is True

    assert hsvc.revoke_allowlist_entry("/opt/guard.sh") == 1
    assert hsvc.allowlist() == []
    with pytest.raises(hsvc.HookError, match="未找到"):
        hsvc.revoke_allowlist_entry("/opt/guard.sh")


def test_gateway_hooks_and_outbound_readonly(hermes_home):
    gh = hermes_home.home / "hooks" / "my-hook"
    gh.mkdir(parents=True)
    (gh / "HOOK.yaml").write_text(
        "name: my-hook\ndescription: 日志记录\nevents:\n  - agent:start\n  - agent:end\n",
        encoding="utf-8")
    found = hsvc.gateway_hooks()
    assert found == [{"name": "my-hook", "description": "日志记录",
                      "events": ["agent:start", "agent:end"], "dir": "my-hook"}]

    hermes_home.config_yaml.write_text(
        "hooks:\n"
        "  pre_tool_call:\n"
        "    - command: guard.sh\n"
        "  outbound:\n"
        "    - name: ci\n"
        "      url: https://ci.example.com/h\n"
        "      events: [on_session_end]\n", encoding="utf-8")
    out = hsvc.outbound_webhooks()
    assert len(out) == 1 and out[0]["url"] == "https://ci.example.com/h"
    # outbound 不应混进 shell hook 列表
    assert all(h.event != "outbound" for h in hsvc.list_hooks())


# ---------------------------------------------------------------------------
# Web 级
# ---------------------------------------------------------------------------

def test_web_plugins_page_and_whitelist(logged_in, hermes_home):
    _make_plugin(hermes_home.home, "web-plugin")
    c = logged_in
    token = csrf_of(c)

    page = c.get("/plugins")
    assert page.status_code == 200
    assert "web-plugin" in page.text and "未加入白名单" in page.text

    resp = c.post("/plugins/web-plugin/toggle", data={"enable": "1", "_csrf": token},
                  follow_redirects=False)
    assert resp.status_code == 303
    assert psvc.enabled_names() == ["web-plugin"]

    resp = c.post("/plugins/web-plugin/delete", data={"confirm": "nope", "_csrf": token})
    assert resp.status_code == 400
    resp = c.post("/plugins/web-plugin/delete", data={"confirm": "web-plugin", "_csrf": token},
                  follow_redirects=False)
    assert resp.status_code == 303
    assert not (hermes_home.home / "plugins" / "web-plugin").exists()


def test_web_shell_hooks_flow(logged_in):
    c = logged_in
    token = csrf_of(c)

    # 校验失败 → 400 错误页
    resp = c.post("/plugins/hooks/create", data={
        "event": "not_an_event", "command": "x", "_csrf": token})
    assert resp.status_code == 400

    resp = c.post("/plugins/hooks/create", data={
        "event": "pre_tool_call", "command": "guard.sh",
        "matcher": "terminal", "timeout": "60", "fail_closed": "1", "_csrf": token,
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "guard.sh" in c.get("/plugins").text

    resp = c.post("/plugins/hooks/auto-accept", data={"enable": "1", "_csrf": token},
                  follow_redirects=False)
    assert resp.status_code == 303
    assert hsvc.get_auto_accept() is True

    resp = c.post("/plugins/hooks/delete", data={
        "event": "pre_tool_call", "command": "guard.sh", "_csrf": token},
        follow_redirects=False)
    assert resp.status_code == 303
    assert hsvc.list_hooks() == []


def test_web_plugins_requires_login(client):
    resp = client.get("/plugins", follow_redirects=False)
    assert resp.status_code == 302
