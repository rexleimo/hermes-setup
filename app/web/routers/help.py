"""帮助与常见问题：应用内唯一的文档入口。

小白不会去翻仓库 README，所有「这是什么 / 坏了怎么办」的答案必须在页面里。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.web.deps import User, csrf_guard
from app.web.templating import render

router = APIRouter(dependencies=[Depends(csrf_guard)])


@router.get("/help")
def help_page(request: Request, user: User):
    return render(request, "help.html", {"nav_active": "help"})
