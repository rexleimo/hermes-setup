"""模型目录拉取：按 API 协议调用各供应商的 list-models 端点。

- OpenAI Chat / Responses 兼容：GET {base}/models（Bearer）
- Anthropic Messages：GET {base}/v1/models（x-api-key + anthropic-version）
- Google Gemini：GET v1beta/models（?key=），并带回 inputTokenLimit 上下文长度
- AWS Bedrock：模型需手动录入（凭证为 AWS SigV4，不在浏览器可拉取范围内）
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

TIMEOUT = httpx.Timeout(15.0, connect=6.0)
UA = "HermesConsole/0.1"


class ModelCatalogError(Exception):
    pass


def _describe_network_error(exc: Exception) -> str:
    """把 httpx 异常翻成可执行的中文原因。

    此前统一报「网络错误：ConnectError」，运维看不出到底是 DNS 不通、
    连接被拒还是超时——排查 502 全靠猜。这里按异常类型分诊，并统一
    带上 endpoint，让「服务器连不上供应商」这类问题一眼可判。
    """
    detail = str(exc)
    if isinstance(exc, httpx.ConnectTimeout):
        return "连接超时（6s）：服务器到该供应商网络不通——通常是出网被限制或需走代理"
    if isinstance(exc, httpx.ReadTimeout):
        return "响应超时（15s）：供应商端点无响应，请核对 base_url 是否指向正确的 API 根路径"
    if isinstance(exc, httpx.ProxyError):
        return f"代理连接失败：{detail[:150]}"
    if isinstance(exc, httpx.TooManyRedirects):
        return f"重定向次数过多：{detail[:150]}"
    if isinstance(exc, httpx.ConnectError):
        low = detail.lower()
        if "name or service not known" in low or "nodename" in low or "getaddrinfo" in low:
            return "域名解析失败（DNS）：服务器无法解析该 base_url 域名，请检查拼写与服务器 DNS"
        if "refused" in low:
            return "连接被拒绝：目标端口未开放或服务未监听"
        if "certificate" in low or "ssl" in low:
            return f"TLS/证书错误：{detail[:150]}"
        return f"连接失败：{detail[:150]}"
    return f"请求失败（{exc.__class__.__name__}）：{detail[:150]}"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    context_length: int | None = None
    display_name: str | None = None


def _normalize_openai_base(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        raise ModelCatalogError("缺少 Base URL")
    if not base.endswith("/v1"):
        base = f"{base}/v1" if not base.rstrip("/").endswith(("v1", "v2")) else base
    return base


def _normalize_anthropic_base(base_url: str) -> str:
    base = (base_url or "https://api.anthropic.com").rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


async def list_models_openai(base_url: str, api_key: str) -> list[ModelInfo]:
    url = f"{_normalize_openai_base(base_url)}/models"
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": UA} if api_key else {"User-Agent": UA}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ModelCatalogError(f"{_describe_network_error(exc)}｜目标：{url}") from exc
    if resp.status_code != 200:
        raise ModelCatalogError(f"HTTP {resp.status_code}：{resp.text[:200]}｜目标：{url}")
    try:
        data = resp.json().get("data", [])
    except ValueError as exc:
        raise ModelCatalogError("响应不是合法 JSON") from exc
    return [ModelInfo(id=m.get("id", "")) for m in data if m.get("id")]


async def list_models_anthropic(base_url: str, api_key: str) -> list[ModelInfo]:
    url = f"{_normalize_anthropic_base(base_url)}/v1/models"
    headers = {"x-api-key": api_key or "", "anthropic-version": "2023-06-01", "User-Agent": UA}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ModelCatalogError(f"{_describe_network_error(exc)}｜目标：{url}") from exc
    if resp.status_code != 200:
        raise ModelCatalogError(f"HTTP {resp.status_code}：{resp.text[:200]}｜目标：{url}")
    data = resp.json().get("data", [])
    return [
        ModelInfo(id=m.get("id", ""), display_name=m.get("display_name"))
        for m in data if m.get("id")
    ]


async def list_models_gemini(api_key: str) -> list[ModelInfo]:
    if not api_key:
        raise ModelCatalogError("缺少 GOOGLE_API_KEY")
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, params={"key": api_key}, headers={"User-Agent": UA})
    except httpx.HTTPError as exc:
        raise ModelCatalogError(f"{_describe_network_error(exc)}｜目标：{url}") from exc
    if resp.status_code != 200:
        raise ModelCatalogError(f"HTTP {resp.status_code}：{resp.text[:200]}｜目标：{url}")
    out: list[ModelInfo] = []
    for m in resp.json().get("models", []):
        raw = m.get("name", "")
        mid = raw.removeprefix("models/") if raw else ""
        if mid:
            out.append(ModelInfo(id=mid, context_length=m.get("inputTokenLimit"),
                                 display_name=m.get("displayName")))
    return out


async def list_models(protocol: str, base_url: str, api_key: str) -> list[ModelInfo]:
    if protocol == "anthropic_messages":
        return await list_models_anthropic(base_url, api_key)
    if protocol == "google_gemini":
        return await list_models_gemini(api_key)
    if protocol == "aws_bedrock":
        raise ModelCatalogError("Bedrock 模型目录需通过 AWS CLI/控制台查询，请在下方手动录入模型")
    return await list_models_openai(base_url, api_key)
