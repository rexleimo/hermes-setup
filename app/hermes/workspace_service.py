"""工作区文件服务（W3 体验版）：jail 化的浏览 / 预览 / 下载 / 打包 / 上传 / 整理。

单一契约 —— 根目录永远读 EngSettings.workspace（与工程注入层同源），
禁止另设第二个路径配置项。所有外部传入的相对路径必须经 resolve_rel() 做
jail 校验（resolve 后仍在根内，symlink 逃逸同样被拦）。
执法策略（见 FILE_WORKBENCH_SPEC.md §1）：不在文件系统层拦截 Agent，
写入口只有「上传→downloads/」与「整理→archive/」两类，永不覆盖。
"""
from __future__ import annotations

import io
import re
import shutil
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

def root() -> Path:
    raw = (eng.load_settings().workspace or eng.DEFAULT_WORKSPACE).strip()
    return validate_root(raw)


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
    if sort == "time":
        key = lambda e: (not e.is_dir, -e.mtime)
    elif sort == "size":
        key = lambda e: (not e.is_dir, -e.size)
    else:
        key = lambda e: (not e.is_dir, e.name.lower())
    return sorted(entries, key=key)


def _walk_files() -> list[Entry]:
    base = root()
    out = []
    for p in base.rglob("*"):
        try:
            parts = p.relative_to(base).parts
            if p.is_file() and not any(part.startswith(".") for part in parts):
                out.append(_entry(p, base))
        except OSError:
            continue
    return out


def list_collection(cat: str, limit: int = COLLECTION_LIMIT) -> list[Entry]:
    """智能集合：全工作区按类型聚合（虚拟视图，文件不动、不复制）。"""
    if cat not in CATEGORY_MAP:
        raise WorkspaceError(f"未知集合：{cat}")
    exts = CATEGORY_MAP[cat][2]
    files = [e for e in _walk_files() if e.ext in exts]
    return _sort_entries(files, "time")[:limit]


def list_recent(limit: int = 100) -> list[Entry]:
    files = _walk_files()
    return _sort_entries(files, "time")[:limit]


# ---------------------------------------------------------------------------
# 计数缓存（W6）：category/location/inspect 每次都是全工作区递归遍历，Windows
# 上一转就是秒级，而它们在每次页面渲染、每次 boosted 导航都被调用。加进程内
# TTL 缓存，写操作（上传/归档/重命名/删除/复制/新建/移动/保存）后主动失效。
# ---------------------------------------------------------------------------

_COUNTS_TTL = 30.0
_counts_cache: dict[tuple[str, str], tuple[float, object]] = {}


def invalidate_caches() -> None:
    _counts_cache.clear()


def _cached_counts(key: str, fn):
    k = (str(root()), key)
    hit = _counts_cache.get(k)
    if hit is not None and time.monotonic() - hit[0] < _COUNTS_TTL:
        return hit[1]
    val = fn()
    _counts_cache[k] = (time.monotonic(), val)
    return val


def category_counts() -> list[tuple[str, str, str, int]]:
    """[(key, 标签, emoji, 数量)]，侧栏用。"""
    def _calc() -> list[tuple[str, str, str, int]]:
        counts = {k: 0 for k, *_ in CATEGORIES}
        for e in _walk_files():
            c = e.category
            if c:
                counts[c] += 1
        return [(k, CATEGORY_MAP[k][0], CATEGORY_MAP[k][1], counts[k]) for k in counts]
    return _cached_counts("category", _calc)


def location_counts() -> list[tuple[str, int]]:
    """标准目录（位置）的文件总数，侧栏用。"""
    def _calc() -> list[tuple[str, int]]:
        base = root()
        out = []
        for d in BUCKETS:
            p = base / d
            n = sum(1 for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0
            out.append((d, n))
        return out
    return _cached_counts("location", _calc)


def list_dir(rel: str = "", sort: str = "name") -> list[Entry]:
    target = resolve_rel(rel)
    if not target.exists():
        raise NotFound(f"目录不存在：{rel or '/'}")
    if not target.is_dir():
        raise WorkspaceError("不是目录")
    entries = []
    base = root()
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
    data = path.read_bytes()[:PREVIEW_LIMIT]
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
    """全局搜索：按文件名子串匹配整个工作区（隐藏目录/.trash 除外）。

    rglob 是惰性生成器，凑满 limit 即停，不强制全树扫完。"""
    q = (query or "").strip().lower()
    if not q:
        return []
    base = root()
    out: list[Entry] = []
    for p in base.rglob("*"):
        if len(out) >= limit:
            break
        try:
            parts = p.relative_to(base).parts
        except OSError:
            continue
        if any(part.startswith(".") for part in parts):
            continue
        if q in p.name.lower():
            try:
                out.append(_entry(p, base))
            except OSError:
                continue
    return out


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
