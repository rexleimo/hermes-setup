"""HTML 预览的 CSP 策略（WI-19B）：公共 CDN 白名单 + 宽松档 CSP 构造。

名单放配置项 `preview_cdn_allowlist`（空格分隔），未配置用内置默认；
逐资源拦截——未命中名单的单个资源降级（那一块裸），整页不白屏。
"""
from __future__ import annotations

# 初版名单：Agent 生成页最常见的公共 CDN（Tailwind / ECharts 系多走 jsdelivr，
# 图表库多走 unpkg/cdnjs，字体走 Google Fonts）
DEFAULT_CDN_HOSTS = (
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.tailwindcss.com",
)


def cdn_hosts() -> tuple:
    """配置项优先（appsettings），读不到/未配置回内置默认。"""
    try:
        from app.core import appsettings
        raw = appsettings.get_setting("preview_cdn_allowlist")
    except Exception:
        return DEFAULT_CDN_HOSTS
    if not raw or not raw.strip():
        return DEFAULT_CDN_HOSTS
    return tuple(h for h in raw.split() if h)


def csp_for_raw_html() -> str:
    """宽松档 CSP：放行名单 CDN 的脚本/样式/图片/字体/媒体/请求。

    执行门仍在 iframe sandbox 手里（默认全禁）；叠加 `sandbox` 指令，
    使“新标签页打开”的顶层文档同样被沙盒（无身份、防带走工作区数据）。
    """
    cdn = " ".join(cdn_hosts())
    return (
        "default-src 'self'; "
        f"img-src 'self' data: blob: {cdn}; "
        f"style-src 'self' 'unsafe-inline' {cdn}; "
        f"script-src 'self' 'unsafe-inline' {cdn}; "
        f"font-src 'self' data: {cdn}; "
        f"connect-src 'self' {cdn}; "
        f"media-src 'self' data: blob: {cdn}; "
        "frame-ancestors 'self'; base-uri 'self'; object-src 'none'; "
        "sandbox allow-scripts allow-forms"
    )
