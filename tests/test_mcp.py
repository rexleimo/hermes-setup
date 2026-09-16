"""MCP 服务管理：service 级 CRUD / 官方目录 + Web 级流程。"""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from app.hermes import mcp_service as svc
from app.hermes.config_store import get_path, load_config
from tests.conftest import csrf_of

_yaml = YAML()
_yaml.preserve_quotes = True


def _config_dict(paths) -> dict:
    data = _yaml.load(paths.config_yaml.read_text(encoding="utf-8"))
    return dict(data) if data else {}


def _seed_catalog(paths, entries: dict[str, str]) -> None:
    """伪造 agent_repo/optional-mcps：entries = {id: manifest yaml 文本}。"""
    base = paths.agent_repo / "optional-mcps"
    for cid, manifest in entries.items():
        d = base / cid
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.yaml").write_text(manifest, encoding="utf-8")


# ---------------------------------------------------------------------------
# service 级
# ---------------------------------------------------------------------------

def test_create_stdio_server(hermes_home):
    inp = svc.McpServerInput(
        transport="stdio", command="uvx", args_text="code-review-graph\nserve",
        env_text="DEBUG=1", secret_env_text="API_KEY=sk-secret-1",
    )
    notes = svc.create_server("review-graph", inp)
    node = get_path(load_config(hermes_home), "mcp_servers.review-graph")
    assert node["command"] == "uvx"
    assert list(node["args"]) == ["code-review-graph", "serve"]
    assert node["env"]["DEBUG"] == "1"
    # 密钥：值进 .env，配置里只留 ${VAR} 占位符
    var = f"MCP_REVIEW_GRAPH_API_KEY"
    assert node["env"]["API_KEY"] == "${" + var + "}"
    assert "sk-secret-1" in hermes_home.env_file.read_text(encoding="utf-8")
    assert any("API_KEY" in n for n in notes)
    assert svc.get_server("review-graph").enabled is True
    assert svc.get_server("review-graph").transport == "stdio"


def test_create_http_server_with_oauth_and_sse(hermes_home):
    inp = svc.McpServerInput(
        transport="http", url="https://mcp.example.com/mcp",
        use_sse=True, auth_oauth=True,
        secret_headers_text="Authorization=Bearer tok-9",
    )
    svc.create_server("remote", inp)
    node = get_path(load_config(hermes_home), "mcp_servers.remote")
    assert node["url"] == "https://mcp.example.com/mcp"
    assert node["transport"] == "sse"
    assert node["auth"] == "oauth"
    assert node["headers"]["Authorization"] == "${MCP_REMOTE_AUTHORIZATION}"
    view = svc.get_server("remote")
    assert view.transport == "sse" and view.auth_oauth


def test_create_validation(hermes_home):
    import pytest

    with pytest.raises(svc.McpError):
        svc.create_server("9bad", svc.McpServerInput(transport="stdio", command="x"))
    with pytest.raises(svc.McpError, match="启动命令"):
        svc.create_server("ok-name", svc.McpServerInput(transport="stdio"))
    with pytest.raises(svc.McpError, match="URL"):
        svc.create_server("ok-name", svc.McpServerInput(transport="http", url="ftp://x"))
    with pytest.raises(svc.McpError, match="已存在"):
        svc.create_server("dup", svc.McpServerInput(transport="http", url="https://a.b/c"))
        svc.create_server("dup", svc.McpServerInput(transport="http", url="https://a.b/c"))


def test_update_preserves_secret_refs(hermes_home):
    svc.create_server("app", svc.McpServerInput(
        transport="stdio", command="node", secret_env_text="TOKEN=t-abc"))
    # 表单回显不含密钥明文，更新时不应丢掉 ${VAR} 引用
    svc.update_server("app", svc.McpServerInput(
        transport="stdio", command="node", args_text="serve", env_text="DEBUG=1"))
    node = get_path(load_config(hermes_home), "mcp_servers.app")
    assert node["env"]["TOKEN"] == "${MCP_APP_TOKEN}"
    assert node["env"]["DEBUG"] == "1"
    assert "t-abc" in hermes_home.env_file.read_text(encoding="utf-8")


def test_toggle_and_delete(hermes_home):
    svc.create_server("tmp", svc.McpServerInput(
        transport="http", url="https://x.example/mcp", secret_env_text="K=v-1"))
    svc.set_enabled("tmp", False)
    assert svc.get_server("tmp").enabled is False
    assert get_path(load_config(hermes_home), "mcp_servers.tmp.enabled") is False

    notes = svc.delete_server("tmp")
    servers = get_path(load_config(hermes_home), "mcp_servers", {})
    assert "tmp" not in servers
    assert "K=v-1" not in hermes_home.env_file.read_text(encoding="utf-8")
    assert any(".env" in n for n in notes)


def test_delete_agentmemory_guarded(hermes_home):
    import pytest

    with pytest.raises(svc.McpError, match="记忆系统"):
        svc.delete_server("agentmemory")


def test_catalog_parse_and_install(hermes_home):
    _seed_catalog(hermes_home, {
        "context7": """
name: context7
description: Up-to-date library docs.
transport:
  type: http
  url: https://mcp.context7.com/mcp
auth:
  type: none
post_install: No credentials needed.
""",
        "local-tool": """
name: local-tool
description: A local stdio server.
transport:
  type: stdio
  command: uvx
  args: [local-tool, serve]
""",
    })
    entries = svc.catalog()
    assert [e.id for e in entries] == ["context7", "local-tool"]  # 热门 context7 排前
    assert entries[0].hot and not entries[1].hot

    svc.install_from_catalog("context7")
    node = get_path(load_config(hermes_home), "mcp_servers.context7")
    assert node["url"] == "https://mcp.context7.com/mcp"
    assert "trust" not in node  # 与官方 install 行为对齐，不落 trust 键

    svc.install_from_catalog("local-tool")
    node = get_path(load_config(hermes_home), "mcp_servers.local-tool")
    assert node["command"] == "uvx"

    import pytest

    with pytest.raises(svc.McpError, match="已在"):
        svc.install_from_catalog("context7")
    with pytest.raises(svc.McpError, match="不存在"):
        svc.install_from_catalog("nope")


def test_view_masking_properties(hermes_home):
    svc.create_server("mix", svc.McpServerInput(
        transport="stdio", command="n",
        env_text="PLAIN=1", secret_env_text="S=vv"))
    v = svc.get_server("mix")
    assert v.env_plain == {"PLAIN": "1"}
    assert v.env_secrets == {"S": "${MCP_MIX_S}"}
    assert "S=v" not in v.env_plain_text


# ---------------------------------------------------------------------------
# Web 级
# ---------------------------------------------------------------------------

def test_web_mcp_page_and_crud(logged_in):
    c = logged_in
    token = csrf_of(c)

    resp = c.get("/mcp")
    assert resp.status_code == 200

    # 新建（表单回显错误）
    resp = c.post("/mcp/new", data={"name": "bad name!", "transport": "stdio",
                                    "_csrf": token})
    assert resp.status_code == 400
    assert "名称" in resp.text

    resp = c.post("/mcp/new", data={
        "name": "web-server", "transport": "stdio", "command": "uvx",
        "args_text": "demo\nrun", "secret_env_text": "KEY=wk-1", "_csrf": token,
    }, follow_redirects=False)
    assert resp.status_code == 303

    resp = c.get("/mcp/web-server")
    assert resp.status_code == 200
    assert "web-server" in resp.text

    # 列表页出现
    assert "web-server" in c.get("/mcp").text

    # 启停
    resp = c.post("/mcp/web-server/toggle", data={"enable": "0", "_csrf": token},
                  follow_redirects=False)
    assert resp.status_code == 303
    assert svc.get_server("web-server").enabled is False

    # 更新
    resp = c.post("/mcp/web-server/update", data={
        "transport": "stdio", "command": "node", "enabled": "1", "_csrf": token,
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert svc.get_server("web-server").command == "node"

    # 删除（确认词）
    resp = c.post("/mcp/web-server/delete", data={"confirm": "wrong", "_csrf": token})
    assert resp.status_code == 400
    resp = c.post("/mcp/web-server/delete", data={"confirm": "web-server", "_csrf": token},
                  follow_redirects=False)
    assert resp.status_code == 303
    assert "web-server" not in [s.name for s in svc.list_servers()]


def test_web_mcp_catalog_flow(logged_in, hermes_home):
    _seed_catalog(hermes_home, {
        "notion": """
name: notion
description: Pages and databases from Notion.
transport:
  type: http
  url: https://mcp.notion.com/mcp
auth:
  type: oauth
""",
    })
    c = logged_in
    token = csrf_of(c)

    page = c.get("/mcp").text
    assert "官方热门目录" in page and "notion" in page

    resp = c.post("/mcp/catalog/notion/add", data={"_csrf": token},
                  follow_redirects=False)
    assert resp.status_code == 303
    assert "notion" in [s.name for s in svc.list_servers()]

    # 已添加后不再重复出现在「一键添加」态
    page = c.get("/mcp").text
    assert "/mcp/catalog/notion/add" not in page


def test_web_mcp_requires_admin(client):
    """未登录访问 → 重定向登录页（认证守卫）。"""
    resp = client.get("/mcp", follow_redirects=False)
    assert resp.status_code == 302
