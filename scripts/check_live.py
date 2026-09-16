"""对运行中的 Console（默认 127.0.0.1:8420）做上线后自检.

用临时管理员会话验证关键页面与修复点是否真的生效；
只在平台自身 SQLite 里建/删临时行，不触碰 ~/.hermes。

用法: uv run python scripts/check_live.py [base_url]
退出码 0 = 全部通过。
"""
import re
import sys

import httpx

from app.core import db, sessions as ss
from app.core.appsettings import get_by_username

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8420"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "OK " if ok else "FAIL"
    print(f"[{mark}] {name}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)


def main() -> int:
    rows = db.query("SELECT * FROM users WHERE role='admin' LIMIT 1")
    user = rows[0] if rows else None
    if user is None:
        print("没有管理员账号，无法自检（先完成初始化）")
        return 2
    session = ss.create(user["id"], ip="127.0.0.1", user_agent="check_live")
    ss.mark_two_fa(session.id)
    client = httpx.Client(
        base_url=BASE, cookies={"hermes_console_session": session.id}, timeout=15
    )

    # 页面可达性
    for path in ("/", "/service", "/providers", "/channels", "/memory",
                 "/engineering", "/chains", "/users", "/settings",
                 "/skills", "/mcp", "/plugins"):
        r = client.get(path)
        check(f"GET {path}", r.status_code == 200, str(r.status_code))

    # 行为回归：连字符用户名必须通过校验（临时账号，验后即删）
    page = client.get("/users").text
    m = re.search(r'name="_csrf" value="([^"]*)"', page)
    if not m:
        check("用户页 CSRF 标记", False)
    else:
        r = client.post(
            "/users",
            data={"username": "zz-check-live", "password": "Checklive123",
                  "role": "operator", "_csrf": m.group(1)},
            follow_redirects=False,
        )
        created = r.status_code == 303
        check("连字符用户名创建", created, str(r.status_code))
        db.execute("DELETE FROM users WHERE username='zz-check-live'")

    # 渠道：QQ 高级编辑器存在 + 凭证警示逻辑字段在页面呈现
    qq = client.get("/channels/qqbot").text
    check("QQ 高级 JSON 编辑器", "advanced_extra" in qq)

    # 记忆：依赖自动安装入口存在
    mem = client.get("/memory/provider/honcho").text
    check("记忆依赖自动安装按钮", "自动安装依赖" in mem)

    ss.destroy(session.id)
    client.close()

    if failures:
        print(f"\n{len(failures)} 项未通过: {failures}")
        return 1
    print("\n全部自检通过（临时账号/会话已清理）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
