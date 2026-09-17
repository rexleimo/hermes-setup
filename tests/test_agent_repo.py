"""root/FHS 安装布局回归：仓库目录经可执行文件反推可达。

背景：root 安装把 hermes-agent 放到 /usr/local/lib（而非家目录），
且 /usr/local/bin/hermes 多为软链；直连 paths.agent_repo 会落空，
连带 MCP/技能/插件目录与 venv 探测一起失效。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.hermes import paths as paths_mod
from app.hermes.paths import HermesPaths


def _make_repo(root: Path, py_rel: str) -> Path:
    """伪造一份最小 hermes-agent 检出：pyproject + venv python。"""
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "pyproject.toml").touch()
    py = repo / "venv" / py_rel
    py.parent.mkdir(parents=True, exist_ok=True)
    py.touch()
    return repo


def test_resolve_agent_repo_direct(tmp_path):
    home = tmp_path / "home"
    repo = home / "hermes-agent"
    repo.mkdir(parents=True)
    assert paths_mod.resolve_agent_repo(HermesPaths(home=home, bin=None)) == repo


def test_resolve_agent_repo_via_bin_parent(tmp_path):
    """bin 直指 venv 内可执行文件（无软链）：逐级上找。"""
    if sys.platform == "win32":
        repo = _make_repo(tmp_path, "Scripts/python.exe")
        exe = repo / "venv" / "Scripts" / "hermes.exe"
    else:
        repo = _make_repo(tmp_path, "bin/python")
        exe = repo / "venv" / "bin" / "hermes"
    exe.touch()
    paths = HermesPaths(home=tmp_path / "home", bin=str(exe))
    assert paths_mod.resolve_agent_repo(paths) == repo


@pytest.mark.skipif(sys.platform == "win32", reason="需要 POSIX 软链")
def test_resolve_agent_repo_via_symlink(tmp_path):
    """FHS 布局：/usr/local/bin/hermes 软链进 repo venv，必须解引用。"""
    repo = _make_repo(tmp_path, "bin/python")
    target = repo / "venv" / "bin" / "hermes"
    target.touch()
    linkdir = tmp_path / "bin"
    linkdir.mkdir()
    link = linkdir / "hermes"
    link.symlink_to(target)
    paths = HermesPaths(home=tmp_path / "home", bin=str(link))
    assert paths_mod.resolve_agent_repo(paths) == repo


def test_resolve_agent_repo_none_when_absent(tmp_path):
    paths = HermesPaths(home=tmp_path / "home", bin=None)
    assert paths_mod.resolve_agent_repo(paths) is None


@pytest.mark.skipif(sys.platform == "win32", reason="FHS 落点仅 POSIX 有意义")
def test_resolve_agent_repo_fhs_static(tmp_path, monkeypatch):
    """官方 root 固定落点兜底（bin 指向他处、直连缺失时）。"""
    fake = tmp_path / "usr" / "local" / "lib" / "hermes-agent"
    fake.mkdir(parents=True)
    (fake / "pyproject.toml").touch()
    monkeypatch.setattr(paths_mod, "_FHS_CANDIDATES", (str(fake),))
    paths = HermesPaths(home=tmp_path / "home",
                        bin=str(tmp_path / "nowhere" / "hermes"))
    assert paths_mod.resolve_agent_repo(paths) == fake


def test_resolve_agent_repo_from_shim(tmp_path):
    """官方 bash 包裹脚本：从 exec 行反推仓库（全平台，纯文件解析）。"""
    repo = tmp_path / "opt" / "custom" / "hermes-agent"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").touch()
    shim = tmp_path / "hermes"
    shim.write_text(
        '#!/usr/bin/env bash\nunset PYTHONPATH\n'
        f'exec "{repo.as_posix()}/venv/bin/python" "{repo.as_posix()}/hermes" "$@"\n',
        encoding="utf-8")
    paths = HermesPaths(home=tmp_path / "home", bin=str(shim))
    assert paths_mod.resolve_agent_repo(paths) == repo


def test_agent_python_prefers_resolved_repo(tmp_path):
    """venv 解释器同样吃反推仓库（FHS 下直连 venv 不存在）。"""
    from app.hermes import onboarding

    repo = tmp_path / "opt" / "custom" / "hermes-agent"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").touch()
    py = repo / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.touch()
    shim = tmp_path / "hermes"
    shim.write_text(
        '#!/usr/bin/env bash\n'
        f'exec "{repo.as_posix()}/venv/bin/python" "{repo.as_posix()}/hermes" "$@"\n',
        encoding="utf-8")
    paths = HermesPaths(home=tmp_path / "home", bin=str(shim))
    assert onboarding.agent_python(paths) == py


@pytest.mark.skipif(sys.platform == "win32", reason="需要 POSIX 软链")
def test_agent_python_resolves_symlinked_bin(tmp_path):
    from app.hermes import onboarding

    repo = _make_repo(tmp_path, "bin/python")
    target = repo / "venv" / "bin" / "hermes"
    target.touch()
    linkdir = tmp_path / "bin"
    linkdir.mkdir()
    link = linkdir / "hermes"
    link.symlink_to(target)
    paths = HermesPaths(home=tmp_path / "home", bin=str(link))
    assert onboarding.agent_python(paths) == repo / "venv" / "bin" / "python"


def test_install_deps_uses_resolved_repo(tmp_path, monkeypatch):
    """install_deps 必须用反推到的仓库做 cwd（曾被直连路径覆盖导致后台任务起不来）。"""
    from app.hermes import installer, onboarding

    if sys.platform == "win32":
        repo = _make_repo(tmp_path, "Scripts/python.exe")
    else:
        repo = _make_repo(tmp_path, "bin/python")
    exe = repo / "venv" / ("Scripts/hermes.exe" if sys.platform == "win32"
                           else "bin/hermes")
    exe.touch()
    captured = {}

    def fake_submit(kind, command, *, shell=True, cwd=None):
        captured["cwd"] = cwd
        return 7

    monkeypatch.setattr(installer, "submit", fake_submit)
    paths = HermesPaths(home=tmp_path / "home", bin=str(exe))
    assert onboarding.install_deps(paths) == 7
    assert captured["cwd"] == str(repo)


def test_catalog_bases_follow_resolved_repo(tmp_path):
    """MCP/技能/插件目录都走反推仓库，而非写死的家目录拼接。"""
    from app.hermes import mcp_service, plugins_service, skills_service

    if sys.platform == "win32":
        repo = _make_repo(tmp_path, "Scripts/python.exe")
        exe = repo / "venv" / "Scripts" / "hermes.exe"
    else:
        repo = _make_repo(tmp_path, "bin/python")
        exe = repo / "venv" / "bin" / "hermes"
    exe.touch()
    paths = HermesPaths(home=tmp_path / "home", bin=str(exe))
    assert skills_service.catalog_base(paths) == repo / "optional-skills"
    assert mcp_service.catalog_dir(paths) == repo / "optional-mcps"
    plugdir = repo / "plugin-catalog"
    plugdir.mkdir()
    (plugdir / "x.yaml").write_text("name: x\n", encoding="utf-8")
    assert [e.name for e in plugins_service.catalog(paths)] == ["x"]
