"""供应商模型目录拉取：上游不可达时的错误可��性回归。

背景：拉取/测试失败曾返回 HTTP 502，而 htmx 对非 2xx 默认不 swap，
导致 _fetch_result.html 里的真实原因从未进入页面——用户只看到裸 502，
误以为网关故障。此处锁定「业务失败仍返回 200 + 原因进正文」。
"""
import httpx
import pytest

from app.hermes import providers_service as svc
from conftest import csrf_of


ADMIN = {"username": "admin"}


def _make_provider(client, pid="probe"):
    svc.create_provider(pid=pid, name="探针供应商", kind="custom",
                        preset_id=None, protocol="openai_chat",
                        base_url="https://api.probe.invalid/v1", env_key="PROBE_KEY",
                        api_key="", default_model="m1", note="")


def _post(client, path):
    return client.post(path, data={"_csrf": csrf_of(client)},
                       headers={"HX-Request": "true"})


def test_fetch_models_unreachable_returns_200_with_reason(logged_in, monkeypatch):
    """上游连不通：必须 200 + 正文含可执行原因，绝不能是裸 502。"""
    _make_provider(logged_in)

    async def boom(*a, **kw):
        raise svc.model_catalog.ModelCatalogError(
            "域名解析失败（DNS）：服务器无法解析该 base_url 域名，请检查拼写与服务器 DNS｜目标：https://api.probe.invalid/v1/models")

    monkeypatch.setattr(svc, "fetch_remote_models", boom)
    resp = _post(logged_in, "/providers/probe/fetch")

    assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}"
    assert "域名解析失败" in resp.text, "真实失败原因必须渲染进页面"
    assert "alert-danger" in resp.text


def test_fetch_models_http_error_surfaces_status(logged_in, monkeypatch):
    """上游返回 401/403（key 无效）也要把状态码透出，不能吞掉。"""
    _make_provider(logged_in)

    async def unauthorized(*a, **kw):
        raise svc.model_catalog.ModelCatalogError("HTTP 401：invalid api key")

    monkeypatch.setattr(svc, "fetch_remote_models", unauthorized)
    resp = _post(logged_in, "/providers/probe/fetch")

    assert resp.status_code == 200
    assert "HTTP 401" in resp.text


def test_test_connection_failure_returns_200(logged_in, monkeypatch):
    """「测试连接」失败同样返回 200，_test_result.html 走失败分支渲染。"""
    _make_provider(logged_in)

    async def boom(*a, **kw):
        raise svc.model_catalog.ModelCatalogError("连接超时（6s）")

    monkeypatch.setattr(svc, "fetch_remote_models", boom)
    resp = _post(logged_in, "/providers/probe/test")

    assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}"
    assert "连接超时" in resp.text


def test_success_paths_still_200(logged_in, monkeypatch):
    """成功路径不受影响。"""
    _make_provider(logged_in)

    async def ok(*a, **kw):
        return [svc.model_catalog.ModelInfo(id="gpt-probe")]

    monkeypatch.setattr(svc, "fetch_remote_models", ok)
    assert _post(logged_in, "/providers/probe/fetch").status_code == 200
    assert _post(logged_in, "/providers/probe/test").status_code == 200


@pytest.mark.parametrize("exc,expect", [
    (httpx.ConnectTimeout(""), "连接超时"),
    (httpx.ReadTimeout(""), "响应超时"),
    (httpx.ProxyError("proxy down"), "代理连接失败"),
    (httpx.ConnectError("[Errno -2] Name or service not known"), "域名解析失败"),
    (httpx.ConnectError("Connection refused"), "连接被拒绝"),
])
def test_network_error_diagnosis(exc, expect):
    """错误分诊：不同网络故障要给出不同、可执行的原因。"""
    from app.hermes.model_catalog import _describe_network_error
    msg = _describe_network_error(exc)
    assert expect in msg, f"{type(exc).__name__} -> {msg}"