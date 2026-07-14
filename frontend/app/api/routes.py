"""Unified LibreStreamer API routes.

These endpoints serve the web UI and any LibreStreamer-compatible client.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests
from fastapi import APIRouter, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse

from ..db import database as db
from ..backends.clients import BackendClient

log = logging.getLogger("librestreamer.api")
router = APIRouter()


def _item_to_dict(row) -> dict:
    sources = {}
    try:
        sources = json.loads(row["sources"]) if row["sources"] else {}
    except:
        pass
    return {
        "id": row["id"],
        "backend": row["backend"],
        "backend_type": row["backend_type"],
        "remote_id": row["remote_id"],
        "type": row["type"],
        "title": row["title"],
        "year": row["year"],
        "parent_id": row["parent_id"],
        "show_name": row["show_name"],
        "season": row["season"],
        "episode": row["episode"],
        "size": row["size"],
        "mime_type": row["mime_type"],
        "resolution": row["resolution"],
        "codec": row["codec"],
        "duration": row["duration"],
        "has_thumbnail": bool(row["has_thumbnail"]),
        "sources": sources,
    }


def get_router(state) -> APIRouter:
    """Build router with access to shared app state."""

    @router.get("/api/library")
    async def api_library(type: str | None = None):
        rows = db.get_media_items(state.conn, type)
        return {"count": len(rows), "items": [_item_to_dict(r) for r in rows]}

    @router.get("/api/library/{item_type}/{item_id}")
    async def api_library_item(item_type: str, item_id: str):
        row = db.get_media_item(state.conn, item_id)
        if not row:
            raise HTTPException(404, "not found")
        item = _item_to_dict(row)
        children = db.get_children(state.conn, item_id)
        item["children"] = [_item_to_dict(c) for c in children]
        return item

    @router.get("/api/stream/{item_id}")
    async def api_stream(item_id: str, request: Request):
        row = db.get_media_item(state.conn, item_id)
        if not row:
            raise HTTPException(404, "not found")
        sources = json.loads(row["sources"]) if row["sources"] else {}
        candidates = list(sources.keys())
        if not candidates:
            raise HTTPException(404, "no source backend")
        # select backend via load balancer
        chosen = state.balancer.select_backend(candidates) or candidates[0]
        client = state.clients.get(chosen)
        if not client:
            # failover
            for name in candidates:
                if name in state.clients:
                    client = state.clients[name]
                    chosen = name
                    break
        if not client:
            raise HTTPException(503, "no backend available")
        remote_id = sources.get(chosen, row["remote_id"])
        url = client.stream_url(remote_id)
        # create play session
        user_id = getattr(request.state, "user_id", 0) or 0
        db.create_session(state.conn, user_id, item_id, chosen)
        # forward the stream with range support
        upstream_headers = dict(client.headers)
        range_header = request.headers.get("range")
        if range_header:
            upstream_headers["Range"] = range_header
        try:
            r = client.session.get(url, headers=upstream_headers, stream=True, timeout=30.0)
        except requests.RequestException as e:
            raise HTTPException(502, f"upstream error: {e}")
        skip = {"connection", "keep-alive", "transfer-encoding", "content-encoding"}
        out_headers = {k: v for k, v in r.headers.items() if k.lower() not in skip}

        def generate():
            try:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                r.close()

        return StreamingResponse(generate(), status_code=r.status_code,
                                 headers=out_headers)

    @router.get("/api/thumbnail/{item_id}")
    async def api_thumbnail(item_id: str):
        row = db.get_media_item(state.conn, item_id)
        if not row:
            raise HTTPException(404)
        sources = json.loads(row["sources"]) if row["sources"] else {}
        candidates = list(sources.keys())
        if not candidates:
            raise HTTPException(404)
        chosen = state.balancer.select_backend(candidates) or candidates[0]
        client = state.clients.get(chosen)
        if not client:
            for name in candidates:
                if name in state.clients:
                    client = state.clients[name]
                    break
        if not client:
            raise HTTPException(503)
        remote_id = sources.get(chosen, row["remote_id"])
        url = client.thumbnail_url(remote_id)
        if not url:
            raise HTTPException(404)
        try:
            r = client.session.get(url, headers=client.headers, timeout=15.0)
        except requests.RequestException:
            raise HTTPException(502)
        if r.status_code != 200:
            raise HTTPException(r.status_code)
        return Response(content=r.content, media_type=r.headers.get("Content-Type", "image/jpeg"))

    @router.get("/api/status")
    async def api_status():
        return {
            "frontend": {
                "version": "1.0.0",
                "auto_load_balance": state.balancer.auto_load_balance,
            },
            "backends": state.balancer.states(),
            "item_count": len(db.get_media_items(state.conn)),
        }

    @router.post("/api/refresh")
    async def api_refresh():
        n = state.refresh_libraries()
        return {"status": "ok", "items": n}

    @router.get("/api/search")
    async def api_search(q: str, limit: int = 50):
        rows = db.search_media(state.conn, q, limit)
        return {"count": len(rows), "items": [_item_to_dict(r) for r in rows]}

    # ---- favorites ----

    @router.get("/api/favorites")
    async def api_favorites(request: Request):
        user_id = getattr(request.state, "user_id", 0) or 0
        if not user_id:
            return {"items": []}
        rows = db.get_favorites(state.conn, user_id)
        return {"items": [_item_to_dict(r) for r in rows]}

    @router.post("/api/favorites/{item_id}")
    async def api_toggle_favorite(item_id: str, request: Request):
        user_id = getattr(request.state, "user_id", 0) or 0
        if not user_id:
            raise HTTPException(401)
        favorited = db.toggle_favorite(state.conn, user_id, item_id)
        return {"favorited": favorited}

    @router.get("/api/favorites/{item_id}")
    async def api_check_favorite(item_id: str, request: Request):
        user_id = getattr(request.state, "user_id", 0) or 0
        if not user_id:
            return {"favorited": False}
        return {"favorited": db.is_favorite(state.conn, user_id, item_id)}

    @router.get("/api/history")
    async def api_history(request: Request):
        user_id = getattr(request.state, "user_id", 0) or 0
        if not user_id:
            return {"items": []}
        rows = db.get_watch_history(state.conn, user_id)
        return {"items": [dict(r) for r in rows]}

    @router.post("/api/progress")
    async def api_progress(request: Request, payload: dict = None):
        user_id = getattr(request.state, "user_id", 0) or 0
        if not user_id or not payload:
            return {"status": "ignored"}
        item_id = payload.get("item_id", "")
        position = int(payload.get("position", 0) or 0)
        duration = int(payload.get("duration", 0) or 0)
        finished = bool(payload.get("finished", False))
        if not item_id:
            return {"status": "ignored"}
        row = db.get_media_item(state.conn, item_id)
        if not row:
            return {"status": "ignored"}
        db.add_watch_history(state.conn, user_id, item_id,
                             row["title"], row["type"], position, duration)
        return {"status": "ok"}

    # ---- backend registration (called by Go backend) ----

    @router.post("/api/backends/register")
    async def api_backend_register(payload: dict):
        name = payload.get("name", "")
        if not name:
            raise HTTPException(400, "name required")
        b = {
            "name": name,
            "type": "librestreamer",
            "host": payload.get("host", ""),
            "port": payload.get("port", 0),
            "secret": payload.get("secret", ""),
            "enabled": True,
            "priority": 1,
            "max_streams": 4,
            "registered": True,
        }
        db.upsert_backend(state.conn, b)
        state.reload_clients()
        state.refresh_libraries()
        state.persist_funnel()
        return {"status": "registered", "name": name}

    @router.post("/api/backends/heartbeat")
    async def api_backend_heartbeat(payload: dict):
        name = payload.get("name", payload.get("id", ""))
        metrics = payload.get("metrics", {})
        if name:
            row = db.get_backend(state.conn, name)
            if not row:
                db.upsert_backend(state.conn, {
                    "name": name,
                    "type": "librestreamer",
                    "host": payload.get("host", ""),
                    "port": payload.get("port", 0),
                    "secret": payload.get("secret", ""),
                    "enabled": True,
                    "priority": 1,
                    "max_streams": 4,
                })
                state.reload_clients()
                state.refresh_libraries()
                state.persist_funnel()
            db.update_heartbeat(state.conn, name)
            state.balancer.update_state(name, "librestreamer", metrics)
        return {"status": "ok"}

    # ---- admin: backend management ----

    @router.post("/api/admin/backends/add")
    async def api_admin_add_backend(payload: dict):
        name = payload.get("name", "")
        if not name:
            raise HTTPException(400, "name required")
        btype = payload.get("type", "librestreamer")
        b = {"name": name, "type": btype, "enabled": True,
             "priority": int(payload.get("priority", 1)),
             "max_streams": int(payload.get("max_streams", 4)),
             "weight": float(payload.get("weight", 1.0))}
        if btype == "librestreamer":
            b["host"] = payload.get("host", "")
            b["port"] = int(payload.get("port", 8080))
            b["secret"] = payload.get("secret", "")
            if not b["secret"]:
                raise HTTPException(400, "secret required")
        else:
            b["host"] = payload.get("host", "")
            b["port"] = int(payload.get("port", 443))
            b["api_key"] = payload.get("api_key", "")
            b["ssl"] = payload.get("ssl", False)
            b["user_id"] = payload.get("user_id", "")
            if not b["api_key"]:
                raise HTTPException(400, "api_key required")
        db.upsert_backend(state.conn, b)
        state.reload_clients()
        state.refresh_libraries()
        # persist to funnel.json
        state.persist_funnel()
        return {"status": "ok"}

    @router.post("/api/admin/backends/{name}/remove")
    async def api_admin_remove_backend(name: str):
        db.remove_backend(state.conn, name)
        state.balancer.remove_state(name)
        state.reload_clients()
        state.persist_funnel()
        return {"status": "ok"}

    @router.post("/api/admin/backends/{name}/toggle")
    async def api_admin_toggle_backend(name: str):
        row = db.get_backend(state.conn, name)
        if not row:
            raise HTTPException(404, "backend not found")
        new_val = not bool(row["enabled"])
        db.update_backend_enabled(state.conn, name, new_val)
        state.reload_clients()
        state.persist_funnel()
        return {"status": "ok", "enabled": new_val}

    @router.post("/api/admin/backends/{name}/edit")
    async def api_admin_edit_backend(name: str, payload: dict):
        row = db.get_backend(state.conn, name)
        if not row:
            raise HTTPException(404, "backend not found")
        updates = {}
        for k in ("host", "port", "secret", "priority", "max_streams", "weight",
                   "api_key", "ssl", "user_id"):
            if k in payload:
                if k in ("port", "priority", "max_streams"):
                    updates[k] = int(payload[k])
                elif k == "weight":
                    updates[k] = float(payload[k])
                elif k == "ssl":
                    updates[k] = bool(payload[k])
                else:
                    updates[k] = payload[k]
        if updates:
            db.update_backend(state.conn, name, updates)
            state.reload_clients()
            state.refresh_libraries()
            state.persist_funnel()
        return {"status": "ok"}

    @router.post("/api/admin/backends/{name}/rescan")
    async def api_admin_rescan_backend(name: str):
        client = state.clients.get(name)
        if not client:
            raise HTTPException(404, f"backend '{name}' not found or disabled")
        try:
            client.rescan()
            state.refresh_libraries()
        except Exception as e:
            raise HTTPException(502, f"rescan failed: {e}")
        return {"status": "ok"}

    @router.post("/api/admin/balancer/toggle")
    async def api_admin_toggle_balancer():
        state.balancer.auto_load_balance = not state.balancer.auto_load_balance
        return {"status": "ok", "auto_load_balance": state.balancer.auto_load_balance}

    @router.get("/api/admin/sessions")
    async def api_admin_sessions():
        rows = db.get_active_sessions(state.conn)
        return {"sessions": [dict(r) for r in rows]}

    @router.post("/api/admin/sessions/{sid}/close")
    async def api_admin_close_session(sid: str):
        if not db.close_session_by_id(state.conn, sid):
            raise HTTPException(404, "session not found")
        return {"status": "ok"}

    # ---- admin: upload ----

    @router.post("/api/admin/upload")
    async def api_admin_upload(backend: str = Form(...), category: str = Form("movies"),
                               subpath: str = Form(""),
                               files: list[UploadFile] = File(..., alias="file")):
        client = state.clients.get(backend)
        if not client or not client.supports_upload():
            raise HTTPException(404, "backend not found or doesn't support uploads")
        url = client.upload_url()
        multipart_files = [("file", (f.filename, await f.read(), f.content_type)) for f in files]
        data = {"category": category}
        if subpath:
            data["subpath"] = subpath
        try:
            r = client.session.post(url, headers=client.headers,
                                    files=multipart_files, data=data, timeout=120.0)
            return r.json()
        except requests.RequestException as e:
            raise HTTPException(502, str(e))

    @router.get("/api/admin/dir")
    async def api_admin_dir(backend: str, category: str = "movies", subpath: str = ""):
        client = state.clients.get(backend)
        if not client or not client.supports_upload():
            raise HTTPException(404)
        url = client.dir_url()
        if not url:
            raise HTTPException(404)
        try:
            r = client.session.get(url, headers=client.headers,
                                   params={"category": category, "subpath": subpath}, timeout=15.0)
            return r.json()
        except requests.RequestException as e:
            raise HTTPException(502, str(e))

    # ---- admin: metrics history ----

    @router.get("/api/admin/metrics/{backend}")
    async def api_admin_metrics_history(backend: str, limit: int = 60):
        rows = db.get_metrics_history(state.conn, backend, limit)
        return {"points": [dict(r) for r in rows]}

    @router.get("/api/admin/stats")
    async def api_admin_stats():
        db_backends = db.list_backends(state.conn)
        balancer_states = {s["name"]: s for s in state.balancer.states()}
        backends = []
        for row in db_backends:
            b = dict(row)
            s = balancer_states.get(b["name"])
            if s:
                b["healthy"] = s["healthy"]
                b["score"] = s["score"]
                b["cpu_usage_pct"] = s["cpu_usage_pct"]
                b["memory_usage_pct"] = s["memory_usage_pct"]
                b["gpu_usage_pct"] = s["gpu_usage_pct"]
                b["gpu_name"] = s["gpu_name"]
                b["memory_total_bytes"] = s["memory_total_bytes"]
                b["memory_used_bytes"] = s["memory_used_bytes"]
                b["disk_total_bytes"] = s["disk_total_bytes"]
                b["disk_used_bytes"] = s["disk_used_bytes"]
                b["disk_usage_pct"] = s["disk_usage_pct"]
                b["active_streams"] = s["active_streams"]
                b["last_seen_ago"] = s["last_seen_ago"]
            else:
                b["healthy"] = False
                b["score"] = 0
                b["cpu_usage_pct"] = -1
                b["memory_usage_pct"] = -1
                b["gpu_usage_pct"] = -1
                b["gpu_name"] = ""
                b["memory_total_bytes"] = 0
                b["memory_used_bytes"] = 0
                b["disk_total_bytes"] = 0
                b["disk_used_bytes"] = 0
                b["disk_usage_pct"] = -1
                b["active_streams"] = 0
                b["last_seen_ago"] = -1
            backends.append(b)
        type_counts = {r["type"]: r["count"] for r in db.count_media_types(state.conn)}
        backend_counts = {r["backend"]: r["count"] for r in db.count_media_by_backend(state.conn)}
        active_sessions = db.get_active_sessions(state.conn)
        return {
            "backends": backends,
            "type_counts": type_counts,
            "backend_counts": backend_counts,
            "total_items": sum(type_counts.values()),
            "active_sessions": len(active_sessions),
            "auto_load_balance": state.balancer.auto_load_balance,
        }

    return router
