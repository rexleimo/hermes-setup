"""审计日志查询页。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core import audit
from app.web.deps import User, csrf_guard
from app.web.templating import render

router = APIRouter(prefix="/audit", dependencies=[Depends(csrf_guard)])


@router.get("")
def audit_page(request: Request, user: User, p: int = 1, action: str = "",
               username: str = "", outcome: str = ""):
    data = audit.page(p=p, action=action.strip(), username=username.strip(),
                      outcome=outcome.strip())
    return render(request, "audit.html", {
        "nav_active": "platform",
        "tab": "audit",
        "data": data,
        "f_action": action.strip(),
        "f_username": username.strip(),
        "f_outcome": outcome.strip(),
    })
