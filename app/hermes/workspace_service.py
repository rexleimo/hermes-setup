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
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core import appsettings
from app.hermes import engineering_service as eng

BUCKETS = ("projects", "downloads", "scratch", "archive")

TEXT_EXTS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".log", ".csv"}
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh", ".go",
             ".rs", ".java", ".sql", ".env"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
PREVIEW_LIMIT = 512 * 1024
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


def _entry(base: Path, p: Path) -> Entry:
    st = p.stat()
    return Entry(name=p.name, rel=rel_of(p), is_dir=p.is_dir(),
                 size=st.st_size, mtime=st.st_mtime)


def list_dir(rel: str = "") -> list[Entry]:
    target = resolve_rel(rel)
    if not target.exists():
        raise NotFound(f"目录不存在：{rel or '/'}")
    if not target.is_dir():
        raise WorkspaceError("不是目录")
    entries = [_entry(target, p) for p in target.iterdir() if not p.name.startswith(".")]
    return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))


def breadcrumbs(rel: str = "") -> list[tuple[str, str]]:
    """[(显示名, 路径)]，含根。"""
    parts = [p for p in (rel or "").split("/") if p]
    out = [("(工作区根)", "")]
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
    if ext == ".pdf":
        return "pdf"
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
    return rel_of(target)


# ---------------------------------------------------------------------------
# 归位巡检（W4 提前做了只读摘要，执法建议 M2 再补）
# ---------------------------------------------------------------------------

def inspect_summary() -> dict:
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
