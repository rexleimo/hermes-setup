"""文件工作台窗口化前端回归（0.8.38：列表首块缺 .wb-pblock-list）。

零构建链决策下无前端测试框架：用 Node 直接跑 tests/js/wb-virt-test.js
（自带 jsdom，加载真实 app.js 做网格 / 列表首块类名 / boost 换页三组受控断言）。
Node 或 jsdom 不可用时跳过（CI 与开发机均有 Node 与 node_modules/jsdom）。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WB_VIRT_TEST_JS = REPO / "tests" / "js" / "wb-virt-test.js"


def test_wb_virt_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用：前端回归测试需要 Node 运行时")
    assert WB_VIRT_TEST_JS.exists(), "wb-virt-test.js 缺失"
    r = subprocess.run([node, str(WB_VIRT_TEST_JS)], capture_output=True,
                       text=True, timeout=60, cwd=str(REPO))
    assert r.returncode == 0, (
        "窗口化前端回归失败：\n" + r.stdout + "\n" + r.stderr)
    assert "passed" in r.stdout and "0 failed" in r.stdout, r.stdout
