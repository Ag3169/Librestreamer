"""Jellyfin API emulation endpoints.

These allow existing Jellyfin clients (Android TV, iOS, Kodi, etc.) to connect
to LibreStreamer transparently.
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Request, Response, HTTPException, Query
from fastapi.responses import StreamingResponse, Response as FastResponse

from ..db import database as db

log = logging.getLogger("librestreamer.jellyfin")
router = APIRouter()


def _item_to_jellyfin(row, user_id: str) -> dict:
    """Convert a LibreStreamer media item to Jellyfin's item format."""
    jtype_map = {"movie": "Movie", "show": "Series", "episode": "Episode", "music": "Audio"}
    jtype = jtype_map.get(row["type"], "Movie")
    item_id = row["id"]
    return {
        "Name": row["title"],
        "Id": item_id,
        "Type": jtype,
        "ProductionYear": row["year"],
        "ParentId": row["parent_id"],
        "SeriesName": row["show_name"],
        "ParentIndexNumber": row["season"],
        "IndexNumber": row["episode"],
        "RunTimeTicks": row["duration"] * 10000000 if row["duration"] else 0,
        "Size": row["size"],
        "Container": row["mime_type"].split("/")[-1] if row["mime_type"] else "",
        "ImageTags": {"Primary": "thumb"} if row["has_thumbnail"] else {},
        "UserData": {"Played": False, "PlayCount": 0},
        "IsFolder": row["type"] in ("show",),
        "DisplayMediaType": "Thumb" if row["has_thumbnail"] else "None",
    }


def get_router(state) -> APIRouter:

    @router.get("/System/Info")
    async def system_info():
        return {
            "ServerName": "LibreStreamer",
            "Version": "1.0.0",
            "Id": "librestreamer-frontend",
            "OperatingSystem": "Linux",
            "HasPendingRestart": False,
            "SupportsLibraryMonitor": False,
        }

    @router.get("/System/Info/Public")
    async def system_info_public():
        return {
            "ServerName": "LibreStreamer",
            "Version": "1.0.0",
            "Id": "librestreamer-frontend",
            "OperatingSystem": "Linux",
        }

    @router.get("/Users")
    async def users():
        rows = db.list_users(state.conn)
        return [{
            "Name": r["username"],
            "Id": str(r["id"]),
            "PrimaryImageTag": "",
            "HasPassword": True,
            "IsAdministrator": bool(r["is_admin"]),
            "Configuration": {"IsHidden": False, "IsDisabled": False},
        } for r in rows]

    @router.get("/Users/{user_id}/Items")
    async def user_items(user_id: str, recursive: bool = Query(False),
                         fields: str = Query(""), type: str | None = Query(None)):
        rows = db.get_media_items(state.conn, type)
        if not recursive:
            # return top-level items (no parent or shows)
            rows = [r for r in rows if r["type"] in ("movie", "show", "music")]
        return {
            "Items": [_item_to_jellyfin(r, user_id) for r in rows],
            "TotalRecordCount": len(rows),
        }

    @router.get("/Users/{user_id}/Items/{item_id}")
    async def user_item(user_id: str, item_id: str):
        row = db.get_media_item(state.conn, item_id)
        if not row:
            raise HTTPException(404)
        result = _item_to_jellyfin(row, user_id)
        if row["type"] == "show":
            children = db.get_children(state.conn, item_id)
            result["ChildCount"] = len(children)
        return result

    @router.get("/Videos/{item_id}/stream")
    async def video_stream(item_id: str, request: Request):
        return await _forward_stream(state, item_id, request)

    @router.get("/Audio/{item_id}/stream")
    async def audio_stream(item_id: str, request: Request):
        return await _forward_stream(state, item_id, request)

    @router.get("/Items/{item_id}/Images/Primary")
    async def item_image(item_id: str):
        return await _forward_thumbnail(state, item_id)

    @router.get("/Sessions")
    async def sessions():
        return []

    @router.get("/Sessions/Capabilities/Full")
    async def sessions_caps():
        return []

    @router.get("/DisplayPreferences/users")
    async def display_prefs(user_id: str = "default"):
        return {"CustomPrefs": {}, "Id": "users", "PrimaryImageTag": ""}

    @router.get("/Branding/Configuration")
    async def branding():
        return {"LoginDisclaimer": "", "SplashscreenEnabled": False}

    @router.get("/web/index.html")
    async def web_index():
        return FastResponse(content="", status_code=302,
                            headers={"Location": "/"})

    return router


async def _forward_stream(state, item_id: str, request: Request) -> StreamingResponse:
    import requests as req_lib
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
                chosen = name
                break
    if not client:
        raise HTTPException(503)
    remote_id = sources.get(chosen, row["remote_id"])
    url = client.stream_url(remote_id)
    upstream_headers = dict(client.headers)
    range_header = request.headers.get("range")
    if range_header:
        upstream_headers["Range"] = range_header
    try:
        r = client.session.get(url, headers=upstream_headers, stream=True, timeout=30.0)
    except req_lib.RequestException as e:
        raise HTTPException(502, str(e))
    skip = {"connection", "keep-alive", "transfer-encoding", "content-encoding"}
    out_headers = {k: v for k, v in r.headers.items() if k.lower() not in skip}

    def generate():
        try:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            r.close()

    return StreamingResponse(generate(), status_code=r.status_code, headers=out_headers)


async def _forward_thumbnail(state, item_id: str) -> Response:
    import requests as req_lib
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
    except req_lib.RequestException:
        raise HTTPException(502)
    return Response(content=r.content,
                    media_type=r.headers.get("Content-Type", "image/jpeg"))
