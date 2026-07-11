"""Web UI routes - renders Jinja2 templates with auth."""
from __future__ import annotations

import os
import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..db import database as db
from ..auth import hash_password, check_password

log = logging.getLogger("librestreamer.ui")
router = APIRouter()

_here = os.path.dirname(os.path.abspath(__file__))
_templates_dir = os.path.join(os.path.dirname(_here), "templates")
templates = Jinja2Templates(directory=_templates_dir)


def get_router(state) -> APIRouter:

    # ---- login/logout ----

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, error: str = ""):
        return templates.TemplateResponse(request, "login.html", {
            "request": request, "error": error, "user": None,
        })

    @router.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
        row = db.get_user_by_name(state.conn, username)
        if row and check_password(password, row["password_hash"]):
            resp = RedirectResponse(url="/", status_code=302)
            resp.set_cookie("ls_session", state.sign_session(row["id"]), httponly=True)
            return resp
        return templates.TemplateResponse(request, "login.html", {
            "request": request, "error": "Invalid username or password", "user": None,
        }, status_code=401)

    @router.get("/logout")
    async def logout():
        resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie("ls_session")
        return resp

    # ---- public pages ----

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        user = request.state.user
        rows = db.get_media_items(state.conn)
        return templates.TemplateResponse(request, "index.html", {
            "request": request, "user": user,
            "backend_count": len(state.clients),
        })

    @router.get("/library", response_class=HTMLResponse)
    async def library_page(request: Request, type: str = ""):
        return templates.TemplateResponse(request, "library.html", {
            "request": request, "item_type": type,
            "user": request.state.user,
        })

    @router.get("/item/{item_id}", response_class=HTMLResponse)
    async def item_page(request: Request, item_id: str):
        row = db.get_media_item(state.conn, item_id)
        if not row:
            raise HTTPException(404)
        children = db.get_children(state.conn, item_id)
        children_json = [dict(c) for c in children]
        return templates.TemplateResponse(request, "item.html", {
            "request": request, "item": row, "children": children,
            "children_json": children_json, "user": request.state.user,
        })

    @router.get("/watch/{item_id}", response_class=HTMLResponse)
    async def watch_page(request: Request, item_id: str):
        row = db.get_media_item(state.conn, item_id)
        if not row:
            raise HTTPException(404)
        return templates.TemplateResponse(request, "player.html", {
            "request": request, "item": row, "user": request.state.user,
        })

    @router.get("/search", response_class=HTMLResponse)
    async def search_page(request: Request, q: str = ""):
        rows = db.search_media(state.conn, q) if q else []
        return templates.TemplateResponse(request, "search.html", {
            "request": request, "query": q, "results": rows,
            "user": request.state.user,
        })

    # ---- admin pages ----

    @router.get("/admin", response_class=HTMLResponse)
    async def admin_dashboard(request: Request):
        db_backends = db.list_backends(state.conn)
        balancer_states = {s["name"]: s for s in state.balancer.states()}
        backends = []
        for row in db_backends:
            b = dict(row)
            s = balancer_states.get(b["name"], {})
            b["healthy"] = s.get("healthy", False)
            b["score"] = s.get("score", 0)
            b["cpu_usage_pct"] = s.get("cpu_usage_pct", -1)
            b["memory_usage_pct"] = s.get("memory_usage_pct", -1)
            b["gpu_usage_pct"] = s.get("gpu_usage_pct", -1)
            b["gpu_name"] = s.get("gpu_name", "")
            b["memory_total_bytes"] = s.get("memory_total_bytes", 0)
            b["memory_used_bytes"] = s.get("memory_used_bytes", 0)
            b["disk_total_bytes"] = s.get("disk_total_bytes", 0)
            b["disk_used_bytes"] = s.get("disk_used_bytes", 0)
            b["disk_usage_pct"] = s.get("disk_usage_pct", -1)
            b["active_streams"] = s.get("active_streams", 0)
            b["last_seen_ago"] = s.get("last_seen_ago", -1)
            backends.append(b)
        type_counts = {r["type"]: r["count"] for r in db.count_media_types(state.conn)}
        total_items = sum(type_counts.values())
        active_sessions = len(db.get_active_sessions(state.conn))
        healthy_count = sum(1 for b in backends if b["healthy"])
        return templates.TemplateResponse(request, "admin/dashboard.html", {
            "request": request, "user": request.state.user,
            "backends": backends, "item_count": total_items,
            "type_counts": type_counts, "active_sessions": active_sessions,
            "healthy_count": healthy_count, "total_backends": len(backends),
            "auto_load_balance": state.balancer.auto_load_balance,
        })

    @router.get("/admin/servers", response_class=HTMLResponse)
    async def admin_servers(request: Request):
        db_backends = db.list_backends(state.conn)
        balancer_states = {s["name"]: s for s in state.balancer.states()}
        backends = []
        for row in db_backends:
            b = dict(row)
            s = balancer_states.get(b["name"], {})
            b["healthy"] = s.get("healthy", False)
            b["score"] = s.get("score", 0)
            b["cpu_usage_pct"] = s.get("cpu_usage_pct", -1)
            b["memory_usage_pct"] = s.get("memory_usage_pct", -1)
            b["gpu_usage_pct"] = s.get("gpu_usage_pct", -1)
            b["gpu_name"] = s.get("gpu_name", "")
            b["memory_total_bytes"] = s.get("memory_total_bytes", 0)
            b["memory_used_bytes"] = s.get("memory_used_bytes", 0)
            b["disk_total_bytes"] = s.get("disk_total_bytes", 0)
            b["disk_used_bytes"] = s.get("disk_used_bytes", 0)
            b["disk_usage_pct"] = s.get("disk_usage_pct", -1)
            b["active_streams"] = s.get("active_streams", 0)
            b["last_seen_ago"] = s.get("last_seen_ago", -1)
            backends.append(b)
        backend_counts = {r["backend"]: r["count"] for r in db.count_media_by_backend(state.conn)}
        return templates.TemplateResponse(request, "admin/servers.html", {
            "request": request, "user": request.state.user,
            "backends": backends, "backend_counts": backend_counts,
        })

    @router.get("/admin/upload", response_class=HTMLResponse)
    async def admin_upload(request: Request):
        backends = [b for b in state.balancer.states() if b.get("kind") == "librestreamer"]
        return templates.TemplateResponse(request, "admin/upload.html", {
            "request": request, "user": request.state.user,
            "backends": backends,
        })

    @router.get("/admin/libraries", response_class=HTMLResponse)
    async def admin_libraries(request: Request):
        rows = db.get_media_items(state.conn)
        by_type = {}
        for r in rows:
            by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        backend_counts = {r["backend"]: r["count"] for r in db.count_media_by_backend(state.conn)}
        return templates.TemplateResponse(request, "admin/libraries.html", {
            "request": request, "user": request.state.user,
            "counts": by_type, "total": len(rows),
            "backend_counts": backend_counts,
        })

    @router.get("/admin/users", response_class=HTMLResponse)
    async def admin_users(request: Request):
        users = db.list_users(state.conn)
        return templates.TemplateResponse(request, "admin/users.html", {
            "request": request, "user": request.state.user,
            "users": users,
        })

    @router.post("/admin/users/add")
    async def admin_users_add(username: str = Form(...), password: str = Form(...),
                              is_admin: str = Form("")):
        db.add_user(state.conn, username, hash_password(password), is_admin == "on")
        return RedirectResponse(url="/admin/users", status_code=302)

    @router.post("/admin/users/{username}/delete")
    async def admin_users_delete(username: str):
        db.remove_user(state.conn, username)
        return RedirectResponse(url="/admin/users", status_code=302)

    @router.post("/admin/users/{username}/password")
    async def admin_users_password(username: str, password: str = Form(...)):
        db.change_password(state.conn, username, hash_password(password))
        return RedirectResponse(url="/admin/users", status_code=302)

    @router.get("/admin/logs", response_class=HTMLResponse)
    async def admin_logs(request: Request):
        log_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_here))), "frontend.log")
        lines = []
        if os.path.exists(log_file):
            with open(log_file, "r", errors="replace") as f:
                lines = f.readlines()[-200:]
        return templates.TemplateResponse(request, "admin/logs.html", {
            "request": request, "user": request.state.user, "lines": lines,
        })

    return router
