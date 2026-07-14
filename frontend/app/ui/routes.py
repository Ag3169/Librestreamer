"""Web UI routes - renders Jinja2 templates with auth + i18n."""
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
from ..i18n import load_translations, detect_language, translate, available_languages, SUPPORTED

load_translations()

log = logging.getLogger("librestreamer.ui")
router = APIRouter()

_here = os.path.dirname(os.path.abspath(__file__))
_templates_dir = os.path.join(os.path.dirname(_here), "templates")
templates = Jinja2Templates(directory=_templates_dir)


def _ctx(request: Request, state, **extra) -> dict:
    """Build template context with i18n + common vars."""
    cookie_lang = request.cookies.get("ls_lang")
    user = getattr(request.state, "user", None)
    user_pref = None
    if user:
        try:
            prefs = json.loads(user["preferences"]) if user["preferences"] else {}
            user_pref = prefs.get("lang")
        except Exception:
            pass
    accept_lang = request.headers.get("accept-language", "")
    lang = detect_language(cookie_lang, user_pref, accept_lang)
    languages = available_languages()

    def t(key: str, **kw) -> str:
        return translate(lang, key, **kw)

    ctx = {
        "request": request,
        "user": user,
        "t": t,
        "lang": lang,
        "languages": languages,
    }
    ctx.update(extra)
    return ctx


def get_router(state) -> APIRouter:

    # ---- login/logout ----

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, error: str = ""):
        return templates.TemplateResponse(request, "login.html",
            _ctx(request, state, error=error))

    @router.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
        row = db.get_user_by_name(state.conn, username)
        if row and check_password(password, row["password_hash"]):
            resp = RedirectResponse(url="/", status_code=302)
            resp.set_cookie("ls_session", state.sign_session(row["id"]), httponly=True)
            return resp
        return templates.TemplateResponse(request, "login.html",
            _ctx(request, state, error="Invalid username or password"), status_code=401)

    @router.get("/logout")
    async def logout():
        resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie("ls_session")
        return resp

    @router.post("/api/language")
    async def set_language(request: Request, payload: dict = None):
        lang = (payload or {}).get("lang", "en")
        resp = JSONResponse({"status": "ok", "lang": lang})
        resp.set_cookie("ls_lang", lang, httponly=False, max_age=365 * 86400)
        user = getattr(request.state, "user", None)
        if user:
            try:
                prefs = json.loads(user["preferences"]) if user["preferences"] else {}
            except Exception:
                prefs = {}
            prefs["lang"] = lang
            db.update_user_preferences(state.conn, user["id"], prefs)
        return resp

    # ---- public pages ----

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        user_id = getattr(request.state, "user_id", 0) or 0
        history = []
        if user_id:
            rows = db.get_watch_history(state.conn, user_id, limit=12)
            for r in rows:
                item = db.get_media_item(state.conn, r["item_id"])
                if item:
                    d = {"id": r["item_id"], "title": r["item_title"],
                         "type": r["item_type"], "position": r["position"],
                         "duration": r["duration"],
                         "has_thumbnail": bool(item["has_thumbnail"])}
                    history.append(d)
        recent = [dict(r) for r in db.get_recent_items(state.conn, limit=12)]
        return templates.TemplateResponse(request, "index.html",
            _ctx(request, state, backend_count=len(state.clients), history=history, recent=recent))

    @router.get("/library", response_class=HTMLResponse)
    async def library_page(request: Request, type: str = "", sort: str = "title", page: int = 1):
        rows, total = db.get_media_paginated(state.conn, type or None, sort, page, 48)
        total_pages = max(1, (total + 47) // 48)
        items = [dict(r) for r in rows]
        user = getattr(request.state, "user", None)
        is_admin = bool(user and user["is_admin"])
        return templates.TemplateResponse(request, "library.html",
            _ctx(request, state, item_type=type, items=items, total=total,
                 sort=sort, page=page, total_pages=total_pages, is_admin=is_admin))

    @router.get("/item/{item_id}", response_class=HTMLResponse)
    async def item_page(request: Request, item_id: str):
        row = db.get_media_item(state.conn, item_id)
        if not row:
            raise HTTPException(404)
        children = db.get_children(state.conn, item_id)
        children_json = [dict(c) for c in children]
        sources = {}
        try:
            import json as _json
            sources = _json.loads(row["sources"]) if row["sources"] else {}
        except Exception:
            pass
        item = dict(row)
        item["sources"] = sources
        similar = [dict(s) for s in db.get_similar_items(state.conn, item_id)]
        return templates.TemplateResponse(request, "item.html",
            _ctx(request, state, item=item, children=children, children_json=children_json, similar=similar))

    @router.get("/watch/{item_id}", response_class=HTMLResponse)
    async def watch_page(request: Request, item_id: str):
        row = db.get_media_item(state.conn, item_id)
        if not row:
            raise HTTPException(404)
        return templates.TemplateResponse(request, "player.html",
            _ctx(request, state, item=row))

    @router.get("/search", response_class=HTMLResponse)
    async def search_page(request: Request, q: str = ""):
        rows = db.search_media(state.conn, q) if q else []
        return templates.TemplateResponse(request, "search.html",
            _ctx(request, state, query=q, results=rows))

    @router.get("/favorites", response_class=HTMLResponse)
    async def favorites_page(request: Request):
        user_id = getattr(request.state, "user_id", 0) or 0
        rows = db.get_favorites(state.conn, user_id) if user_id else []
        return templates.TemplateResponse(request, "favorites.html",
            _ctx(request, state, items=rows))

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
        return templates.TemplateResponse(request, "admin/dashboard.html",
            _ctx(request, state, backends=backends, item_count=total_items,
                 type_counts=type_counts, active_sessions=active_sessions,
                 healthy_count=healthy_count, total_backends=len(backends),
                 auto_load_balance=state.balancer.auto_load_balance))

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
        return templates.TemplateResponse(request, "admin/servers.html",
            _ctx(request, state, backends=backends, backend_counts=backend_counts))

    @router.get("/admin/upload", response_class=HTMLResponse)
    async def admin_upload(request: Request):
        backends = [b for b in state.balancer.states() if b.get("kind") == "librestreamer"]
        return templates.TemplateResponse(request, "admin/upload.html",
            _ctx(request, state, backends=backends))

    @router.get("/admin/libraries", response_class=HTMLResponse)
    async def admin_libraries(request: Request):
        rows = db.get_media_items(state.conn)
        by_type = {}
        for r in rows:
            by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        backend_counts = {r["backend"]: r["count"] for r in db.count_media_by_backend(state.conn)}
        return templates.TemplateResponse(request, "admin/libraries.html",
            _ctx(request, state, counts=by_type, total=len(rows), backend_counts=backend_counts))

    @router.get("/admin/users", response_class=HTMLResponse)
    async def admin_users(request: Request):
        users = db.list_users(state.conn)
        return templates.TemplateResponse(request, "admin/users.html",
            _ctx(request, state, users=users))

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
        return templates.TemplateResponse(request, "admin/logs.html",
            _ctx(request, state, lines=lines))

    # ---- user preferences ----

    @router.get("/preferences", response_class=HTMLResponse)
    async def preferences_page(request: Request):
        user = getattr(request.state, "user", None)
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        return templates.TemplateResponse(request, "preferences.html",
            _ctx(request, state))

    @router.post("/preferences/password")
    async def preferences_change_password(request: Request, current_password: str = Form(...),
                                           new_password: str = Form(...)):
        user = getattr(request.state, "user", None)
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        if not check_password(current_password, user["password_hash"]):
            return templates.TemplateResponse(request, "preferences.html",
                _ctx(request, state, error="Invalid current password"), status_code=400)
        if len(new_password) < 3:
            return templates.TemplateResponse(request, "preferences.html",
                _ctx(request, state, error="Password too short (min 3 chars)"), status_code=400)
        db.change_password(state.conn, user["username"], hash_password(new_password))
        return RedirectResponse(url="/preferences?ok=1", status_code=302)

    # ---- first-run setup wizard ----

    @router.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request):
        if db.count_users(state.conn) > 0:
            return RedirectResponse(url="/", status_code=302)
        return templates.TemplateResponse(request, "setup.html",
            _ctx(request, state))

    @router.post("/api/setup/admin")
    async def setup_create_admin(request: Request, payload: dict = None):
        if db.count_users(state.conn) > 0:
            return JSONResponse({"error": "setup_already_done"}, status_code=400)
        payload = payload or {}
        username = (payload.get("username") or "").strip()
        password = payload.get("password") or ""
        if not username or len(username) < 2:
            return JSONResponse({"error": "username_too_short"}, status_code=400)
        if len(password) < 4:
            return JSONResponse({"error": "password_too_short"}, status_code=400)
        db.add_user(state.conn, username, hash_password(password), is_admin=True)
        return JSONResponse({"status": "ok"})

    @router.post("/api/setup/backend")
    async def setup_add_backend(request: Request, payload: dict = None):
        if db.count_users(state.conn) == 0:
            return JSONResponse({"error": "create_admin_first"}, status_code=400)
        payload = payload or {}
        btype = payload.get("type", "librestreamer")
        name = (payload.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "name_required"}, status_code=400)
        b = {"name": name, "type": btype, "enabled": True,
             "priority": int(payload.get("priority", 1)),
             "max_streams": int(payload.get("max_streams", 4))}
        if btype == "librestreamer":
            b["host"] = payload.get("host", "127.0.0.1")
            b["port"] = int(payload.get("port", 8080))
            b["secret"] = payload.get("secret", "")
            if not b["secret"]:
                return JSONResponse({"error": "secret_required"}, status_code=400)
        elif btype == "jellyfin":
            b["host"] = payload.get("host", "")
            b["port"] = int(payload.get("port", 8096))
            b["api_key"] = payload.get("api_key", "")
            b["ssl"] = bool(payload.get("ssl", False))
            b["user_id"] = payload.get("user_id", "")
            if not b["api_key"]:
                return JSONResponse({"error": "api_key_required"}, status_code=400)
        db.upsert_backend(state.conn, b)
        state.reload_clients()
        state.refresh_libraries()
        state.persist_funnel()
        return JSONResponse({"status": "ok"})

    @router.post("/api/setup/local-backend")
    async def setup_start_local_backend(request: Request, payload: dict = None):
        if db.count_users(state.conn) == 0:
            return JSONResponse({"error": "create_admin_first"}, status_code=400)
        payload = payload or {}
        media_paths = payload.get("media_paths", [])
        if not media_paths:
            return JSONResponse({"error": "media_paths_required"}, status_code=400)
        import subprocess, secrets as pysecrets
        secret = pysecrets.token_hex(16)
        port = int(payload.get("port", 8080))
        name = payload.get("name") or "Local Backend"
        data_dir = os.path.join(state.data_dir, "local-backend")
        os.makedirs(data_dir, exist_ok=True)
        config = {
            "server": {
                "id": "local-1", "name": name,
                "host": "127.0.0.1", "port": port,
                "data_dir": data_dir,
                "media_paths": media_paths,
                "transcoding": {"enabled": False, "hardware_accel": "none", "max_concurrent_streams": 4}
            },
            "frontend": {
                "enabled": True,
                "frontend_host": "127.0.0.1",
                "frontend_port": int(payload.get("frontend_port", 3000)),
                "secret": secret,
                "heartbeat_interval": 10
            },
            "monitoring": {"enabled": False, "metrics_port": 0}
        }
        config_path = os.path.join(state.data_dir, "local-backend-config.json")
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        backend_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here)))), "backend", "librestreamer-server")
        if not os.path.exists(backend_bin):
            return JSONResponse({"error": "backend_binary_not_found", "hint": "Build the Go backend first"}, status_code=500)
        log_path = os.path.join(state.data_dir, "local-backend.log")
        lf = open(log_path, "a")
        proc = subprocess.Popen([backend_bin, "-config", config_path], stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, start_new_session=True)
        with open(os.path.join(state.data_dir, "local-backend.pid"), "w") as f:
            f.write(str(proc.pid))
        b = {"name": name, "type": "librestreamer", "host": "127.0.0.1", "port": port,
             "secret": secret, "enabled": True, "priority": 1, "max_streams": 4}
        db.upsert_backend(state.conn, b)
        state.reload_clients()
        state.persist_funnel()
        return JSONResponse({"status": "ok", "secret": secret, "port": port})

    @router.post("/api/setup/finish")
    async def setup_finish(request: Request):
        if db.count_users(state.conn) == 0:
            return JSONResponse({"error": "no_admin"}, status_code=400)
        return JSONResponse({"status": "ok"})

    return router
