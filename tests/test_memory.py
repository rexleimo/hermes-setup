"""记忆系统模块测试：内置调优、各 provider 自动配置、状态读取、Web 流程。"""
from __future__ import annotations

from app.hermes import memory_service as mem
from app.hermes.config_store import EnvStore, get_path, load_config, load_json_opt


# ---------------------------------------------------------------------------
# 内置调优
# ---------------------------------------------------------------------------

def test_save_builtin_updates_config(hermes_home):
    changes = mem.save_builtin(
        memory_enabled=True, user_profile_enabled=True,
        memory_char_limit=16000, user_char_limit=8000,
        nudge_interval=5, write_approval=True,
        context_file_max_chars=400000)
    config = load_config()
    assert get_path(config, "memory.memory_char_limit") == 16000
    assert get_path(config, "memory.user_char_limit") == 8000
    assert get_path(config, "memory.write_approval") is True
    assert get_path(config, "context_file_max_chars") == 400000


def test_save_builtin_rejects_out_of_range(hermes_home):
    import pytest

    with pytest.raises(mem.MemoryError):
        mem.save_builtin(memory_enabled=True, user_profile_enabled=True,
                         memory_char_limit=100, user_char_limit=1375,
                         nudge_interval=10, write_approval=False,
                         context_file_max_chars=None)
    with pytest.raises(mem.MemoryError):
        mem.save_builtin(memory_enabled=True, user_profile_enabled=True,
                         memory_char_limit=2200, user_char_limit=1375,
                         nudge_interval=10, write_approval=False,
                         context_file_max_chars=999)


def test_status_defaults(hermes_home):
    st = mem.get_status()
    assert st.provider == ""
    assert st.builtin["memory_char_limit"] == 2200
    assert st.mcp_agentmemory is False


# ---------------------------------------------------------------------------
# agentmemory
# ---------------------------------------------------------------------------

def test_agentmemory_mcp_mode(hermes_home):
    mem.apply_provider("agentmemory", {"mode": "mcp", "agentmemory_api_url": ""})
    config = load_config()
    mcp = get_path(config, "mcp_servers.agentmemory")
    assert mcp["command"] == "npx"
    assert mcp["args"] == ["agentmemory-mcp"]
    assert "env" not in mcp
    assert get_path(config, "memory.provider") == "agentmemory"


def test_agentmemory_mcp_with_remote_url(hermes_home):
    mem.apply_provider("agentmemory", {"mode": "mcp", "deployment": "remote",
                                       "agentmemory_api_url": "http://10.0.0.9:3113"})
    mcp = get_path(load_config(), "mcp_servers.agentmemory")
    assert mcp["env"]["AGENTMEMORY_API_URL"] == "http://10.0.0.9:3113"


def test_agentmemory_local_mode_ignores_url(hermes_home):
    mem.apply_provider("agentmemory", {"mode": "mcp", "deployment": "local",
                                       "agentmemory_api_url": "http://should-be-ignored"})
    mcp = get_path(load_config(), "mcp_servers.agentmemory")
    assert "env" not in mcp


def test_agentmemory_remote_requires_url(hermes_home):
    import pytest

    with pytest.raises(mem.MemoryError):
        mem.apply_provider("agentmemory", {"mode": "mcp", "deployment": "remote",
                                           "agentmemory_api_url": ""})


def test_agentmemory_plugin_mode_switch(hermes_home):
    mem.apply_provider("agentmemory", {"mode": "mcp"})
    mem.apply_provider("agentmemory", {"mode": "plugin"})
    config = load_config()
    assert "agentmemory" not in (get_path(config, "mcp_servers", {}) or {})
    assert get_path(config, "memory.provider") == "agentmemory"


# ---------------------------------------------------------------------------
# 各 provider 的配置写入
# ---------------------------------------------------------------------------

def test_mem0_platform(hermes_home):
    mem.apply_provider("mem0", {"mode": "platform", "MEM0_API_KEY": "mk-1",
                                "user_id": "u1"})
    assert EnvStore.load().get("MEM0_API_KEY") == "mk-1"
    cfg = load_json_opt(None, "mem0.json")
    assert cfg["user_id"] == "u1" and "host" not in cfg
    assert get_path(load_config(), "memory.provider") == "mem0"


def test_mem0_selfhosted_requires_host(hermes_home):
    import pytest

    with pytest.raises(mem.MemoryError):
        mem.apply_provider("mem0", {"mode": "selfhosted", "MEM0_API_KEY": "k"})


def test_mem0_selfhosted(hermes_home):
    mem.apply_provider("mem0", {"mode": "selfhosted", "MEM0_API_KEY": "admin",
                                "host": "http://localhost:8888"})
    cfg = load_json_opt(None, "mem0.json")
    assert cfg["host"] == "http://localhost:8888"
    assert cfg["api_key"] == "admin"


def test_supermemory_selfhost(hermes_home):
    mem.apply_provider("supermemory", {"SUPERMEMORY_API_KEY": "sm-1",
                                       "base_url": "http://localhost:6767",
                                       "container_tag": "hermes",
                                       "auto_recall": "on", "auto_capture": "off"})
    assert EnvStore.load().get("SUPERMEMORY_API_KEY") == "sm-1"
    cfg = load_json_opt(None, "supermemory.json")
    assert cfg["base_url"] == "http://localhost:6767"
    assert cfg["auto_capture"] is False and cfg["auto_recall"] is True


def test_openviking_env_provider(hermes_home):
    mem.apply_provider("openviking", {"OPENVIKING_ENDPOINT": "http://127.0.0.1:1933",
                                      "OPENVIKING_ACCOUNT": "default",
                                      "OPENVIKING_USER": "default"})
    env = EnvStore.load()
    assert env.get("OPENVIKING_ENDPOINT") == "http://127.0.0.1:1933"
    assert get_path(load_config(), "memory.provider") == "openviking"


def test_hindsight_config_file(hermes_home):
    mem.apply_provider("hindsight", {"HINDSIGHT_API_KEY": "hs-1", "mode": "cloud",
                                     "bank_id": "mybank", "recall_budget": "high",
                                     "memory_mode": "hybrid"})
    cfg = load_json_opt(None, "hindsight/config.json")
    assert cfg["bank_id"] == "mybank" and cfg["recall_budget"] == "high"
    assert EnvStore.load().get("HINDSIGHT_API_KEY") == "hs-1"


def test_holographic_config_yaml(hermes_home):
    mem.apply_provider("holographic", {"auto_extract": "on", "default_trust": "0.8"})
    config = load_config()
    assert get_path(config, "plugins.hermes-memory-store.auto_extract") is True
    assert get_path(config, "plugins.hermes-memory-store.default_trust") == 0.8
    assert get_path(config, "memory.provider") == "holographic"


def test_honcho_json_merge_preserves_existing(hermes_home):
    mem.apply_provider("honcho", {"peerName": "eri", "workspace": "hermes"})
    # 第二次带 apiKey，第一次写入的 hosts 键应保留
    mem.apply_provider("honcho", {"peerName": "eri", "workspace": "hermes",
                                  "apiKey": "hc-1"})
    cfg = load_json_opt(None, "honcho.json")
    assert cfg["apiKey"] == "hc-1"
    assert cfg["hosts"]["hermes"]["peerName"] == "eri"
    assert cfg["hosts"]["hermes"]["enabled"] is True


def test_required_field_enforced(hermes_home):
    import pytest

    with pytest.raises(mem.MemoryError):
        mem.apply_provider("retaindb", {"RETAINDB_API_KEY": ""})


# ---------------------------------------------------------------------------
# 关闭外置方案
# ---------------------------------------------------------------------------

def test_disable_removes_provider_and_mcp(hermes_home):
    mem.apply_provider("agentmemory", {"mode": "mcp"})
    changes = mem.disable_provider(keep_mcp=False)
    config = load_config()
    assert get_path(config, "memory.provider", "") == ""
    assert "agentmemory" not in (get_path(config, "mcp_servers", {}) or {})
    assert any("MCP" in c for c in changes)


# ---------------------------------------------------------------------------
# Web 流程
# ---------------------------------------------------------------------------

def test_memory_page_and_flows(logged_in):
    from tests.conftest import csrf_of

    c = logged_in
    assert c.get("/memory").status_code == 200
    # provider 表单片段
    assert "Mem0" in c.get("/memory/provider/mem0").text
    assert c.get("/memory/provider/nope").status_code == 404
    token = csrf_of(c)
    # 内置调优
    resp = c.post("/memory/builtin", data={
        "memory_enabled": "on", "user_profile_enabled": "on",
        "write_approval": "off", "memory_char_limit": "8000",
        "user_char_limit": "4000", "nudge_interval": "10",
        "context_file_max_chars": "", "_csrf": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert get_path(load_config(), "memory.memory_char_limit") == 8000
    # 应用 supermemory
    resp = c.post("/memory/apply/supermemory", data={
        "SUPERMEMORY_API_KEY": "sm-x", "container_tag": "hermes",
        "_csrf": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert get_path(load_config(), "memory.provider") == "supermemory"
    # 页面反映使用中状态
    assert "使用中" in c.get("/memory").text
    # 审计留痕
    assert "memory_provider_apply" in c.get("/audit").text


def test_provider_counts_and_descriptors():
    providers = mem.list_providers()
    assert len(providers) == 9
    ids = {p.id for p in providers}
    assert {"agentmemory", "mem0", "supermemory", "openviking", "hindsight",
            "holographic", "retaindb", "byterover", "honcho"} == ids


def test_deps_commands(hermes_home):
    cmd = mem.deps_command("honcho")
    assert "honcho-ai" in cmd and ".venv/bin/pip" in cmd
    # hindsight 依赖随模式变化
    assert "hindsight-all" in mem.deps_command("hindsight", {"mode": "local"})
    assert "hindsight-client" in mem.deps_command("hindsight", {"mode": "cloud"})
    # 无依赖的方案返回空
    assert mem.deps_command("holographic") == ""
    import os
    import subprocess

    if os.name != "posix":
        return  # bash 语法检查仅限 POSIX；命令拼接正确性已由上方断言覆盖

    for pid, values in [("honcho", {}), ("hindsight", {"mode": "local"}),
                        ("mem0", {"mode": "platform"})]:
        r = subprocess.run(["bash", "-n", "-c", mem.deps_command(pid, values)])
        assert r.returncode == 0


def test_submit_install_job_validation(hermes_home):
    import pytest

    from app.hermes import installer

    with pytest.raises(mem.MemoryError):
        mem.submit_install_job("holographic")   # 无依赖无任务
