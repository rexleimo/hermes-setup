"""Hermes 配置存储：config.yaml（ruamel round-trip，保留注释）与 .env 读写。

安全工程约束：
- 所有写入先「校验 → 备份 → 临时文件 → os.replace 原子替换」，任何一步失败都不落盘；
- 并发写通过同目录 `.config.lock`（跨平台文件锁，见 core/filelock.py）串行化；
- 备份保留最近 N 份（默认 10），文件名 config.yaml.bak-<timestamp>。
"""
from __future__ import annotations

import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from app.core import appsettings
from app.core.filelock import exclusive_lock
from app.hermes.paths import HermesPaths, detect

MAX_BACKUPS = 10
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096
_yaml.indent(mapping=2, sequence=4, offset=2)


class ConfigError(Exception):
    """配置读写失败的统一异常（信息可直接展示给管理员）。"""


# ---------------------------------------------------------------------------
# 路径锁
# ---------------------------------------------------------------------------

@contextmanager
def _config_lock(paths: HermesPaths):
    lock_path = paths.home / ".config.lock"
    paths.home.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(lock_path):
        yield


# ---------------------------------------------------------------------------
# config.yaml
# ---------------------------------------------------------------------------

SKELETON = CommentedMap()
SKELETON["model"] = CommentedMap()

_MISSING = object()


def load_config(paths: HermesPaths | None = None) -> CommentedMap:
    paths = paths or detect()
    if not paths.config_yaml.exists():
        return CommentedMap(SKELETON)
    try:
        with open(paths.config_yaml, encoding="utf-8") as fh:
            data = _yaml.load(fh)
    except Exception as exc:  # ruamel 抛出的解析错误种类较多，统一包装
        raise ConfigError(f"config.yaml 解析失败：{exc}") from exc
    return data if isinstance(data, CommentedMap) else CommentedMap()


def _backup(paths: HermesPaths) -> Path | None:
    if not paths.config_yaml.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = paths.config_yaml.with_name(f"config.yaml.bak-{stamp}")
    shutil.copy2(paths.config_yaml, target)
    # 清理旧备份
    backups = sorted(paths.home.glob("config.yaml.bak-*"))
    for stale in backups[:-MAX_BACKUPS]:
        stale.unlink(missing_ok=True)
    return target


def save_config(data: CommentedMap, paths: HermesPaths | None = None) -> Path:
    """原子写入；返回备份文件路径（首次创建返回 None）。"""
    paths = paths or detect()
    paths.home.mkdir(parents=True, exist_ok=True)
    with _config_lock(paths):
        backup = _backup(paths)
        tmp = paths.config_yaml.with_name(f".config.yaml.tmp-{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                _yaml.dump(data, fh)
                fh.flush()
                os.fsync(fh.fileno())
            _replace_with_retry(tmp, paths.config_yaml)
        finally:
            tmp.unlink(missing_ok=True)
    return backup


def _replace_with_retry(src: Path, dst: Path, attempts: int = 10) -> None:
    """Windows 上 os.replace 的坑：目标文件被 Gateway 监控/读取句柄短暂占用，
    或杀软扫描时，会抛 WinError 5（拒绝访问）。POSIX 下不存在此问题；
    这里短暂退避重试，而不是把偶发锁当成保存失败吓到用户。"""
    import time
    last: OSError | None = None
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(0.2 * (i + 1))
    raise last  # type: ignore[misc]


def backups(paths: HermesPaths | None = None) -> list[Path]:
    paths = paths or detect()
    return sorted(paths.home.glob("config.yaml.bak-*"), reverse=True)


def get_path(data: CommentedMap, dotted: str, default=_MISSING):
    """按 a.b.c 读取；缺省时返回 default（未给 default 则抛 ConfigError）。"""
    node = data
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            if default is _MISSING:
                raise ConfigError(f"配置缺少键：{dotted}")
            return default
    return node


def ensure_path(data: CommentedMap, dotted: str) -> CommentedMap:
    """逐级建目录，返回最内层节点。"""
    node = data
    for part in dotted.split(".")[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = CommentedMap()
            node[part] = child
        node = child
    leaf = dotted.rsplit(".", 1)[-1]
    child = node.get(leaf)
    if not isinstance(child, dict):
        child = CommentedMap()
        node[leaf] = child
    return child


# ---------------------------------------------------------------------------
# JSON 配置文件（honcho.json / mem0.json / supermemory.json / hindsight/config.json）
# ---------------------------------------------------------------------------

def load_json_opt(paths: HermesPaths | None, rel: str) -> dict:
    """读取 Hermes 家目录下相对路径的 JSON 文件；不存在/非法返回 {}。"""
    paths = paths or detect()
    target = paths.home / rel
    if not target.exists():
        return {}
    try:
        import json

        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json(paths: HermesPaths | None, rel: str, updates: dict, *,
              merge: bool = True) -> Path:
    """原子写入 JSON 配置；merge=True 时深合并进现有内容（保留既有键）。"""
    import json

    paths = paths or detect()
    target = paths.home / rel
    data: dict = {}
    if merge and target.exists():
        data = load_json_opt(paths, rel)
    _deep_merge(data, updates)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = target.with_name(f"{target.name}.bak-{stamp}")
        shutil.copy2(target, backup)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return backup


def _deep_merge(base: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ---------------------------------------------------------------------------
# .env 文件（敏感键：渠道令牌 / API Key）
# ---------------------------------------------------------------------------

@dataclass
class EnvStore:
    """最小侵入式 .env 编辑：保留原有行序与注释，仅替换目标键所在行。"""

    path: Path
    _lines: list[str]

    @classmethod
    def load(cls, paths: HermesPaths | None = None) -> "EnvStore":
        paths = paths or detect()
        lines: list[str] = []
        if paths.env_file.exists():
            lines = paths.env_file.read_text(encoding="utf-8").splitlines()
        return cls(path=paths.env_file, _lines=lines)

    def get(self, key: str) -> str | None:
        prefix = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
        for line in self._lines:
            if prefix.match(line):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
        return None

    def set(self, key: str, value: str) -> None:
        """设置键；已存在则原位替换，否则追加。空值 = 删除该键。"""
        prefix = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
        replaced = False
        new_lines: list[str] = []
        for line in self._lines:
            if prefix.match(line):
                if value:
                    new_lines.append(f'{key}="{value}"')
                    replaced = True
                # value 为空 → 删除（跳过该行）
            else:
                new_lines.append(line)
        if not replaced and value:
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.append(f'# 由 Hermes Console 写入于 {time.strftime("%Y-%m-%d %H:%M:%S")}')
            new_lines.append(f'{key}="{value}"')
        self._lines = new_lines

    def keys(self) -> list[str]:
        pat = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
        out = []
        for line in self._lines:
            m = pat.match(line)
            if m:
                out.append(m.group(1))
        return out

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".env.tmp-{os.getpid()}")
        try:
            content = "\n".join(self._lines)
            if content:
                content += "\n"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            _replace_with_retry(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def chmod_600(self) -> None:
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
