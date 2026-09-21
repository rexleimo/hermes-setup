"""运行体检：把「小白不知道哪里坏了」变成逐项打勾 + 每项给下一步动作。

设计原则（与 onboarding.py 同一哲学）：小白不读日志、不懂术语，
所以每项检查都用大白话说明「它影响什么」，失败项直接给可点击的去处。
网络探测较慢（每条最多 5 秒），单独走 HTMX 懒加载，不拖慢整页。
"""
from __future__ import annotations

import shutil
import sys
import urllib.request

from fastapi import APIRouter, Depends, Request

from app.hermes import supervisor
from app.hermes.paths import HermesPaths, detect
from app.web.deps import User, csrf_guard
from app.web.templating import render, render_partial

router = APIRouter(prefix="/diagnose", dependencies=[Depends(csrf_guard)])

# 网络探测目标：安装/更新与依赖下载链路；hint 说明不通时的症状与解法
# （Windows 下一键安装走 PowerShell 安装器，清单为 ps1 域名 + GitHub + astral + PyPI 四项）
_NET_PROBES_BASE = [
    ("GitHub（安装 / 更新 Hermes Agent）", "https://raw.githubusercontent.com",
     "不通 → 「服务管理」的一键安装与更新都会失败。开代理或换网络后重试。"),
    ("astral.sh（首次自动安装 uv）", "https://astral.sh",
     "不通只影响启动脚本自动装 uv；可改用国内镜像手动装（见帮助页）。"),
    ("PyPI（控制台自身依赖）", "https://pypi.org/simple/",
     "不通 → 启动脚本报「依赖安装出错」。可用清华镜像重试："
     "UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"),
]


def network_probes() -> list[tuple[str, str, str]]:
    """平台相关的探测清单：探测目标必须与实际安装源同域，
    否则出现「体检全绿、一键安装必败」。"""
    if sys.platform != "win32":
        return list(_NET_PROBES_BASE)
    return [
        ("Hermes 安装源（Windows PowerShell 安装器）",
         "https://hermes-agent.nousresearch.com/install.ps1",
         "不通 → 「服务管理」的一键安装无法下载安装器。开代理或换网络后重试。"),
        ("GitHub（更新 Hermes Agent）", "https://github.com",
         "不通 → hermes update 与安装器下载文件都会失败。开代理或换网络后重试。"),
        _NET_PROBES_BASE[1],
        _NET_PROBES_BASE[2],
    ]


def _probe(url: str, timeout: float = 5.0) -> bool:
    """GET 探测（部分站点拒绝 HEAD；不读响应体，只看状态码）。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-console"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def build_checks(paths: HermesPaths) -> list[dict]:
    """本机静态检查（毫秒级）：返回 {label, state, detail, href, action_label}。
    state：ok=正常 warn=待办/存疑 fail=已坏。"""
    checks: list[dict] = []

    def add(label: str, state: str, detail: str,
            href: str = "", action_label: str = "") -> None:
        checks.append({"label": label, "state": state, "detail": detail,
                       "href": href, "action_label": action_label})

    uv = shutil.which("uv")
    add("uv 已安装" if uv else "uv 未安装",
        "ok" if uv else "warn",
        "控制台后台任务（装依赖 / 装插件）依赖它" if uv
        else "微信依赖与插件安装会失败；退出控制台后重新运行 start.sh / start.bat 会自动安装")

    git = shutil.which("git")
    add("Git 已安装" if git else "Git 未安装",
        "ok" if git else "fail",
        "" if git else "一键安装 Hermes 需要 Git 下拉官方仓库；装好 Git（git-scm.com）后重试安装")

    add("Hermes Agent 已安装" if paths.installed else "Hermes Agent 未安装",
        "ok" if paths.installed else "warn",
        f"版本 {supervisor.version(paths)}" if paths.installed
        else "还没装 Agent 本体；这是第一步",
        "/service" if not paths.installed else "", "去安装" if not paths.installed else "")

    config_yaml = paths.config_yaml
    if not config_yaml.exists():
        add("config.yaml 尚未创建", "warn",
            "首次配置供应商或渠道时会自动生成", "", "")
    else:
        try:
            from app.hermes import config_store
            config_store.load_config()
            add("config.yaml 可以正常解析", "ok", "", "", "")
        except Exception as exc:
            add("config.yaml 解析失败", "fail",
                f"Gateway 会拒绝启动：{exc}。可在设置页查看最近的配置备份",
                "/settings", "去恢复")

    try:
        from app.hermes.providers_service import list_providers
        main_ready = any(p.is_main for p in list_providers())
    except Exception:
        main_ready = False
    add("主模型已设置" if main_ready else "主模型未设置",
        "ok" if main_ready else "warn",
        "Agent 用它思考" if main_ready else "没有主模型，Agent 无法回答",
        "/providers" if not main_ready else "", "去设置" if not main_ready else "")

    try:
        from app.hermes.channels_service import list_channels
        channels = list_channels()
        enabled = [c for c in channels if c.enabled]
        usable = [c for c in enabled if c.configured]
        if enabled and usable:
            add(f"消息渠道已启用（{len(usable)} 个可用）", "ok", "", "", "")
        elif enabled:
            add("有渠道已启用但凭证不全", "warn",
                "凭证没填全的渠道 Gateway 构建不出来", "/channels", "去补全")
        else:
            add("尚未启用任何消息渠道", "warn",
                "启用一个（推荐微信或飞书）才能在聊天里指挥 Agent",
                "/channels", "去启用")
    except Exception as exc:  # 渠道定义异常不该拖垮整个体检页
        add("消息渠道状态未知", "warn", f"读取渠道配置失败：{exc}", "", "")

    st = supervisor.status(paths, force=True)  # 体检页要真实时探测，不走缓存
    if st.running is True:
        add("Gateway 正在运行", "ok", "", "", "")
    elif st.running is None:
        add("Gateway 状态未知", "warn", st.detail or "三路探测都没有结果",
            "/service", "去查看")
    else:
        add("Gateway 未运行", "warn",
            "配好后在这里启动；它负责收发各渠道消息",
            "/service", "去启动")
    return checks


@router.get("")
def diagnose_page(request: Request, user: User):
    paths = detect()
    checks = build_checks(paths)
    fails = sum(1 for c in checks if c["state"] == "fail")
    warns = sum(1 for c in checks if c["state"] == "warn")
    return render(request, "diagnose.html", {
        "nav_active": "diagnose",
        "checks": checks,
        "fails": fails,
        "warns": warns,
        "paths": paths,
    })


@router.get("/network")
def network_fragment(request: Request, user: User):
    results = [{"label": label, "ok": _probe(url), "hint": hint}
               for label, url, hint in network_probes()]
    return render_partial(request, "diagnose/_net.html", {
        "results": results,
        "all_ok": all(r["ok"] for r in results),
    })
