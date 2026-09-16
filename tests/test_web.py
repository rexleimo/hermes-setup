"""Web 集成测试：认证流、权限、CSRF、页面可达、供应商/渠道/链路路由。"""
from __future__ import annotations

from app.core import ratelimit
from app.core.appsettings import set_totp
from tests.conftest import csrf_of, login

# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------

def test_setup_flow_then_admin_exists(client):
    resp = client.get("/setup")
    assert resp.status_code in (200, 303)


def test_anonymous_redirects_to_login(client):
    for path in ("/", "/providers", "/channels", "/audit", "/users"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302, path
        assert "/login" in resp.headers["location"]


def test_login_wrong_password(admin):
    token = csrf_of(admin)
    resp = admin.post("/login", data={"username": "admin", "password": "nope",
                                      "_csrf": token})
    assert resp.status_code == 401


def test_login_without_csrf_is_403(admin):
    resp = admin.post("/login", data={"username": "admin", "password": "x"},
                      follow_redirects=False)
    assert resp.status_code == 403


def test_login_success_and_dashboard(admin):
    login(admin, "admin", "Sup3rSecure!x")
    resp = admin.get("/")
    assert resp.status_code == 200
    assert "仪表盘" in resp.text


def test_logout(admin):
    login(admin, "admin", "Sup3rSecure!x")
    token = csrf_of(admin)
    resp = admin.post("/logout", data={"_csrf": token}, follow_redirects=False)
    assert resp.status_code == 303
    resp = admin.get("/", follow_redirects=False)
    assert resp.status_code == 302


def test_login_lockout(admin):
    token = csrf_of(admin)
    for _ in range(5):
        admin.post("/login", data={"username": "admin", "password": "bad",
                                   "_csrf": token})
    ratelimit.record("testclient", "admin", success=False)
    resp = admin.post("/login", data={"username": "admin",
                                      "password": "Sup3rSecure!x", "_csrf": token})
    assert resp.status_code == 429
    assert "锁定" in resp.text


def test_totp_challenge(admin):
    import re

    import pyotp
    from app.core.security import verify_totp

    login(admin, "admin", "Sup3rSecure!x")
    token = csrf_of(admin)
    # 走真实绑定流程：生成密钥 → 提交验证码
    page = admin.get("/2fa/setup").text
    secret = re.search(r'<code style="font-size:15px;">([A-Z2-7]+)</code>', page).group(1)
    resp = admin.post("/2fa/enable", data={"code": pyotp.TOTP(secret).now(),
                                           "_csrf": token}, follow_redirects=False)
    assert resp.status_code == 303
    # 本会话已验证 → 可访问
    assert admin.get("/").status_code == 200
    # 重新登录 → 必须过挑战
    admin.post("/logout", data={"_csrf": csrf_of(admin)}, follow_redirects=False)
    login(admin, "admin", "Sup3rSecure!x")  # 该 helper 断言 303 → 跳挑战页
    code = pyotp.TOTP(secret).now()
    token = csrf_of(admin)
    resp = admin.post("/login/2fa", data={"code": code, "_csrf": token},
                      follow_redirects=False)
    assert resp.status_code == 303
    assert admin.get("/").status_code == 200


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------

def test_operator_cannot_access_users(admin):
    from app.core.appsettings import create_user
    from tests.conftest import ADMIN_PASS

    create_user("op1", ADMIN_PASS, role="operator")
    login(admin, "op1", ADMIN_PASS)
    assert admin.get("/users").status_code == 403
    assert admin.get("/settings").status_code == 403
    assert admin.get("/providers").status_code == 200


def test_operator_cannot_create_provider(admin):
    from app.core.appsettings import create_user
    from tests.conftest import ADMIN_PASS

    create_user("op2", ADMIN_PASS, role="operator")
    login(admin, "op2", ADMIN_PASS)
    token = csrf_of(admin)
    resp = admin.post("/providers/new",
                      data={"kind": "custom", "pid": "nope", "name": "n",
                            "protocol": "openai_chat", "base_url": "https://x/v1",
                            "env_key": "K", "api_key": "", "default_model": "",
                            "note": "", "_csrf": token})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 页面与业务路由
# ---------------------------------------------------------------------------

def test_all_pages_render(admin):
    login(admin, "admin", "Sup3rSecure!x")
    pages = ["/", "/service", "/providers", "/providers/new", "/channels",
             "/chains", "/audit", "/users", "/settings", "/settings/account",
             "/catalog", "/2fa/setup", "/service/pill", "/service/status"]
    for p in pages:
        resp = admin.get(p)
        assert resp.status_code == 200, f"{p} -> {resp.status_code}"


def test_provider_crud_via_web(admin):
    login(admin, "admin", "Sup3rSecure!x")
    token = csrf_of(admin)
    resp = admin.post("/providers/new", data={
        "kind": "custom", "pid": "web-p", "name": "WebP",
        "protocol": "openai_chat", "base_url": "https://api.webp.dev/v1",
        "env_key": "", "api_key": "sk-web", "default_model": "m1",
        "note": "", "_csrf": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert admin.get("/providers/web-p").status_code == 200

    resp = admin.post("/providers/web-p/set-main",
                      data={"model_id": "m1", "context_length": "100000",
                            "_csrf": token}, follow_redirects=False)
    assert resp.status_code == 303

    resp = admin.post("/providers/web-p/alias",
                      data={"model_id": "m1", "alias": "quick", "_csrf": token},
                      follow_redirects=False)
    assert resp.status_code == 303

    # 列表与链路页出现新供应商
    assert "WebP" in admin.get("/providers").text
    assert "quick" in admin.get("/chains").text

    # 删除（确认词 = pid）
    resp = admin.post("/providers/web-p/delete",
                      data={"confirm": "wrong", "_csrf": token})
    assert resp.status_code == 400
    resp = admin.post("/providers/web-p/delete",
                      data={"confirm": "web-p", "_csrf": token},
                      follow_redirects=False)
    assert resp.status_code == 303


def test_channel_save_via_web(admin):
    login(admin, "admin", "Sup3rSecure!x")
    token = csrf_of(admin)
    resp = admin.post("/channels/feishu/save", data={
        "enabled": "on", "env_FEISHU_APP_ID": "cli_1",
        "env_FEISHU_APP_SECRET": "s", "_csrf": token}, follow_redirects=False)
    assert resp.status_code == 303
    text = admin.get("/channels").text
    assert "已启用" in text


def test_audit_records_actions(admin):
    login(admin, "admin", "Sup3rSecure!x")
    token = csrf_of(admin)
    admin.post("/providers/new", data={
        "kind": "custom", "pid": "aud", "name": "A", "protocol": "openai_chat",
        "base_url": "https://a/v1", "env_key": "KA", "api_key": "",
        "default_model": "", "note": "", "_csrf": token})
    text = admin.get("/audit").text
    assert "provider_create" in text


def test_fallback_chain_via_web(admin):
    login(admin, "admin", "Sup3rSecure!x")
    token = csrf_of(admin)
    admin.post("/chains/fallback/add", data={
        "provider": "anthropic", "model": "claude-opus-4-6", "_csrf": token})
    assert "claude-opus-4-6" in admin.get("/chains").text
    admin.post("/chains/fallback/remove", data={"position": "1", "_csrf": token})


def test_css_button_specificity_guard():
    """回归防护：按钮基态不得用 a.btn（特异性会压过 .btn-primary 变体，
    导致链接型主按钮白底白字）；empty-state 大图标不得用后代选择器
    污染按钮内的小图标。"""
    import pathlib

    css = (pathlib.Path(__file__).resolve().parents[1]
           / "app/web/static/css/admin.css").read_text(encoding="utf-8")
    assert "\na.btn {" not in css and not css.startswith("a.btn {")
    assert ".empty-state .icon" not in css
