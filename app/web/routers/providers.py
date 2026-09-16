"""模型供应商路由：CRUD、API Key、主模型、模型目录、别名。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit
from app.hermes import providers_service as svc
from app.hermes.providers_service import ProviderError
from app.hermes.schema import PRESETS, PROTOCOLS
from app.web.deps import Admin, User, client_ip, csrf_guard
from app.web.htmx import is_htmx, redirect, toast
from app.web.templating import render, render_partial

router = APIRouter(prefix="/providers", dependencies=[Depends(csrf_guard)])


@router.get("")
def list_page(request: Request, user: User):
    providers = svc.list_providers()
    fallback = {e.provider for e in svc.get_fallback()}
    return render(request, "providers/list.html", {
        "nav_active": "providers",
        "providers": providers,
        "fallback_providers": fallback,
    })


# ---------------------------------------------------------------------------
# 新建 / 编辑
# ---------------------------------------------------------------------------

@router.get("/new")
def new_page(request: Request, user: Admin, kind: str = "custom", preset: str = ""):
    return render(request, "providers/new.html", {
        "nav_active": "providers",
        "presets": PRESETS.values(),
        "protocols": PROTOCOLS,
        "kind": kind if kind in ("custom", "preset") else "custom",
        "preset_id": preset,
        "form": {},
    })


@router.post("/new")
async def create(request: Request, user: Admin,
                 kind: str = Form("custom"),
                 pid: str = Form(""),
                 name: str = Form(""),
                 preset_id: str = Form(""),
                 protocol: str = Form("openai_chat"),
                 base_url: str = Form(""),
                 env_key: str = Form(""),
                 api_key: str = Form(""),
                 default_model: str = Form(""),
                 note: str = Form("")):
    try:
        svc.create_provider(
            pid=pid, name=name, kind=kind, preset_id=preset_id or None,
            protocol=protocol, base_url=base_url, env_key=env_key,
            api_key=api_key, default_model=default_model, note=note,
        )
    except ProviderError as exc:
        if is_htmx(request):
            return render_partial(request, "providers/_form_error.html",
                                  {"error": str(exc)}, status_code=400)
        return render(request, "providers/new.html", {
            "nav_active": "providers", "presets": PRESETS.values(),
            "protocols": PROTOCOLS, "kind": kind, "preset_id": preset_id,
            "error": str(exc),
            "form": {"pid": pid, "name": name, "base_url": base_url,
                     "env_key": env_key, "default_model": default_model, "note": note},
        }, status_code=400)

    audit.record("provider_create", username=user["username"], target=pid,
                 detail={"kind": kind, "preset": preset_id, "protocol": protocol},
                 ip=client_ip(request))
    resp = redirect(request, f"/providers/{pid}")
    toast(resp, f"供应商 {name or pid} 已添加")
    return resp


@router.get("/{pid}")
def detail(request: Request, user: User, pid: str):
    try:
        view = svc.get_provider(pid)
    except ProviderError:
        return _not_found(request)
    proto = PROTOCOLS[view.protocol]
    models = svc.list_models(pid)
    aliases = {a: e for a, e in svc.list_aliases().items()
               if isinstance(e, dict) and e.get("provider") == _hermes_pid(view)}
    return render(request, "providers/detail.html", {
        "nav_active": "providers",
        "p": view, "proto": proto,
        "models": models,
        "aliases": aliases,
        "is_main": view.is_main,
    })


def _hermes_pid(view: svc.ProviderView) -> str:
    return view.preset_id if view.kind == "preset" else view.id


@router.post("/{pid}/update")
def update(request: Request, user: Admin, pid: str,
           name: str = Form(...), base_url: str = Form(""),
           env_key: str = Form(""), default_model: str = Form(""), note: str = Form("")):
    try:
        svc.update_provider(pid, name=name, base_url=base_url, env_key=env_key,
                            default_model=default_model, note=note)
    except ProviderError as exc:
        return _reject(request, str(exc))
    audit.record("provider_update", username=user["username"], target=pid,
                 ip=client_ip(request))
    resp = redirect(request, f"/providers/{pid}")
    toast(resp, "供应商信息已更新")
    return resp


@router.post("/{pid}/apikey")
def apikey(request: Request, user: Admin, pid: str, api_key: str = Form(...)):
    try:
        svc.set_api_key(pid, api_key.strip())
    except ProviderError as exc:
        return _reject(request, str(exc))
    audit.record("provider_apikey", username=user["username"], target=pid,
                 ip=client_ip(request))
    resp = redirect(request, f"/providers/{pid}")
    toast(resp, "API Key 已写入 ~/.hermes/.env")
    return resp


@router.post("/{pid}/delete")
def delete(request: Request, user: Admin, pid: str, confirm: str = Form("")):
    if confirm.strip() != pid:
        audit.record("provider_delete", username=user["username"], target=pid,
                     outcome="denied", detail="确认词不匹配", ip=client_ip(request))
        return _reject(request, "请输入供应商标识以确认删除")
    try:
        notes = svc.delete_provider(pid)
    except ProviderError as exc:
        return _reject(request, str(exc))
    audit.record("provider_delete", username=user["username"], target=pid,
                 detail=notes, ip=client_ip(request))
    resp = redirect(request, "/providers")
    toast(resp, f"供应商 {pid} 已删除" + ("；" + "；".join(notes) if notes else ""),
          level="warning" if notes else "success")
    return resp


# ---------------------------------------------------------------------------
# 主模型
# ---------------------------------------------------------------------------

@router.post("/{pid}/set-main")
def set_main(request: Request, user: Admin, pid: str,
             model_id: str = Form(...), context_length: str = Form("")):
    ctx_len = None
    if context_length.strip().isdigit():
        ctx_len = int(context_length.strip())
    try:
        svc.set_main_model(pid, model_id, ctx_len)
    except ProviderError as exc:
        return _reject(request, str(exc))
    audit.record("provider_set_main", username=user["username"], target=pid,
                 detail={"model": model_id, "context_length": ctx_len},
                 ip=client_ip(request))
    resp = redirect(request, "/chains")
    toast(resp, f"主模型已切换为 {model_id}（写入 model.* 后 Gateway 会热加载）")
    return resp


# ---------------------------------------------------------------------------
# 模型目录
# ---------------------------------------------------------------------------

@router.post("/{pid}/fetch")
async def fetch_models(request: Request, user: Admin, pid: str):
    try:
        remote = await svc.fetch_remote_models(pid)
    except Exception as exc:  # ModelCatalogError / ProviderError
        if is_htmx(request):
            return render_partial(request, "providers/_fetch_result.html",
                                  {"error": str(exc), "remote": [], "pid": pid},
                                  status_code=502)
        return _reject(request, str(exc), code=502)
    audit.record("provider_fetch_models", username=user["username"], target=pid,
                 detail=f"拉取到 {len(remote)} 个模型", ip=client_ip(request))
    existing = {m["model_id"] for m in svc.list_models(pid)}
    return render_partial(request, "providers/_fetch_result.html", {
        "remote": remote, "pid": pid, "error": "", "existing_ids": existing,
    })


@router.post("/{pid}/models")
async def save_models(request: Request, user: Admin, pid: str):
    """批量保存拉取到的模型：勾选 = 保存，未勾选 = 从目录移除。"""
    form = await request.form()
    tokens = [key[4:] for key in form if key.startswith("mid_")]
    existing = {m["model_id"] for m in svc.list_models(pid)}
    saved_ids: set[str] = set()
    for token in tokens:
        real_id = str(form.get(f"mid_{token}") or "").strip()
        if not real_id:
            continue
        if form.get(f"sel_{token}") == "on":
            ctx_raw = str(form.get(f"ctx_{token}") or "").strip()
            svc.upsert_model(
                pid, real_id,
                display_name=str(form.get(f"disp_{token}") or "").strip() or real_id,
                context_length=int(ctx_raw) if ctx_raw.isdigit() else None,
            )
            saved_ids.add(real_id)
        elif real_id in existing:
            svc.remove_model(pid, real_id)
    audit.record("provider_models_save", username=user["username"], target=pid,
                 detail=f"保存 {len(saved_ids)} 个模型", ip=client_ip(request))
    resp = redirect(request, f"/providers/{pid}")
    toast(resp, f"已保存 {len(saved_ids)} 个模型配置")
    return resp


@router.post("/{pid}/models/manual")
def add_model_manual(request: Request, user: Admin, pid: str,
                     model_id: str = Form(...), display_name: str = Form(""),
                     context_length: str = Form("")):
    ctx_len = int(context_length.strip()) if context_length.strip().isdigit() else None
    svc.upsert_model(pid, model_id.strip(), display_name=display_name.strip() or model_id.strip(),
                     context_length=ctx_len)
    audit.record("provider_model_add", username=user["username"], target=f"{pid}/{model_id}",
                 ip=client_ip(request))
    resp = redirect(request, f"/providers/{pid}")
    toast(resp, f"已登记模型 {model_id.strip()}")
    return resp


@router.post("/{pid}/models/{model_id}/delete")
def delete_model(request: Request, user: Admin, pid: str, model_id: str):
    svc.remove_model(pid, model_id)
    audit.record("provider_model_delete", username=user["username"], target=f"{pid}/{model_id}",
                 ip=client_ip(request))
    resp = redirect(request, f"/providers/{pid}")
    toast(resp, f"已移除模型 {model_id}")
    return resp


# ---------------------------------------------------------------------------
# 别名 / 连接测试
# ---------------------------------------------------------------------------

@router.post("/{pid}/alias")
def publish_alias(request: Request, user: Admin, pid: str,
                  model_id: str = Form(...), alias: str = Form(...)):
    try:
        svc.publish_alias(pid, model_id, alias)
    except ProviderError as exc:
        return _reject(request, str(exc))
    audit.record("alias_publish", username=user["username"], target=alias,
                 detail=f"{pid} / {model_id}", ip=client_ip(request))
    resp = redirect(request, f"/providers/{pid}")
    toast(resp, f"别名 {alias} → {model_id} 已写入 model_aliases")
    return resp


@router.post("/{pid}/test")
async def test_connection(request: Request, user: Admin, pid: str):
    try:
        remote = await svc.fetch_remote_models(pid)
        ok, message = True, f"连接正常，拉取到 {len(remote)} 个模型"
        sample = [m.id for m in remote[:5]]
    except Exception as exc:
        ok, message, sample = False, str(exc), []
    audit.record("provider_test", username=user["username"], target=pid,
                 outcome="ok" if ok else "failed", detail=message[:300],
                 ip=client_ip(request))
    return render_partial(request, "providers/_test_result.html", {
        "ok": ok, "message": message, "sample": sample, "pid": pid,
    }, status_code=200 if ok else 502)


# ---------------------------------------------------------------------------

def _not_found(request: Request):
    if is_htmx(request):
        from fastapi.responses import HTMLResponse

        return HTMLResponse("<div class='alert alert-danger'>供应商不存在</div>", status_code=404)
    return render(request, "error.html",
                  {"nav_active": "providers", "code": 404, "message": "供应商不存在"},
                  status_code=404)


def _reject(request: Request, message: str, code: int = 400):
    if is_htmx(request):
        from fastapi.responses import HTMLResponse

        return HTMLResponse(f"<div class='alert alert-danger'>{message}</div>", status_code=code)
    return render(request, "error.html",
                  {"nav_active": "providers", "code": code, "message": message},
                  status_code=code)
