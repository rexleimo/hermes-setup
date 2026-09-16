"""Skill 路由：已安装列表（启停/删除）、官方技能库一键安装、skills 配置域。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit
from app.hermes import skills_service as svc
from app.hermes.skills_service import SkillError, SkillsSettings
from app.web.deps import Admin, User, client_ip, csrf_guard
from app.web.htmx import is_htmx, redirect, toast
from app.web.templating import render

router = APIRouter(prefix="/skills", dependencies=[Depends(csrf_guard)])


@router.get("")
def list_page(request: Request, user: User):
    return render(request, "skills/list.html", {
        "nav_active": "skills",
        "skills": svc.installed_skills(),
        "catalog": svc.catalog(),
        "catalog_available": svc.catalog_base().is_dir(),
        "settings": svc.get_settings(),
    })


# ---------------------------------------------------------------------------
# 启停 / 删除
# ---------------------------------------------------------------------------

@router.post("/{slug}/toggle")
def toggle(request: Request, user: Admin, slug: str, enable: str = Form(...)):
    try:
        svc.toggle_skill(slug, enable == "1")
    except SkillError as exc:
        return _reject(request, str(exc))
    audit.record("skill_toggle", username=user["username"], target=slug,
                 detail={"enabled": enable == "1"}, ip=client_ip(request))
    resp = redirect(request, "/skills")
    toast(resp, f"技能 {slug} 已{'启用' if enable == '1' else '禁用'}")
    return resp


@router.post("/{slug}/delete")
def delete(request: Request, user: Admin, slug: str, confirm: str = Form("")):
    if confirm.strip() != slug:
        audit.record("skill_delete", username=user["username"], target=slug,
                     outcome="denied", detail="确认词不匹配", ip=client_ip(request))
        return _reject(request, "请输入技能目录名以确认删除")
    try:
        svc.remove_skill(slug)
    except SkillError as exc:
        return _reject(request, str(exc))
    audit.record("skill_delete", username=user["username"], target=slug,
                 ip=client_ip(request))
    resp = redirect(request, "/skills")
    toast(resp, f"技能 {slug} 已删除", level="warning")
    return resp


# ---------------------------------------------------------------------------
# 官方技能库
# ---------------------------------------------------------------------------

@router.post("/catalog/install")
def install(request: Request, user: Admin, rel: str = Form(...)):
    try:
        slug = svc.install_from_catalog(rel.strip())
    except SkillError as exc:
        return _reject(request, str(exc))
    audit.record("skill_install", username=user["username"], target=slug,
                 detail={"source": rel}, ip=client_ip(request))
    resp = redirect(request, "/skills")
    toast(resp, f"技能 {slug} 已安装到 ~/.hermes/skills/")
    return resp


# ---------------------------------------------------------------------------
# skills 配置域
# ---------------------------------------------------------------------------

@router.post("/settings")
def save(request: Request, user: Admin,
         external_dirs_text: str = Form(""),
         project_discovery: str = Form("0"),
         template_vars: str = Form("0"),
         inline_shell: str = Form("0"),
         inline_shell_timeout: str = Form("10"),
         guard_agent_created: str = Form("0")):
    s = SkillsSettings(
        external_dirs=[line.strip() for line in external_dirs_text.splitlines()
                       if line.strip()],
        project_discovery=project_discovery == "1",
        template_vars=template_vars == "1",
        inline_shell=inline_shell == "1",
        inline_shell_timeout=int(inline_shell_timeout)
        if inline_shell_timeout.strip().isdigit() else 10,
        guard_agent_created=guard_agent_created == "1",
    )
    try:
        svc.save_settings(s)
    except SkillError as exc:
        return _reject(request, str(exc))
    audit.record("skill_settings", username=user["username"],
                 detail={"external_dirs": len(s.external_dirs),
                         "inline_shell": s.inline_shell},
                 ip=client_ip(request))
    resp = redirect(request, "/skills")
    toast(resp, "技能设置已写入 config.yaml（skills 域）")
    return resp


@router.post("/untrust")
def untrust(request: Request, user: Admin, path: str = Form(...)):
    try:
        svc.untrust_project_dir(path.strip())
    except SkillError as exc:
        return _reject(request, str(exc))
    audit.record("skill_untrust", username=user["username"], target=path.strip(),
                 ip=client_ip(request))
    resp = redirect(request, "/skills")
    toast(resp, f"已取消信任：{path.strip()}")
    return resp


# ---------------------------------------------------------------------------

def _reject(request: Request, message: str, code: int = 400):
    if is_htmx(request):
        from fastapi.responses import HTMLResponse

        return HTMLResponse(f"<div class='alert alert-danger'>{message}</div>", status_code=code)
    return render(request, "error.html",
                  {"nav_active": "skills", "code": code, "message": message},
                  status_code=code)
