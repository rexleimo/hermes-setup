"""window-manager.js 前端回归测试（pytest 包装）。

零构建链决策下无前端测试框架：用 Node 直接跑 tests/js/wm-test.js
（自带 mini-DOM shim，47 断言：WM 生命周期 / 任务条 chip 归属 / 媒体续播 /
viewer 注册表 / openFile 扁平描述符 / XSS 转义 / 工厂异常降级）。
Node 不可用时跳过（CI 与开发机均有 Node；纯 Python 环境降级为已知边界）。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WM_TEST_JS = REPO / "tests" / "js" / "wm-test.js"
WM_JS = REPO / "app" / "web" / "static" / "js" / "window-manager.js"


def test_window_manager_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用：前端回归测试需要 Node 运行时")
    assert WM_JS.exists(), "window-manager.js 缺失"
    r = subprocess.run([node, str(WM_TEST_JS)], capture_output=True,
                       text=True, timeout=60, cwd=str(REPO))
    assert r.returncode == 0, (
        "WM 前端回归测试失败：\n" + r.stdout + "\n" + r.stderr)
    assert "passed" in r.stdout, r.stdout
