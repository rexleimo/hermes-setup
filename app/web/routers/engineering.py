"""工程规范层路由：规范注入、技能安装、工作区初始化与卸载。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request

from app.core import audit
from app.hermes import engineering_service as eng
from app.hermes.paths import detect
from app.web.deps import Admin, User, client_ip
from app.web.htmx import redirect, toast
from app.web.templating import render

router = APIRouter(prefix="/engineering")


@router.get("")
def engineering_page(request: Request, user: User):
    s = eng.load_settings()
    paths = detect()
    return render(request, "engineering.html", {
        "nav_active": "engineering",
        "s": s,
        "status": eng.get_status(paths),
        "block_preview": eng.compose_block(s),
        "skills": eng.SKILLS,
        "requirement_template": eng.DEFAULT_REQUIREMENT_TEMPLATE,
        "paths": paths,
    })


@router.post("/apply")
def apply(request: Request, admin: Admin,
          workspace: str = Form(...), rules: str = Form(""),
          inject_coding_instructions: str = Form("off"),
          skills: list[str] = Form([])):
    s = eng.EngSettings(
        workspace=workspace.strip() or eng.DEFAULT_WORKSPACE,
        rules=rules.strip() or eng.DEFAULT_RULES,
        inject_coding_instructions=inject_coding_instructions == "on",
        skills_enabled=tuple(skills) if skills else (),
    )
    changes = eng.apply(s, detect())
    audit.record("engineering_apply", username=admin["username"], detail=changes,
                 ip=client_ip(request))
    resp = redirect(request, "/engineering")
    toast(resp, "工程规范已应用：" + "；".join(changes[:2]) + ("…" if len(changes) > 2 else ""))
    return resp


@router.post("/workspace-init")
def workspace_init(request: Request, user: User, workspace: str = Form("")):
    s = eng.load_settings()
    if workspace.strip():
        s.workspace = workspace.strip()
    created = eng.init_workspace(s, detect())
    audit.record("engineering_workspace_init", username=user["username"],
                 target=s.workspace, detail=created, ip=client_ip(request))
    resp = redirect(request, "/engineering")
    toast(resp, f"工作区已初始化（{len(created)} 项新建）" if created else "工作区已存在，未做改动")
    return resp


@router.post("/remove")
def remove(request: Request, admin: Admin, keep_skills: str = Form("off")):
    changes = eng.remove(keep_skills=keep_skills == "on", paths=detect())
    audit.record("engineering_remove", username=admin["username"], detail=changes,
                 ip=client_ip(request))
    resp = redirect(request, "/engineering")
    toast(resp, "工程规范已卸载：" + ("；".join(changes) if changes else "本来就没有注入"))
    return resp
