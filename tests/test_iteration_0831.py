"""0.8.31 迭代回归：插件跨平台安装 / 嵌套插件 / 状态缓存 / 工作区基本盘操作 /
全局搜索 / 记忆连接测试 / 渠道稳定度标记。

背景：0.8.30 实机排查发现四类问题——
1) 插件安装命令用 shlex.quote 拼串 + shell=True，Windows cmd 不认 POSIX 单引号必挂；
2) 记忆 provider 依赖安装是纯 POSIX shell，且 API Key 从不校验；
3) 顶栏 pill/服务页轮询每次现跑 hermes 子进程、文件页每次全树遍历（卡顿主因）；
4) 文件管理器缺重命名/删除/复制/新建等基本盘。
"""
from __future__ import annotations

import os
import sys

import pytest

from app.hermes import engineering_service as eng
from app.hermes import plugins_service as psvc
from app.hermes import workspace_service as ws
from tests.conftest import csrf_of


# ---------------------------------------------------------------------------
# W1 插件安装：跨平台命令 + --enable + 收尾兜底白名单
# ---------------------------------------------------------------------------

def test_install_command_platform_safe(hermes_home, monkeypatch):
    from app.hermes import paths as paths_module
    from app.hermes.paths import HermesPaths

    stub = hermes_home.home / "bin" / ("hermes.exe" if sys.platform == "win32" else "hermes")
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("stub", encoding="utf-8")
    paths_module.set_override(HermesPaths(home=hermes_home.home, bin=str(stub)))
    try:
        cmds = []
        monkeypatch.setattr(
            psvc.installer, "submit",
            lambda kind, command, shell=True, cwd=None: cmds.append(command) or 7)
        psvc.install("sample-plugin")
        assert len(cmds) == 1
        cmd = cmds[0]
        assert "--enable" in cmd, "后台任务无 TTY，官方 Enable 提示默认 No，必须显式 --enable"
        assert "plugins install sample-plugin" in cmd
        if sys.platform == "win32":
            assert "'" not in cmd, "Windows cmd 不认 POSIX 单引号（0.8.30 必挂根因）"
    finally:
        paths_module.set_override(None)


def test_nested_plugin_visible_and_auto_loaded(hermes_home):
    d = hermes_home.home / "plugins" / "memory" / "agentmemory"
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text("name: nested-mem\nversion: 1.0\n", encoding="utf-8")
    views = {v.name: v for v in psvc.list_plugins()}
    assert "nested-mem" in views, "只扫一层 glob 的话嵌套插件永远不可见（实机踩过）"
    v = views["nested-mem"]
    assert v.auto_loaded and v.enabled and v.dir == "memory/agentmemory"


def test_remove_nested_plugin(hermes_home):
    d = hermes_home.home / "plugins" / "memory" / "agentmemory"
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text("name: nested-mem\n", encoding="utf-8")
    psvc.remove_plugin("nested-mem")
    assert not d.exists()


def test_finish_job_enables_installed_plugin(hermes_home):
    from app.core import db
    from app.hermes import jobs

    d = hermes_home.home / "plugins" / "sample-plugin"
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text("name: sample-plugin\n", encoding="utf-8")
    job_id = db.execute(
        "INSERT INTO job_runs (kind, command, status, log_path) VALUES (?,?,?,?)",
        ("plugin_install", '"hermes" plugins install sample-plugin --enable',
         "running", "jobs/job-T.log"))
    jobs._finish_job(job_id, "plugin_install", 0)
    assert "sample-plugin" in psvc.enabled_names(), "CLI 不认 --enable 时的兜底白名单"


# ---------------------------------------------------------------------------
# W5 supervisor 状态缓存
# ---------------------------------------------------------------------------

def test_status_cache_hits_and_invalidation(tmp_path, monkeypatch):
    from app.hermes import supervisor
    from app.hermes.paths import HermesPaths

    calls = []

    def fake_probe(paths):
        calls.append(1)
        return supervisor.GatewayStatus(running=False, detail="stub")

    monkeypatch.setattr(supervisor, "_probe_status", fake_probe)
    paths = HermesPaths(home=tmp_path / "h", bin=None)
    supervisor.invalidate_status_cache()
    s1 = supervisor.status(paths)
    s2 = supervisor.status(paths)
    assert len(calls) == 1, "TTL 内第二次调用应命中缓存（只探测一次）"
    assert s1 is s2
    supervisor.invalidate_status_cache()
    supervisor.status(paths)
    assert len(calls) == 2, "失效后应重新探测"


def test_status_force_bypasses_cache(tmp_path, monkeypatch):
    from app.hermes import supervisor
    from app.hermes.paths import HermesPaths

    calls = []

    def fake_probe(paths):
        calls.append(1)
        return supervisor.GatewayStatus(running=None)

    monkeypatch.setattr(supervisor, "_probe_status", fake_probe)
    paths = HermesPaths(home=tmp_path / "h", bin=None)
    supervisor.invalidate_status_cache()
    supervisor.status(paths)
    supervisor.status(paths, force=True)
    assert len(calls) == 2, "体检页 force=True 必须真实时探测"


def test_version_cache(tmp_path, monkeypatch):
    from app.hermes import supervisor
    from app.hermes.paths import HermesPaths

    calls = []

    def fake_run_cli(paths, *args):
        calls.append(1)
        return 0, "hermes 1.2.3"

    monkeypatch.setattr(supervisor, "_run_cli", fake_run_cli)
    paths = HermesPaths(home=tmp_path / "h", bin="stub")
    supervisor.invalidate_status_cache()
    assert supervisor.version(paths) == "hermes 1.2.3"
    assert supervisor.version(paths) == "hermes 1.2.3"
    assert len(calls) == 1, "版本缓存 300s 内只跑一次 CLI"


# ---------------------------------------------------------------------------
# W6/W13/W15 工作区：计数缓存、基本盘操作、全局搜索
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path):
    w = tmp_path / "hermes-workspace"
    s = eng.EngSettings(workspace=str(w))
    eng.save_settings(s)
    eng.init_workspace(s)
    ws.invalidate_caches()
    return w


def test_counts_cache_invalidated_by_writes(workspace):
    (workspace / "downloads" / "a.png").write_bytes(b"x")
    before = dict((k, n) for k, _l, _e, n in ws.category_counts())
    assert before["images"] == 1
    (workspace / "downloads" / "b.png").write_bytes(b"x")
    assert dict((k, n) for k, _l, _e, n in ws.category_counts())["images"] == 1, \
        "直接写盘不经服务层：TTL 内允许命中缓存"
    ws.upload("c.png", b"x")
    assert dict((k, n) for k, _l, _e, n in ws.category_counts())["images"] == 3, \
        "服务层写操作必须主动失效缓存（a+b+c 三张）"


def test_rename_entry(workspace):
    f = workspace / "downloads" / "note.txt"
    f.write_text("hi", encoding="utf-8")
    rel = ws.rename_entry("downloads/note.txt", "renamed.txt")
    assert rel == "downloads/renamed.txt" and not f.exists()
    # 同名 = 无变化，原样返回（不算错误）
    assert ws.rename_entry("downloads/renamed.txt", "renamed.txt") == rel
    with pytest.raises(ws.UnsafePath):
        ws.rename_entry("downloads", "evil")  # 规范目录受保护


def test_rename_rejects_existing_target(workspace):
    (workspace / "downloads" / "a.txt").write_text("a", encoding="utf-8")
    (workspace / "downloads" / "b.txt").write_text("b", encoding="utf-8")
    with pytest.raises(ws.WorkspaceError):
        ws.rename_entry("downloads/a.txt", "b.txt")  # 永不静默覆盖


def test_delete_restore_roundtrip(workspace):
    f = workspace / "downloads" / "doomed.txt"
    f.write_text("x", encoding="utf-8")
    trash_rel = ws.delete_entry("downloads/doomed.txt")
    assert trash_rel.startswith(".trash/doomed.txt-")
    assert not f.exists()
    rel = ws.restore_entry(trash_rel)
    assert rel == "downloads/doomed.txt" and f.exists()
    with pytest.raises(ws.UnsafePath):
        ws.restore_entry("downloads/doomed.txt")  # 回收站外的不能"还原"
    with pytest.raises(ws.UnsafePath):
        ws.delete_entry("projects")  # 规范目录受保护


def test_copy_entry(workspace):
    f = workspace / "downloads" / "orig.txt"
    f.write_text("data", encoding="utf-8")
    rel = ws.copy_entry("downloads/orig.txt")
    assert rel == "downloads/orig-副本.txt"
    d = workspace / "downloads" / "projdir"
    d.mkdir()
    rel2 = ws.copy_entry("downloads/projdir")
    assert rel2 == "downloads/projdir-副本" and (workspace / rel2).is_dir()


def test_mkdir_mkfile_unique(workspace):
    rel = ws.mkdir_entry("downloads", "newdir")
    assert (workspace / rel).is_dir()
    rel2 = ws.mkfile_entry("downloads", "new.txt")
    assert (workspace / rel2).is_file()
    rel3 = ws.mkfile_entry("downloads", "new.txt")
    assert rel3 != rel2 and rel3.startswith("downloads/new-")


def test_move_entry_guards(workspace):
    f = workspace / "downloads" / "mv.txt"
    f.write_text("x", encoding="utf-8")
    rel = ws.move_entry("downloads/mv.txt", "documents")
    assert rel == "documents/mv.txt" and not f.exists()
    with pytest.raises(ws.UnsafePath):
        ws.move_entry("projects", "documents")  # 规范目录不可移出
    with pytest.raises(ws.WorkspaceError):
        ws.move_entry("documents/mv.txt", "downloads/nowhere")  # 目标目录不存在


def test_search_by_name(workspace):
    (workspace / "downloads" / "alpha-report.txt").write_text("1", encoding="utf-8")
    (workspace / "downloads" / "beta.txt").write_text("2", encoding="utf-8")
    hits = ws.search("alpha")
    assert [e.rel for e in hits] == ["downloads/alpha-report.txt"]
    assert ws.search("  ") == []
    assert all(not e.rel.startswith(".") for e in ws.search("txt"))


def test_collection_view_cached_and_invalidated(workspace):
    """集合/最近视图与计数同走缓存：写操作失效，直接写盘 TTL 内允许命中。"""
    (workspace / "downloads" / "a.png").write_bytes(b"x")
    first = ws.list_collection("images")
    assert [e.name for e in first] == ["a.png"]
    (workspace / "downloads" / "b.png").write_bytes(b"x")
    assert len(ws.list_collection("images")) == 1, "直接写盘：TTL 内允许命中缓存"
    ws.upload("c.png", b"x")
    # 时间序要能分辨，就得真把 mtime 拉开：三个文件在同一时钟 tick 内写完时，
    # “按时间倒序”本身没有定义（以前靠进盘顺序碰运气）。
    for name, stamp in (("a.png", 100), ("b.png", 200), ("c.png", 300)):
        os.utime(workspace / "downloads" / name, (stamp, stamp))
    ws.invalidate_caches()
    assert [e.name for e in ws.list_collection("images")] == ["c.png", "b.png", "a.png"], \
        "服务层写操作必须主动失效集合缓存（视图按时间倒序）"
    recent_names = {e.name for e in ws.list_recent()}
    assert {"a.png", "b.png", "c.png"} <= recent_names


# ---------------------------------------------------------------------------
# 文件工作台路由：新端点（rename/delete/new/search/batch/move dest）
# ---------------------------------------------------------------------------

def test_router_file_ops_roundtrip(client, logged_in, tmp_path):
    from pathlib import Path

    w = tmp_path / "ws-router"
    s = eng.EngSettings(workspace=str(w))
    eng.save_settings(s)
    eng.init_workspace(s)
    ws.invalidate_caches()
    token = csrf_of(logged_in)
    assert Path(ws.root()) == w.resolve()

    r = logged_in.post("/files/new", data={"dir": "downloads", "kind": "file",
                                           "name": "made.txt", "_csrf": token})
    assert r.status_code == 200 and (w / "downloads" / "made.txt").is_file()
    r = logged_in.post("/files/rename", data={"path": "downloads/made.txt",
                                              "name": "renamed.txt", "_csrf": token})
    assert r.status_code == 200 and (w / "downloads" / "renamed.txt").is_file()
    r = logged_in.post("/files/copy", data={"path": "downloads/renamed.txt",
                                            "_csrf": token})
    assert r.status_code == 200 and (w / "downloads" / "renamed-副本.txt").is_file()
    r = logged_in.post("/files/batch", data={"op": "delete",
                                             "paths": ["downloads/renamed.txt",
                                                       "downloads/renamed-副本.txt"],
                                             "_csrf": token})
    assert r.status_code == 200
    assert not (w / "downloads" / "renamed.txt").exists()
    r = logged_in.get("/files", params={"cat": "trash"})
    assert r.status_code == 200 and "renamed.txt" in r.text
    trash_items = ws.trash_list()
    assert len(trash_items) == 2
    r = logged_in.post("/files/restore", data={"path": trash_items[0].rel,
                                               "_csrf": token})
    assert r.status_code == 200
    origins = [w / "downloads" / "renamed.txt", w / "downloads" / "renamed-副本.txt"]
    assert any(p.exists() for p in origins), "还原应回到删除时的原位置"


def test_router_move_with_dest(client, logged_in, tmp_path):
    w = tmp_path / "ws-router2"
    s = eng.EngSettings(workspace=str(w))
    eng.save_settings(s)
    eng.init_workspace(s)
    ws.invalidate_caches()
    (w / "downloads" / "d.txt").write_text("x", encoding="utf-8")
    token = csrf_of(logged_in)
    r = logged_in.post("/files/move", data={"path": "downloads/d.txt",
                                            "dest": "documents", "_csrf": token})
    assert r.status_code == 200
    assert (w / "documents" / "d.txt").exists()


def test_search_page(client, logged_in, tmp_path):
    w = tmp_path / "ws-router3"
    s = eng.EngSettings(workspace=str(w))
    eng.save_settings(s)
    eng.init_workspace(s)
    ws.invalidate_caches()
    (w / "downloads" / "findme.txt").write_text("x", encoding="utf-8")
    r = logged_in.get("/files", params={"q": "findme"})
    assert r.status_code == 200 and "findme.txt" in r.text


def test_watch_endpoint_aggregate_view_closes(client, logged_in):
    r = logged_in.get("/files/watch", params={"cat": "recent"})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "bye" in r.text


# ---------------------------------------------------------------------------
# W9 记忆连接测试
# ---------------------------------------------------------------------------

def test_memory_test_connection_skips_local(hermes_home):
    from app.hermes import memory_service as mem

    assert mem.test_connection("holographic", {})["level"] == "skip"
    assert mem.test_connection("hindsight", {"mode": "local"})["level"] == "skip"
    assert mem.test_connection("byterover", {})["level"] == "skip"


def test_memory_test_connection_unreachable(hermes_home):
    from app.hermes import memory_service as mem

    r = mem.test_connection("openviking",
                            {"OPENVIKING_ENDPOINT": "http://127.0.0.1:9"})
    assert r["level"] == "error"


def test_memory_test_connection_bad_scheme(hermes_home):
    from app.hermes import memory_service as mem

    r = mem.test_connection("openviking", {"OPENVIKING_ENDPOINT": "ftp://x"})
    assert r["level"] == "error"


# ---------------------------------------------------------------------------
# W11 渠道稳定度标记与推荐排序
# ---------------------------------------------------------------------------

def test_channel_stability_fields_present():
    from app.hermes.schema import PLATFORMS

    assert PLATFORMS["telegram"].recommended
    assert PLATFORMS["feishu"].recommended
    assert PLATFORMS["dingtalk"].recommended
    assert PLATFORMS["wecom"].recommended
    assert PLATFORMS["whatsapp"].stability == "bridge"
    assert PLATFORMS["signal"].stability == "selfhost"
    assert PLATFORMS["webhook"].stability == "local"
    for p in PLATFORMS.values():
        assert p.stability in ("official", "bridge", "selfhost", "local")


def test_channel_list_recommended_first(client, logged_in):
    page = logged_in.get("/channels")
    assert page.status_code == 200
    text = page.text
    feishu, whatsapp = text.find("飞书 / Lark"), text.find("WhatsApp")
    assert feishu != -1 and whatsapp != -1
    assert feishu < whatsapp, "推荐渠道应排在卡片墙前列"


def test_memory_router_csrf_enforced(client, logged_in):
    # 0.8.31 起 memory 路由与其余路由一致挂 csrf_guard：无令牌 POST → 403
    r = logged_in.post("/memory/builtin", data={"memory_char_limit": "8000"})
    assert r.status_code == 403
