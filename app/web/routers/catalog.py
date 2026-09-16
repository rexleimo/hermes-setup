"""配置项全景页（只读参考）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.web.catalog_data import AREAS, INTEGRATION_LABELS
from app.web.deps import User, csrf_guard
from app.web.templating import render

router = APIRouter(prefix="/catalog", dependencies=[Depends(csrf_guard)])


@router.get("")
def catalog_page(request: Request, user: User):
    counts: dict[str, int] = {}
    for area in AREAS:
        counts[area.integration] = counts.get(area.integration, 0) + 1
    return render(request, "catalog.html", {
        "nav_active": "catalog",
        "areas": AREAS,
        "labels": INTEGRATION_LABELS,
        "counts": counts,
    })
