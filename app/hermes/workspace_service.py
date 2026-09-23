"""工作区文件服务（W3 体验版）：jail 化的浏览 / 预览 / 下载 / 打包 / 上传 / 整理。

单一契约 —— 根目录永远读 EngSettings.workspace（与工程注入层同源），
禁止另设第二个路径配置项。所有外部传入的相对路径必须经 resolve_rel() 做
jail 校验（resolve 后仍在根内，symlink 逃逸同样被拦）。
执法策略（见 FILE_WORKBENCH_SPEC.md §1）：不在文件系统层拦截 Agent，
写入口只有「上传→downloads/」与「整理→archive/」两类，永不覆盖。
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core import appsettings
from app.hermes import engineering_service as eng

BUCKETS = ("projects", "downloads", "documents", "pictures", "videos",
           "scratch", "archive")

TEXT_EXTS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".log", ".csv"}
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh", ".go",
             ".rs", ".java", ".sql", ".env"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"}
# 能生成位图缩略图的扩展名（svg 是矢量、avif 需解码器，两者不进网格缩略图）
THUMB_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# 全树遍历的剪枝目录：依赖/构建产物不是工作区内容，但它们动辄十万个文件，
# 是「一开页就把服务器跑满」的头号诱因（rglob 会走进 node_modules 全扫一遍）。
WALK_PRUNE = {"node_modules", "__pycache__", ".git", ".venv", "venv", ".tox"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".flv", ".wmv"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
DOC_EXTS = {".md", ".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
            ".ppt", ".pptx", ".csv", ".rtf"}
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar"}

# 智能集合（虚拟视图，不复制文件）：key → (标签, emoji, 扩展名)
CATEGORIES = (
    ("images", "图片", "🖼️", IMAGE_EXTS),
    ("videos", "视频", "🎬", VIDEO_EXTS),
    ("audio", "音频", "🎵", AUDIO_EXTS),
    ("documents", "文档", "📄", DOC_EXTS),
    ("code", "代码", "📜", CODE_EXTS),
    ("archives", "压缩包", "🗜️", ARCHIVE_EXTS),
)
CATEGORY_MAP = {k: (label, emoji, exts) for k, label, emoji, exts in CATEGORIES}
PREVIEW_LIMIT = 512 * 1024
COLLECTION_LIMIT = 1000
# 索引上限：异常工作区（把工作区挂在盘根之类）下限制内存与遍历时间。
INDEX_LIMIT = 50000
# 网格/列表单页条目数：以前一次渲染往浏览器里堆 1000 张卡片（= 1000 个
# 缩略图请求），线上小机器直接被打穿。超出部分走「加载更多」增量拉取。
PAGE_SIZE = 120
# 缩略图跳过的原图上限（字节）：超大图解码一次要数百 ms CPU，宁可回退类型
# 图标，也不让一张图吃掉半台机器。
THUMB_SOURCE_MAX = 64 << 20
UPLOAD_DEFAULT_MAX_MB = 50
SCRATCH_STALE_DAYS = 14


class WorkspaceError(Exception):
    """错误信息可直接展示给管理员。"""


class UnsafePath(WorkspaceError):
    """越狱/非法路径 —— 路由层按 4xx + 审计 denied 处理。"""


class NotFound(WorkspaceError):
    pass


# ---------------------------------------------------------------------------
# 根目录与 jail
# ---------------------------------------------------------------------------

_ROOT_TTL = 5.0
_root_lock = threading.Lock()
_root_cache: tuple[float, Path] | None = None


def invalidate_root_cache() -> None:
    global _root_cache
    with _root_lock:
        _root_cache = None


def root() -> Path:
    """工作区根（缓存 5s）：以前每个带 path 的请求都要读一次库 + 解一次
    Path.resolve()，而网格页有几百个这样的请求 —— 纯开销。写设置（
    engineering.save_settings）与服务层写操作都会主动失效。"""
    global _root_cache
    now = time.monotonic()
    with _root_lock:
        if _root_cache is not None and now - _root_cache[0] < _ROOT_TTL:
            return _root_cache[1]
    rp = validate_root((eng.load_settings().workspace or eng.DEFAULT_WORKSPACE).strip())
    with _root_lock:
        _root_cache = (time.monotonic(), rp)
    return rp


def validate_root(raw: str) -> Path:
    """S2 防劫持：拒绝过浅路径与 home 本身作为 jail 根。"""
    p = Path(raw).expanduser()
    rp = p.resolve() if p.exists() else p.absolute()
    if len(rp.parts) < 2 or rp == Path.home().expanduser().resolve():
        raise WorkspaceError(
            f"workspace 根目录过浅或不安全：{rp}（请使用 home 之下至少一层的专属目录）")
    return rp


def resolve_rel(rel: str = "") -> Path:
    base = root()
    cleaned = (rel or "").strip().lstrip("/\\")
    target = (base / cleaned).resolve()
    if target != base and base not in target.parents:
        raise UnsafePath(f"路径越出工作区：{rel}")
    return target


def rel_of(path: Path) -> str:
    base = root()
    return path.relative_to(base).as_posix()


# ---------------------------------------------------------------------------
# 条目模型
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    name: str
    rel: str
    is_dir: bool
    size: int
    mtime: float
    count: int | None = None  # 目录内子项数（列表页填充）

    @property
    def mtime_text(self) -> str:
        return datetime.fromtimestamp(self.mtime, tz=timezone.utc).strftime("%m-%d %H:%M")

    @property
    def size_text(self) -> str:
        if self.is_dir:
            return "-"
        for unit, div in (("MB", 1 << 20), ("KB", 1 << 10)):
            if self.size >= div:
                return f"{self.size / div:.1f} {unit}"
        return f"{self.size} B"

    @property
    def ext(self) -> str:
        return Path(self.name).suffix.lower()

    @property
    def is_bucket(self) -> bool:
        return self.rel in BUCKETS

    @property
    def category(self) -> str | None:
        return category_of(self.name)

    @property
    def category_emoji(self) -> str:
        c = self.category
        return CATEGORY_MAP[c][1] if c else "📎"

    @property
    def thumbable(self) -> bool:
        """网格能否走 /files/thumb（不可时模板回退类型图标，不发白请求）。"""
        return (not self.is_dir) and self.ext in THUMB_EXTS


def _entry(p: Path, base: Path) -> Entry:
    st = p.stat()
    return Entry(name=p.name, rel=p.relative_to(base).as_posix(),
                 is_dir=p.is_dir(), size=st.st_size, mtime=st.st_mtime)


def category_of(filename: str) -> str | None:
    ext = Path(filename).suffix.lower()
    for key, _label, _emoji, exts in CATEGORIES:
        if ext in exts:
            return key
    return None


def _sort_entries(entries: list[Entry], sort: str) -> list[Entry]:
    """视图排序。次级键一律名字：主键同值（同一秒连续写入、同大小）时
    输出不再依赖进盘顺序，集合页不会刷新一次就换一套顶部。"""
    if sort == "time":
        key = lambda e: (not e.is_dir, -e.mtime, e.name.lower())
    elif sort == "size":
        key = lambda e: (not e.is_dir, -e.size, e.name.lower())
    else:
        key = lambda e: (not e.is_dir, e.name.lower())
    return sorted(entries, key=key)


def _walk_files() -> list[Entry]:
    """全工作区文件索引（一次遍历）。

    用 os.scandir 而不是 Path.rglob，两个要紧的理由：
    1. DirEntry 直接复用目录项里已有的元数据（Windows 上一次 readdir 就拿齐
       name/属性），rglob + is_file() + stat() 是每条两三次系统调用；
    2. 能在进入目录前剪枝：隐藏目录与 WALK_PRUNE（node_modules 等）根本不进栈。
       rglob 会先把它们整个走完再丢弃，一个前端项目就能把工作区“扫”成十万条。

    符号链接一律跳过（不跟随 = 既防环也防路径逸出 jail）。"""
    base = str(root())
    out: list[Entry] = []
    stack: list[tuple[str, str]] = [(base, "")]
    while stack:
        cur, prefix = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError:
            continue
        with it:
            for de in it:
                name = de.name
                if name.startswith(".") or name in WALK_PRUNE:
                    continue
                try:
                    if de.is_dir(follow_symlinks=False):
                        stack.append((de.path, f"{prefix}{name}/"))
                    elif de.is_file(follow_symlinks=False) and len(out) < INDEX_LIMIT:
                        st = de.stat(follow_symlinks=False)
                        out.append(Entry(name=name, rel=prefix + name, is_dir=False,
                                         size=st.st_size, mtime=st.st_mtime))
                except OSError:
                    continue
        if len(out) >= INDEX_LIMIT:
            break               # 到上界就收工：剩下的目录不该再走盘
    return out


def _index() -> list[Entry]:
    """工作区索引（TTL 缓存 + 单飞）：计数/集合/最近全部从它派生。

    0.8.33 之前一次 /files 渲染要跑 4 遍全树（category / location / collection /
    inspect），而网格页本身又会因导航反复触发 —— 线上就是被这个吃死的。"""
    return _cached_counts("index", _walk_files)


def prefetch_index() -> None:
    """后台预热全树索引（**非阻塞**）：首屏不等它，后续视图 / 集合再命中。

    0.8.34 把 category / location 计数挪到首屏同步等全树，文件一多冷缓存时整个
    页面卡在盘上 —— 本函数把预热放回后台线程，调用方立刻返回。计算本身仍走
    _cached_counts 的单飞（同一 key 只跑一次全树），重复预热自动收敛。
    """
    threading.Thread(target=_index, daemon=True, name="wb-index-prefetch").start()


def list_collection(cat: str, limit: int = COLLECTION_LIMIT,
                    sort: str = "time") -> list[Entry]:
    """智能集合：从工作区索引按类型聚合（虚拟视图，文件不动、不复制）。

    过滤 + 排序是内存操作（索引已缓存），写操作后索引失效 → 视图跟着变。
    `sort` 跟随地址栏：/files?cat=images&sort=name 以前拿到的是时间序，
    因为集合自己写死了 time —— 视图排序语义不该由数据层决定。"""
    if cat not in CATEGORY_MAP:
        raise WorkspaceError(f"未知集合：{cat}")
    exts = CATEGORY_MAP[cat][2]
    return _sort_entries([e for e in _index() if e.ext in exts], sort)[:limit]


def list_recent(limit: int = 100) -> list[Entry]:
    return _sort_entries(_index(), "time")[:limit]


# ---------------------------------------------------------------------------
# 视图分页：一頁只送 PAGE_SIZE 条，剩下的用「加载更多」按 offset 增量拉。
# ---------------------------------------------------------------------------

@dataclass
class Page:
    items: list[Entry]
    total: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total

    @property
    def next_offset(self) -> int:
        return self.offset + len(self.items)


def paginate(entries: list[Entry], offset: int = 0, size: int = PAGE_SIZE) -> Page:
    offset = max(0, offset)
    if offset >= len(entries) and entries:
        offset = 0            # 写操作后集合变短：越界回到首页，不出空页
    return Page(items=entries[offset: offset + size], total=len(entries), offset=offset)


# ---------------------------------------------------------------------------
# 计数/索引缓存（W6 + 0.8.34 单飞）：全树遍历结果按 TTL 复用，写操作（上传/
# 归档/重命名/删除/复制/新建/移动/保存）后主动失效。
#
# 单飞：以前的 _cached_counts 无锁，缓存一过期时 N 个并发请求会同时重跑全树
# （缓存雪崩）—— 网格页几百个请求同时到期就是 CPU 尖刺。现在同一 key 只有一
# 个线程计算，其余线程等它，然后直接命中。不拿全局锁跨计算期，避免串行化。
# ---------------------------------------------------------------------------

_COUNTS_TTL = 30.0
_counts_cache: dict[str, tuple[float, object]] = {}
_cache_guard = threading.Lock()
_cache_flight: dict[str, threading.Lock] = {}


def invalidate_caches() -> None:
    global _cache_flight
    with _cache_guard:
        _counts_cache.clear()
        _cache_flight = {}
    invalidate_root_cache()


def invalidate_dir_cache(rel: str = "") -> None:
    """只作废单个目录的目录扫描缓存（watcher 按签名发现变更时使用）。

    与 invalidate_caches 不同，这里不动全树索引 / 其他目录 / 根缓存：盯盘盯的是
    当前这个目录的真变化，正在浏览的列表必须立刻反映；而集合 / 最近 / 搜索派生
    自全树索引，由写操作与索引 TTL（30s）自己保证，不该被一个目录的变更连带打穿。
    0.8.x 之前 watcher 一发现变化就 invalidate_caches 一把清全部 —— Agent 在另一
    头写文件时，3 秒一轮把整页索引反复重算，就是「每次翻页都要重新获取计算」的头号
    诱因。这里只清当前目录一份快照，其余视图继续吃热缓存。
    """
    with _cache_guard:
        _counts_cache.pop(_cache_key(f"dir:{rel or '.'}"), None)


def invalidate_bucket_cache(name: str) -> None:
    """作废单个标准目录（bucket）的扫描缓存。"""
    with _cache_guard:
        _counts_cache.pop(_cache_key(f"dir:{name}"), None)


def _cache_key(key: str) -> str:
    return f"{root()}|{key}"


def _cached_counts(key: str, fn):
    k = _cache_key(key)
    with _cache_guard:
        hit = _counts_cache.get(k)
        if hit is not None and time.monotonic() - hit[0] < _COUNTS_TTL:
            return hit[1]
        flight = _cache_flight.get(k)
        mine = flight is None
        if mine:
            flight = _cache_flight[k] = threading.Lock()
    if not mine:                      # 已有线程在算：等它完事再取结果
        flight.acquire()
        flight.release()
        with _cache_guard:
            hit = _counts_cache.get(k)
            if hit is not None and time.monotonic() - hit[0] < _COUNTS_TTL:
                return hit[1]
        return _store(k, fn())        # 前车线程挂了：兜底自己算
    with flight:
        try:
            return _store(k, fn())
        finally:
            with _cache_guard:
                _cache_flight.pop(k, None)


def _store(k: str, val):
    with _cache_guard:
        _counts_cache[k] = (time.monotonic(), val)
    return val


def category_counts() -> list[tuple[str, str, str, int]]:
    """[(key, 标签, emoji, 数量)]，侧栏用（从索引派生，不再走盘）。"""
    counts = {k: 0 for k, *_ in CATEGORIES}
    for e in _index():
        c = e.category
        if c:
            counts[c] += 1
    return [(k, CATEGORY_MAP[k][0], CATEGORY_MAP[k][1], counts[k]) for k in counts]


def location_counts() -> list[tuple[str, int]]:
    """标准目录（位置）的文件总数，侧栏用。

    口径修正：和集合/计数同源，隐藏目录与依赖产物不计入（以前每个 bucket 再
    rglob 一次，还把人家的 .git/node_modules 算进了计数）。"""
    counts = {d: 0 for d in BUCKETS}
    for e in _index():
        head = e.rel.split("/", 1)[0]
        if head in counts:
            counts[head] += 1
    return [(d, counts[d]) for d in BUCKETS]


def _scan_dir(target: Path) -> list[Entry]:
    base = root()
    entries = []
    for p in target.iterdir():
        if p.name.startswith("."):
            continue
        e = _entry(p, base)
        if e.is_dir:
            try:
                e.count = sum(1 for c in p.iterdir() if not c.name.startswith("."))
            except OSError:
                e.count = None
        entries.append(e)
    return entries


def list_dir(rel: str = "", sort: str = "name") -> list[Entry]:
    """目录列表（结果与集合/计数同缓存，服务层写操作主动失效）。

    以前每请求都 iterdir + 建满量 Entry：一个 5 万文件的目录就是秒级 +
    几十 MB 内存，翻页、切排序、点进去又点回来各扫一次。缓存后同目录只扫
    一次盘；排序是内存操作（返回新列表，不改动缓存）。
    代价：绕过服务层直接写盘的文件最多 _COUNTS_TTL（30s）后才出现在列表。"""
    target = resolve_rel(rel)          # 每请求都过 jail：缓存不得成为越权后门
    if not target.exists():
        raise NotFound(f"目录不存在：{rel or '/'}")
    if not target.is_dir():
        raise WorkspaceError("不是目录")
    entries = _cached_counts(f"dir:{rel or '.'}", lambda: _scan_dir(target))
    return _sort_entries(entries, sort)


def breadcrumbs(rel: str = "") -> list[tuple[str, str]]:
    """[(显示名, 路径)]，含根。"""
    parts = [p for p in (rel or "").split("/") if p]
    out = [("工作区", "")]
    for i in range(len(parts)):
        out.append((parts[i], "/".join(parts[: i + 1])))
    return out


def dir_exists(rel: str = "") -> bool:
    try:
        return resolve_rel(rel).is_dir()
    except (WorkspaceError, OSError):
        return False


def workspace_status() -> dict:
    try:
        base = root()
    except WorkspaceError as exc:
        return {"error": str(exc), "exists": False, "initialized": False}
    return {
        "root": str(base),
        "exists": base.exists(),
        "initialized": base.is_dir() and (base / "README.md").exists(),
    }


# ---------------------------------------------------------------------------
# 预览 / 原始字节 / 打包
# ---------------------------------------------------------------------------

def preview_class(rel: str) -> str:
    ext = Path(rel).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext == ".pdf":
        return "pdf"
    if ext in (".html", ".htm"):
        return "html"   # 沙盒 iframe 预览（WI-19），不再走 text 纯文本
    if ext in TEXT_EXTS or ext in CODE_EXTS:
        return "text"
    return "none"


def read_preview(rel: str) -> dict:
    path = resolve_rel(rel)
    if not path.is_file():
        raise NotFound(f"文件不存在：{rel}")
    mode = preview_class(rel)
    if mode != "text":
        return {"mode": mode, "rel": rel}
    data = read_prefix(path, PREVIEW_LIMIT)
    return {
        "mode": "text",
        "rel": rel,
        "content": data.decode("utf-8", errors="replace"),
        "truncated": path.stat().st_size > PREVIEW_LIMIT,
        "is_code": Path(rel).suffix.lower() in CODE_EXTS,
    }


SAVE_LIMIT = 1 * 1024 * 1024  # 窗口编辑保存的内容上限（覆盖/副本通用）


def save_text(rel: str, content: str, mode: str = "overwrite", name: str = "") -> str:
    """窗口文本编辑保存：overwrite 覆盖原文件；copy 在同目录另存（自动唯一化）。

    截断预览（原文件超 PREVIEW_LIMIT）拒绝覆盖——内存里只有前 512KB，
    直接覆盖会丢数据；想改请下载改完上传，或用 copy 另存当前预览内容。
    """
    src = resolve_rel(rel)
    if not src.is_file():
        raise NotFound(f"文件不存在：{rel}")
    data = (content or "").encode("utf-8")
    if len(data) > SAVE_LIMIT:
        raise WorkspaceError("内容超过上限 1MB")
    if mode == "copy":
        stem, suffix = src.stem, src.suffix
        want = (name or "").strip() or f"{stem}-副本{suffix}"
        target = _unique(src.parent / _sanitize_name(want))
        target.write_bytes(data)
        invalidate_caches()
        return rel_of(target)
    if src.stat().st_size > PREVIEW_LIMIT:
        raise WorkspaceError("文件过大（预览已截断），只读：请下载修改后上传")
    src.write_bytes(data)
    invalidate_caches()
    return rel_of(src)


def file_for_download(rel: str) -> Path:
    path = resolve_rel(rel)
    if not path.is_file():
        raise NotFound(f"文件不存在：{rel}")
    return path


def read_prefix(path: Path, limit: int) -> bytes:
    """只读前 limit 字节。以前预览走 read_bytes()[:limit]，对大文件是先全读
    进内存再切片——一个 300MB 的日志能把小机器直接顶住。"""
    with open(path, "rb") as fh:
        return fh.read(limit)


# ---------------------------------------------------------------------------
# 缩略图：网格此前 <img src=/files/raw> 直接加载原图，开一个照片目录就是
# 几百 MB 流量 + 秒级渲染（文件 OS 最后一个已知大慢点）。Pillow 生成最长边
# 320px 的 JPEG 落盘缓存；缓存键含 mtime+size → 文件一变自动换新键，
# 旧 URL 的响应永不变化，浏览器可放心长缓存（immutable）。
# ---------------------------------------------------------------------------

THUMB_MAX = 320
_THUMB_EXTS = THUMB_EXTS
# 分片锁：同一个源图被并发请求（刷新/多标签）时只解一次，其余等结果。
# 分片而不是 per-key 字典：内存有上界，不会随历史图片数无限增长。
_THUMB_SHARDS = 16
_thumb_locks = [threading.Lock() for _ in range(_THUMB_SHARDS)]


def _thumb_lock(key: str) -> threading.Lock:
    return _thumb_locks[int.from_bytes(hashlib.sha1(key.encode()).digest()[:4],
                                       "big") % _THUMB_SHARDS]


def thumb_for(rel: str) -> Path:
    from PIL import Image, ImageOps  # 延迟导入：缩略图只在文件工作台用到

    src = resolve_rel(rel)
    if not src.is_file():
        raise NotFound(f"文件不存在：{rel}")
    if src.suffix.lower() not in THUMB_EXTS:
        raise WorkspaceError("该类型不生成缩略图")
    st = src.stat()
    if st.st_size > THUMB_SOURCE_MAX:
        raise WorkspaceError(f"原图超过 {THUMB_SOURCE_MAX >> 20}MB，不生成缩略图")
    key = hashlib.sha1(
        f"{rel}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")).hexdigest()[:24]
    from app.core.settings import settings

    cache_dir = settings.data_dir / "thumbs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{key}.jpg"
    if out.exists():
        return out
    with _thumb_lock(key):
        if out.exists():                     # 等锁期间别人算好了
            return out
        with Image.open(src) as im:
            im.draft("RGB", (THUMB_MAX * 2, THUMB_MAX * 2))  # JPEG 解码降采样，大图省内存
            im = ImageOps.exif_transpose(im)                 # 手机照片按 EXIF 摆正
            im.thumbnail((THUMB_MAX, THUMB_MAX))
            rgb = im.convert("RGB")
            # 先写临时文件再原子换名：并发首访同一文件不会让对方读到半张 JPEG
            fd, tmp = tempfile.mkstemp(dir=cache_dir, suffix=".jpg")
            try:
                with os.fdopen(fd, "wb") as fh:
                    rgb.save(fh, "JPEG", quality=82)
                Path(tmp).replace(out)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
    return out


def zip_rel(rel: str) -> tuple[bytes, str]:
    """目录打包（zip，内存流，M1 体验版限单目录 <200MB）。返回 (bytes, 文件名)。"""
    target = resolve_rel(rel)
    if not target.is_dir():
        raise WorkspaceError("只能打包目录")
    buf = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(target.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                total += p.stat().st_size
                if total > 200 << 20:
                    raise WorkspaceError("目录过大（>200MB），暂不支持打包")
                zf.write(p, p.relative_to(target.parent))
    return buf.getvalue(), f"{target.name}.zip"


# ---------------------------------------------------------------------------
# 写操作（仅两类，永不覆盖）
# ---------------------------------------------------------------------------

_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _sanitize_name(filename: str) -> str:
    name = Path(filename or "").name.strip()
    name = _BAD_NAME.sub("_", name).lstrip(". ")
    return name or "unnamed"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 1000):
        cand = path.with_name(f"{stem}-{i}{suffix}")
        if not cand.exists():
            return cand
    raise WorkspaceError("同名文件过多")


def _upload_max_bytes() -> int:
    raw = appsettings.get_setting("workbench_upload_max_mb") or str(UPLOAD_DEFAULT_MAX_MB)
    try:
        return max(1, int(raw)) << 20
    except ValueError:
        return UPLOAD_DEFAULT_MAX_MB << 20


def upload(filename: str, data: bytes, dest_rel: str = "downloads") -> str:
    """S3：上传固定落 downloads/，空文件与超限拒绝，同名自动加后缀。"""
    if not data:
        raise WorkspaceError("空文件不落盘")
    if len(data) > _upload_max_bytes():
        raise WorkspaceError(f"超过上限 {_upload_max_bytes() >> 20}MB")
    base = root()
    dest_dir = resolve_rel(dest_rel)
    if dest_dir != (base / "downloads").resolve():
        raise UnsafePath("上传只允许进入 downloads/")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = _unique(dest_dir / _sanitize_name(filename))
    target.write_bytes(data)
    invalidate_caches()
    return rel_of(target)


def move_to_archive(rel: str) -> str:
    """整理动作 = mv 进 archive/（项目带日期后缀），永不复制出第二份。"""
    src = resolve_rel(rel)
    if not src.exists():
        raise NotFound(f"不存在：{rel}")
    base = root()
    if src == base:
        raise UnsafePath("不能移动工作区根")
    archive = base / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    name = f"{src.name}-{stamp}" if src.parent == base / "projects" else src.name
    target = _unique(archive / name)
    src.rename(target)
    invalidate_caches()
    return rel_of(target)


# ---------------------------------------------------------------------------
# 基本盘文件操作（W13）：重命名 / 软删除 / 复制 / 新建 / 移动。
# 全部复用 resolve_rel 的 jail 校验；规范目录（BUCKETS）与 .trash 受保护；
# 永不静默覆盖（目标存在即报错或自动后缀）。删除是软删除，进 .trash 可还原。
# ---------------------------------------------------------------------------

TRASH_DIR = ".trash"


def _guard_protected(src: Path) -> None:
    """规范目录与回收站本身不可改名/删除/移出。"""
    base = root()
    if src == base:
        raise UnsafePath("不能对工作区根本身操作")
    if src.parent == base and src.name in (BUCKETS + (TRASH_DIR,)):
        raise UnsafePath(f"「{src.name}」是工作区规范目录，不允许该操作")


def rename_entry(rel: str, new_name: str) -> str:
    src = resolve_rel(rel)
    _guard_protected(src)
    if not src.exists():
        raise NotFound(f"不存在：{rel}")
    name = _sanitize_name(new_name)
    if name == src.name:
        return rel_of(src)
    target = src.parent / name
    if target.exists():
        raise WorkspaceError(f"同名条目已存在：{name}")
    src.rename(target)
    invalidate_caches()
    return rel_of(target)


def delete_entry(rel: str) -> str:
    """软删除：移入 workspace/.trash/（时间戳后缀防撞名），manifest 记录原位置。"""
    src = resolve_rel(rel)
    _guard_protected(src)
    if not src.exists():
        raise NotFound(f"不存在：{rel}")
    base = root()
    trash = base / TRASH_DIR
    trash.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = trash / f"{src.name}-{stamp}"
    src.rename(target)
    manifest = _trash_manifest()
    manifest[rel_of(target)] = rel_of(src)
    _save_trash_manifest(manifest)
    invalidate_caches()
    return rel_of(target)


def _trash_manifest() -> dict:
    import json

    f = root() / TRASH_DIR / "manifest.json"
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_trash_manifest(manifest: dict) -> None:
    import json

    f = root() / TRASH_DIR / "manifest.json"
    f.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def restore_entry(trash_rel: str) -> str:
    """从 .trash 还原到删除时的原位置（原位置被占则自动加后缀）。"""
    src = resolve_rel(trash_rel)
    base = root()
    if base / TRASH_DIR not in src.parents:
        raise UnsafePath("只能还原回收站内的条目")
    if not src.exists():
        raise NotFound(f"不存在：{trash_rel}")
    manifest = _trash_manifest()
    origin_rel = manifest.get(rel_of(src), "")
    origin = resolve_rel(origin_rel) if origin_rel else base / src.name
    origin.parent.mkdir(parents=True, exist_ok=True)
    target = _unique(origin)
    src.rename(target)
    manifest.pop(rel_of(src), None)
    _save_trash_manifest(manifest)
    invalidate_caches()
    return rel_of(target)


def trash_list() -> list[Entry]:
    """回收站内容（.trash 被 jail 常规列表隐藏，这里显式读）。"""
    trash = root() / TRASH_DIR
    if not trash.is_dir():
        return []
    base = root()
    out = []
    for p in trash.iterdir():
        if p.name.startswith(".") or p.name == "manifest.json":
            continue
        try:
            out.append(_entry(p, base))
        except OSError:
            continue
    return out


def copy_entry(rel: str) -> str:
    """同目录复制副本（「xxx-副本」自动后缀），目录递归复制。"""
    src = resolve_rel(rel)
    _guard_protected(src)
    if not src.exists():
        raise NotFound(f"不存在：{rel}")
    want = src.with_name(f"{src.stem}-副本{src.suffix}")
    target = _unique(want)
    if src.is_dir():
        shutil.copytree(src, target)
    else:
        shutil.copy2(src, target)
    invalidate_caches()
    return rel_of(target)


def mkdir_entry(parent_rel: str, name: str) -> str:
    parent = resolve_rel(parent_rel)
    if not parent.is_dir():
        raise NotFound("目标目录不存在")
    target = _unique(parent / _sanitize_name(name))
    target.mkdir()
    invalidate_caches()
    return rel_of(target)


def mkfile_entry(parent_rel: str, name: str) -> str:
    parent = resolve_rel(parent_rel)
    if not parent.is_dir():
        raise NotFound("目标目录不存在")
    target = _unique(parent / _sanitize_name(name))
    target.write_bytes(b"")
    invalidate_caches()
    return rel_of(target)


def move_entry(rel: str, dest_rel: str) -> str:
    """移动到指定目录（拖拽放入文件夹）；目标同名自动加后缀，绝不覆盖。"""
    src = resolve_rel(rel)
    _guard_protected(src)
    dest_dir = resolve_rel(dest_rel)
    if not dest_dir.is_dir():
        raise NotFound(f"目标目录不存在：{dest_rel}")
    if src == dest_dir or src in dest_dir.parents:
        raise UnsafePath("不能把目录移进它自己（或自己的子目录）")
    if src.parent == dest_dir:
        return rel_of(src)
    target = _unique(dest_dir / src.name)
    src.rename(target)
    invalidate_caches()
    return rel_of(target)


def search(query: str, limit: int = 500) -> list[Entry]:
    """全局搜索：在缓存的工作区索引里按文件名子串匹配。

    以前每查询一次重走一遍全树（索引未命中时是秒级），现在变成内存筛选。
    代价：搜索视图和集合/计数一样受索引 TTL（默认 30s）约束，服务层写操作
    会立即失效，只有绕过服务层直接写盘的文件需要等 TTL 才能被搜到。"""
    q = (query or "").strip().lower()
    if not q:
        return []
    return [e for e in _index() if q in e.name.lower()][:limit]


# ---------------------------------------------------------------------------
# 归位巡检（W4 提前做了只读摘要，执法建议 M2 再补）
# ---------------------------------------------------------------------------

def inspect_summary() -> dict:
    def _calc() -> dict:
        base = root()
        if not base.is_dir():
            return {"available": False}
        stray = sorted(p.name for p in base.iterdir()
                       if p.name not in BUCKETS and not p.name.startswith("."))
        now = datetime.now(timezone.utc).timestamp()
        stale_scratch = 0
        scratch = base / "scratch"
        if scratch.is_dir():
            stale_scratch = sum(1 for p in scratch.iterdir()
                                if now - p.stat().st_mtime > SCRATCH_STALE_DAYS * 86400)
        projects = base / "projects"
        no_req = []
        if projects.is_dir():
            for d in sorted(p for p in projects.iterdir() if p.is_dir()):
                if d.name in ("example-project",):
                    continue
                if not (d / "docs" / "requirements.md").exists():
                    no_req.append(d.name)
        return {
            "available": True,
            "stray": stray,
            "stale_scratch": stale_scratch,
            "projects_without_requirements": no_req,
        }
    return _cached_counts("inspect", _calc)
