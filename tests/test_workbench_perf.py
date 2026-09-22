"""文件工作台性能护栏（0.8.34）：每请求的重活必须有上界。

线上症状：工作区文件一多，/files?cat=images&view=grid 把服务打到完全进不去。
根因不是“某条查询慢”，而是每请求的固定开销没有上界：

  1. 一次渲染遍历全树 4 遍（侧栏计数 / 位置计数 / 集合 / 健康检查各一遍），
     `rglob("*")` 还会先钻进 node_modules/.git 再把结果丢掉；
  2. 缓存到期时没有合流，N 个并发请求 = N 次全树重算（stampede）；
  3. 页面一次吐出全部条目（1000 张卡片），每张卡片又是一个 <img src>，
     浏览器并发拉 1000 个 /files/thumb，每个首次命中都要在服务端解码原图；
  4. 每个带 cookie 的请求都开事务写 session 滑动；SSE 监听长期占用同步 worker。

本文件钉住“有上界”这件事：全树只走一次、缓存并发只算一次、单页有条数上限、
增量端点按 offset 翻页、依赖/隐藏目录不进入统计、缩略图有体积与类型闸门。
"""
from __future__ import annotations

import io
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from app.core import db
from app.hermes import engineering_service as eng
from app.hermes import workspace_service as ws

PNG_1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d7b5cf3101000000008a696d0a0000000049454e44ae426082")


@pytest.fixture()
def workspace(logged_in, tmp_path):
    ws_root = tmp_path / "hermes-workspace"
    s = eng.EngSettings(workspace=str(ws_root))
    eng.save_settings(s)
    eng.init_workspace(s)
    return ws_root


def _png(side: int = 900) -> bytes:
    img = Image.new("RGB", (side, side // 2), (120, 60, 160))
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


def _make_images(root, n: int, subdir: str = "pictures") -> list:
    d = root / subdir
    d.mkdir(parents=True, exist_ok=True)
    made = []
    for i in range(n):
        p = d / f"photo-{i:03d}.png"
        p.write_bytes(_png())
        made.append(p)
    return made


def _rel(root, path) -> str:
    return path.relative_to(root).as_posix()


class WalkCounter:
    """给 _walk_files 装计数器（全树扫描的唯一入口）。"""

    def __init__(self, monkeypatch):
        self.n = 0
        real = ws._walk_files
        outer = self

        def spy():
            outer.n += 1
            return real()

        monkeypatch.setattr(ws, "_walk_files", spy)


# ---------------------------------------------------------------------------
# 1. 一次渲染只走一次树
# ---------------------------------------------------------------------------

def test_all_aggregations_share_one_walk(workspace, monkeypatch):
    """计数 / 位置 / 集合 / 最近 / 健康检查必须来自同一份索引。

    0.8.33 之前它们各自 rglob 一遍，一个 /files 页面 = 4 次全树。"""
    _make_images(workspace, 4)
    (workspace / "documents" / "a.md").write_text("# a\n")
    counter = WalkCounter(monkeypatch)
    ws.invalidate_caches()

    ws.category_counts()
    ws.location_counts()
    ws.list_collection("images")
    ws.list_recent()
    ws.search("photo")
    ws.list_dir("")
    assert counter.n == 1, f"一次页面渲染走了 {counter.n} 遍全树"


def test_index_is_reused_between_requests(workspace, monkeypatch):
    _make_images(workspace, 3)
    counter = WalkCounter(monkeypatch)
    ws.invalidate_caches()
    for _ in range(5):
        ws.category_counts()
        ws.location_counts()
    assert counter.n == 1, f"{counter.n} 次扫描：索引 TTL 缓存没生效"


# ---------------------------------------------------------------------------
# 2. 缓存到期不放大（single-flight）
# ---------------------------------------------------------------------------

def test_cache_expiry_does_not_stampede(workspace, monkeypatch):
    """TTL=0 时 8 个线程同时到期，只能有 1 个真的重算，其余等它。"""
    _make_images(workspace, 4)
    real = ws._walk_files
    calls = []
    gate = threading.Event()

    def slow_walk():
        calls.append(1)
        gate.wait(3.0)
        return real()

    monkeypatch.setattr(ws, "_walk_files", slow_walk)
    ws.invalidate_caches()          # 真实场景：TTL 到期后第一波并发同时未命中

    threads = [threading.Thread(target=ws.category_counts) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    gate.set()
    for t in threads:
        t.join(15)
    assert not any(t.is_alive() for t in threads), "有线程卡死在缓存合流上"
    assert len(calls) == 1, f"{len(calls)} 次全树重算：并发没有合流"


def test_index_limit_bounds_work(workspace, monkeypatch):
    """索引必须有硬上界：异常工作区（挂在大目录上）不能把内存与遍历时间拖开。"""
    _make_images(workspace, 8)
    monkeypatch.setattr(ws, "INDEX_LIMIT", 3)
    ws.invalidate_caches()
    assert len(ws._index()) <= 3, f"索引 {len(ws._index())} 条，上限没生效"


# ---------------------------------------------------------------------------
# 3. 单页有条数上限 + offset 增量翻页
# ---------------------------------------------------------------------------

def test_paginate_window_and_clamp():
    e = lambda i: ws.Entry(name=f"n{i}", rel=f"n{i}", is_dir=False, size=i, mtime=float(i))
    items = [e(i) for i in range(250)]
    p = ws.paginate(items, offset=120, size=120)
    assert [x.name for x in p.items] == [f"n{i}" for i in range(120, 240)]
    assert p.has_more and p.next_offset == 240
    assert ws.paginate(items, offset=9999, size=120).offset == 0, "越界 offset 必须钳回首页"


def test_collection_respects_url_sort(workspace, logged_in):
    """?cat=images&sort=name 以前拿到的是时间序 —— 视图排序不该由数据层写死。

    路由级断言：数据层支持 sort 不等于页面 follows 地址栏，接线也得钉住。"""
    made = _make_images(workspace, 5)
    for i, p in enumerate(made):        # 拉开 mtime，让两种排序真的不同
        os.utime(p, (1000 + i * 100, 1000 + i * 100))
    ws.invalidate_caches()

    def names(html):
        return re.findall(r'data-name0="([^"]+)"', html)

    by_name = names(logged_in.get("/files", params={"cat": "images", "sort": "name",
                                                    "view": "grid"}).text)
    by_time = names(logged_in.get("/files", params={"cat": "images", "sort": "time",
                                                    "view": "grid"}).text)
    assert by_name == sorted(by_name) and len(by_name) == 5
    assert by_time == list(reversed(by_name)), "时间序应与名字序相反（刚写入的一页）"
    assert [e.name for e in ws.list_collection("images", sort="name")] == sorted(
        e.name for e in ws.list_collection("images", sort="name"))


def test_page_renders_one_slice_and_more_endpoint_pages(workspace, logged_in):
    total = ws.PAGE_SIZE + 5
    _make_images(workspace, total)
    ws.invalidate_caches()

    html = logged_in.get("/files", params={"cat": "images", "sort": "name",
                                           "view": "grid"}).text
    shown = html.count('class="osfm-card-item"')
    assert shown == ws.PAGE_SIZE, f"首屏渲染 {shown} 张卡片，超过单页上限"
    assert f'data-total="{total}"' in html and "加载更多" in html

    more = logged_in.get("/files/more", params={"cat": "images", "sort": "name",
                                                "view": "grid",
                                                "offset": ws.PAGE_SIZE})
    assert more.status_code == 200
    assert more.headers["X-Osfm-More"] == "0"
    assert more.text.count('class="osfm-card-item"') == 5
    assert "<div class=\"e-grid\"" not in more.text, "增量片段不能带容器"

    # 列表视图同样分页，且增量返回 <tr>
    html = logged_in.get("/files", params={"cat": "images", "view": "list"}).text
    assert html.count('class="osfm-card-item"') == ws.PAGE_SIZE
    rows = logged_in.get("/files/more", params={"cat": "images", "view": "list",
                                                "offset": ws.PAGE_SIZE}).text
    assert rows.count("<tr") == 5 and "<table" not in rows

    # 越界 offset 钳回首页，不给空页
    far = logged_in.get("/files", params={"cat": "images", "offset": 9999})
    assert far.status_code == 200
    assert far.text.count('class="osfm-card-item"') == ws.PAGE_SIZE


@pytest.mark.parametrize("here,expect", [
    (f"cat=images&sort=name&view=grid&offset={120}", 4),   # 留在第二页窗口
    ("cat=images&sort=name&view=grid", 120),               # 首页窗口
])
def test_rerender_keeps_offset_window(workspace, logged_in, here, expect):
    """写操作后原地重渲染必须带上 offset：`here` 丢了分页参数就跳回首页。"""
    if ws.PAGE_SIZE != 120:
        pytest.skip("用例里的期望值按 PAGE_SIZE=120 写死")
    made = _make_images(workspace, ws.PAGE_SIZE + 5)
    ws.invalidate_caches()
    from tests.conftest import csrf_of

    victim = made[0] if "offset" in here else made[-1]
    resp = logged_in.post(
        "/files/batch",
        data={"op": "delete", "paths": _rel(workspace, victim), "here": here,
              "_csrf": csrf_of(logged_in)},
        headers={"HX-Request": "true"})
    assert resp.status_code == 200, resp.text
    assert resp.text.count('class="osfm-card-item"') == expect


# ---------------------------------------------------------------------------
# 4. 每请求 DB 写事务有上界（缩略图不能变成写放大器）
# ---------------------------------------------------------------------------

def test_session_slide_writes_are_throttled(logged_in):
    """连续请求最多滑动一次 session：一个页面几百个请求 = 几百次写事务是灾难。"""
    writes = []
    real_execute = db.execute

    def counting(sql, params=()):
        if sql.strip().upper().startswith("UPDATE SESSIONS"):
            writes.append(sql)
        return real_execute(sql, params)

    db.execute = counting
    try:
        for _ in range(6):
            assert logged_in.get("/files").status_code == 200
    finally:
        db.execute = real_execute
    assert len(writes) <= 1, f"6 次请求产生 {len(writes)} 次 session 写入"


def test_session_slide_resumes_after_window(logged_in):
    """节流只改写入频率，不改语义：超过窗口必须重新续期，过期照样失效。"""
    sid = logged_in.cookies.get("hermes_console_session")
    assert sid, "登录后应带 session cookie"
    db.execute("UPDATE sessions SET last_seen_at = datetime('now','-2 minutes') "
               "WHERE id = ?", (sid,))
    assert logged_in.get("/files").status_code == 200
    row = db.query_one("SELECT last_seen_at FROM sessions WHERE id = ?", (sid,))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert row and (now - datetime.strptime(
        row["last_seen_at"], "%Y-%m-%d %H:%M:%S")) < timedelta(seconds=5), \
        "过期窗口后必须把 last_seen 写回去"


# ---------------------------------------------------------------------------
# 5. 遍历边界：依赖目录、隐藏目录、软链不进入统计
# ---------------------------------------------------------------------------

def test_walk_prunes_hidden_and_dependency_dirs(workspace):
    _make_images(workspace, 2)
    junk = workspace / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    for i in range(5):
        (junk / f"leak-{i}.png").write_bytes(PNG_1)
    hidden = workspace / ".cache" / "pics"
    hidden.mkdir(parents=True)
    for i in range(5):
        (hidden / f"hide-{i}.png").write_bytes(PNG_1)
    link = workspace / "selfloop"
    try:
        link.symlink_to(str(workspace))     # 自指软链：不跳过就会无限下钻
    except (OSError, NotImplementedError):
        pass                                # Windows 非开发者模式造不出软链
    ws.invalidate_caches()

    rels = [e.rel for e in ws.list_collection("images")]
    assert len(rels) == 2, f"依赖/隐藏目录被计入统计：{rels}"
    assert all("node_modules" not in r and not r.startswith(".") for r in rels)


# ---------------------------------------------------------------------------
# 6. 缩略图闸门：类型与体积
# ---------------------------------------------------------------------------

def test_list_dir_cache_busts_on_service_write(workspace):
    """目录列表进了缓存 → 服务层写操作必须把它一起作废（不然是“改名后页面不变”）。"""
    pic = workspace / "pictures"
    pic.mkdir(parents=True, exist_ok=True)
    (pic / "a.png").write_bytes(_png(40))
    ws.invalidate_caches()
    assert [e.name for e in ws.list_dir("pictures")] == ["a.png"]
    (pic / "b.png").write_bytes(_png(40))
    assert [e.name for e in ws.list_dir("pictures")] == ["a.png"], "TTL 内命中缓存（预期行为）"
    ws.rename_entry("pictures/b.png", "c.png")
    assert [e.name for e in ws.list_dir("pictures")] == ["a.png", "c.png"], \
        "写操作后目录快照必须作废"


def test_thumb_skips_non_raster_exts(workspace, logged_in):
    """SVG/AVIF 不是 PIL 稳定可解码的位图：网格不该为它们发请求，端点也不该试。"""
    pic = workspace / "pictures"
    (pic / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (pic / "shot.avif").write_bytes(PNG_1)
    (pic / "ok.png").write_bytes(_png())
    ws.invalidate_caches()

    by_name = {e.name: e for e in ws.list_collection("images")}
    assert not by_name["logo.svg"].thumbable and not by_name["shot.avif"].thumbable
    assert by_name["ok.png"].thumbable
    assert logged_in.get("/files/thumb", params={"path": "pictures/logo.svg"}).status_code == 400
    assert logged_in.get("/files/thumb", params={"path": "pictures/ok.png"}).status_code == 200


def test_thumb_rejects_oversized_source(workspace, logged_in, monkeypatch):
    monkeypatch.setattr(ws, "THUMB_SOURCE_MAX", 4096)
    p = _make_images(workspace, 1)[0]
    while p.stat().st_size <= 4096:
        p.write_bytes(p.read_bytes() + _png())
    ws.invalidate_caches()
    r = logged_in.get("/files/thumb", params={"path": _rel(workspace, p)})
    assert r.status_code == 400, "超大原图必须有界拒绝（不能整读进内存再解码）"


# ---------------------------------------------------------------------------
# 7. 设置读取不进每请求热路径
# ---------------------------------------------------------------------------

def test_setting_reads_are_cached():
    """get_setting 以前每请求 5 次打库；进程内 TTL 后一批请求最多一次。"""
    from app.core import appsettings as ap

    ap.set_setting("perf_probe", "a")
    calls = {"n": 0}
    real = db.query_one

    def counting(sql, params=()):
        if "app_settings" in sql:
            calls["n"] += 1
        return real(sql, params)

    db.query_one = counting
    try:
        for _ in range(20):
            assert ap.get_setting("perf_probe") == "a"
        assert calls["n"] <= 1, f"20 次读取打库 {calls['n']} 次"
        ap.set_setting("perf_probe", "b")
        assert ap.get_setting("perf_probe") == "b", "写入后必须立刻读到新值"
    finally:
        db.query_one = real


def test_sse_watch_is_not_a_thread_holder(logged_in):
    """/files/watch 必须是 async 端点：同步生成器会整段占住一个 worker 线程。"""
    import inspect

    from app.web.routers import files as files_router

    fn = files_router.watch
    assert inspect.iscoroutinefunction(fn), "SSE 端点仍是同步函数（会占满线程池）"
    src = inspect.getsource(fn)
    assert "asyncio.sleep" in src and "time.sleep" not in src, "监听循环不得阻塞式 sleep"
    assert "workbench_sse_max" in src or "max_slots" in src, "监听必须有并发上限"


def test_watch_signature_detects_disk_changes(workspace):
    """盯盘签名：新增/改名/mtime 变要能看出来，隐藏文件与轮次无关不能发教。"""
    from app.web.routers import files as fr

    pic = workspace / "pictures"
    pic.mkdir(parents=True, exist_ok=True)
    (pic / "a.png").write_bytes(_png(40))
    ws.invalidate_caches()
    s1 = fr.watch_signature("pictures")
    assert fr.watch_signature("pictures") == s1, "同一快照内不得反复报变化（不然前端每 3s 自刷）"

    (pic / ".hidden.png").write_bytes(_png(40))
    assert fr.watch_signature("pictures") == s1, "隐藏文件不该触发刷新"

    (pic / "b.png").write_bytes(_png(40))
    assert fr.watch_signature("pictures") != s1, "新增文件没被发现"

    (pic / "a.png").rename(pic / "a2.png")
    s3 = fr.watch_signature("pictures")
    (pic / "a2.png").rename(pic / "a.png")
    assert fr.watch_signature("pictures") != s3, "改名没被发现"
    assert fr.watch_signature("../outside") is None, "越狱路径必须静默拒（不能 500）"



def test_watch_change_busts_the_listing_cache(logged_in):
    """盯盘发现变化 → 必须作废列表/索引快照（推送了但页面不变 = 这个钩子丢了）。

    监听循环靠 TestClient 无法可靠流式驱动，这里钉接线：两个机制一旦脱钩，
    本用例红。行为面另由 test_list_dir_cache_busts_on_service_write 兜住。"""
    import inspect

    from app.web.routers import files as files_router

    src = inspect.getsource(files_router.watch)
    assert "changed" in src and "invalidate_caches" in src, \
        "changed 事件没伴随缓存作废——盯盘推送会被 30s 列表缓存吞掉"
    assert "yield _payload" in src, "监听循环得自己产出 SSE 事件"


# ---------------------------------------------------------------------------
# 0.8.34 残留：首屏不得等全树索引。
#
# 症状：工作区文件一多，每进一次 /files 都同步等 category_counts / location_counts
# （二者都派生自全树 _walk_files）→ 冷缓存时整个页面卡在盘上。本组用例把「首屏」
# 与「全树索引」解耦这件事钉死。
# ---------------------------------------------------------------------------


def test_first_page_does_not_wait_for_index(workspace, logged_in, monkeypatch):
    """_walk_files 卡在 Event 上时，/files 仍立刻 200、且不聚合计数。"""
    from app.hermes import workspace_service as ws
    from threading import Event

    gate = Event()
    real_walk = ws._walk_files
    calls = {"n": 0}

    def blocking_walk():
        calls["n"] += 1
        gate.wait(5)            # 模拟慢全树
        return real_walk()

    monkeypatch.setattr(ws, "_walk_files", blocking_walk)

    cat_calls = {"n": 0}
    real_cat = ws.category_counts
    monkeypatch.setattr(ws, "category_counts",
                        lambda: (cat_calls.__setitem__("n", cat_calls["n"] + 1)
                                 or real_cat()))

    loc_calls = {"n": 0}
    real_loc = ws.location_counts
    monkeypatch.setattr(ws, "location_counts",
                        lambda: (loc_calls.__setitem__("n", loc_calls["n"] + 1)
                                 or real_loc()))

    t0 = time.monotonic()
    r = logged_in.get("/files")
    elapsed = time.monotonic() - t0

    assert r.status_code == 200
    assert "projects" in r.text                 # 侧栏链接恒在（占位）
    # 首屏根本没等索引完成 —— 索引线程还卡在 gate 上。
    assert not gate.is_set()
    # 首屏不调用聚合计数（机制证据）+ 不起全树（prefetch 起线程但没等）。
    assert cat_calls["n"] == 0
    assert loc_calls["n"] == 0
    assert calls["n"] == 1
    # 没卡在 gate(5s) 上 —— 首屏返回得快。
    assert elapsed < 2.0


def test_first_page_side_fragment_link_present_without_numbers(workspace, logged_in):
    """首屏 HTML 含 hx-get=/files/side 与 e-loc 链接，但不强制 e-n 数字。"""
    r = logged_in.get("/files")
    assert r.status_code == 200
    body = r.text
    assert 'hx-get="/files/side"' in body
    assert "e-loc" in body                       # 侧栏链接在
    assert "主文件夹" in body
    # 占位侧栏：链接的数字为空（n is None 不渲染数字），但链接仍可点。


def test_side_fragment_loads_counts(workspace, logged_in, tmp_path):
    """GET /files/side 异步返回真实计数（可等待索引，单飞收敛）。"""
    _make_images(workspace, 1)
    ws.invalidate_caches()

    expected = dict((k, n) for k, _, _, n in ws.category_counts())
    images = expected.get("images", 0)
    assert images >= 1

    r = logged_in.get("/files/side")
    assert r.status_code == 200
    assert "图片" in r.text                       # 智能集合链接
    assert "e-n" in r.text
    assert str(images) in r.text                 # 数字与服务真相一致


def test_prefetch_index_returns_without_blocking(workspace, monkeypatch):
    """prefetch_index() 即使底层 _walk_files 极慢也立即返回（后台线程，不阻塞）。"""
    from threading import Event
    from app.hermes import workspace_service as ws

    gate = Event()
    real_walk = ws._walk_files

    def slow_walk():
        gate.wait(10)
        return real_walk()

    monkeypatch.setattr(ws, "_walk_files", slow_walk)

    t0 = time.monotonic()
    ws.prefetch_index()
    assert time.monotonic() - t0 < 1.0          # 立刻返回，没等慢全树


def test_first_page_side_not_regressed_by_write_oob(workspace, logged_in):
    """写操作后侧栏仍走 OOB 换入（id=osfm-side + hx-swap-oob），不回归同步等待。"""
    from app.hermes import engineering_service as eng
    # 上传一条文件触发 rerender（含侧栏 OOB）。
    (workspace / "documents").mkdir(parents=True, exist_ok=True)
    (workspace / "documents" / "note.md").write_text("hello", encoding="utf-8")
    ws.invalidate_caches()

    from tests.conftest import csrf_of
    token = csrf_of(logged_in)
    resp = logged_in.post("/files/move",
                          headers={"X-CSRF-Token": token},
                          data={"path": "documents/note.md", "here": "/",
                                "_csrf": token})
    r = resp.text
    assert 'id="osfm-side"' in r, "侧栏计数应 OOB 换入"
    assert 'hx-swap-oob="true"' in r
