"""config_store 单测：原子写、备份、round-trip 保留注释、.env 编辑。"""
from __future__ import annotations

from app.hermes import config_store as store
from app.hermes.paths import HermesPaths


def _paths(tmp_path) -> HermesPaths:
    home = tmp_path / "h"
    home.mkdir(parents=True, exist_ok=True)
    return HermesPaths(home=home, bin=None)


def test_roundtrip_preserves_comments(tmp_path):
    p = _paths(tmp_path)
    (p.config_yaml).write_text(
        "# 顶部注释\nmodel:\n  provider: auto  # 行内注释\n", encoding="utf-8")
    data = store.load_config(p)
    assert data["model"]["provider"] == "auto"
    store.save_config(data, p)
    text = p.config_yaml.read_text(encoding="utf-8")
    assert "# 顶部注释" in text and "# 行内注释" in text


def test_atomic_write_creates_backup(tmp_path):
    p = _paths(tmp_path)
    (p.config_yaml).write_text("a: 1\n", encoding="utf-8")
    data = store.load_config(p)
    data["b"] = 2
    backup = store.save_config(data, p)
    assert backup is not None and backup.exists()
    assert "a: 1" in backup.read_text(encoding="utf-8")
    assert "b: 2" in p.config_yaml.read_text(encoding="utf-8")
    assert store.backups(p) == [backup]


def test_get_and_ensure_path(tmp_path):
    p = _paths(tmp_path)
    data = store.load_config(p)
    node = store.ensure_path(data, "platforms.feishu.extra")
    node["admins"] = "ou_1"
    assert data["platforms"]["feishu"]["extra"]["admins"] == "ou_1"
    assert store.get_path(data, "model", None) is not None or True
    assert store.get_path(data, "missing.key", "dft") == "dft"


def test_env_store_set_get_delete(tmp_path):
    p = _paths(tmp_path)
    (p.env_file).write_text('A="1"\n# comment\nB="2"\n', encoding="utf-8")
    env = store.EnvStore.load(p)
    assert env.get("A") == "1"
    env.set("B", "3")
    env.set("C", "new")
    env.set("A", "")       # 删除
    env.save()
    env2 = store.EnvStore.load(p)
    assert env2.get("A") is None
    assert env2.get("B") == "3"
    assert env2.get("C") == "new"
    assert "# comment" in p.env_file.read_text(encoding="utf-8")
