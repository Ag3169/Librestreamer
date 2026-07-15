"""LibreStreamer frontend - main FastAPI application.

Run with:  python -m app.main --funnel funnel.json
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .db import database as db
from .db.database import open_db
from .backends.clients import build_clients, BackendClient
from .balancer.engine import LoadBalancer
from .monitor.poller import MetricsPoller
from .api.routes import get_router as get_api_router
from .api.jellyfin import get_router as get_jf_router
from .ui.routes import get_router as get_ui_router

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("librestreamer")


class AppState:
    """Shared application state accessible from route handlers."""

    def __init__(self, funnel_path: str, data_dir: str):
        self.funnel_path = funnel_path
        self.data_dir = data_dir
        self.conn = open_db(data_dir)
        self.session = requests.Session()
        self.clients: dict[str, BackendClient] = {}
        self.balancer = LoadBalancer(
            auto_load_balance=True,
            failover_timeout=10.0,
        )
        self.poller: MetricsPoller | None = None
        self._session_secret = "change-me"

    def sign_session(self, user_id: int) -> str:
        """Create a signed session cookie value."""
        payload = f"{user_id}:{int(time.time())}"
        sig = hmac.new(self._session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        return f"{payload}:{sig}"

    def verify_session(self, cookie_val: str) -> int | None:
        """Verify session cookie and return user_id, or None."""
        try:
            parts = cookie_val.rsplit(":", 2)
            if len(parts) != 3:
                return None
            uid_str, ts_str, sig = parts
            payload = f"{uid_str}:{ts_str}"
            expected = hmac.new(self._session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
            if not hmac.compare_digest(sig, expected):
                return None
            return int(uid_str)
        except (ValueError, IndexError):
            return None

    def load_funnel(self) -> None:
        """Load funnel.json and populate the database."""
        with open(self.funnel_path, "r") as f:
            raw = json.load(f)
        self._session_secret = raw.get("frontend", {}).get("session_secret", "change-me")
        frontend_cfg = raw.get("frontend", {})
        self.balancer.auto_load_balance = frontend_cfg.get("auto_load_balance", True)
        self.balancer.failover_timeout = frontend_cfg.get("failover_timeout", 10)
        for b in raw.get("backends", []):
            db.upsert_backend(self.conn, b)
        log.info("loaded funnel.json: %d backends", len(raw.get("backends", [])))

    def persist_funnel(self) -> None:
        """Save current backends from DB back to funnel.json."""
        try:
            with open(self.funnel_path, "r") as f:
                raw = json.load(f)
            rows = db.list_backends(self.conn)
            out = []
            for r in rows:
                entry = {
                    "name": r["name"], "type": r["type"],
                    "host": r["host"], "port": r["port"],
                    "priority": r["priority"], "max_streams": r["max_streams"],
                    "enabled": bool(r["enabled"]),
                }
                if r["type"] == "librestreamer":
                    entry["secret"] = r["secret"]
                else:
                    entry["api_key"] = r["api_key"]
                    entry["ssl"] = bool(r["ssl"])
                    if r["user_id"]:
                        entry["user_id"] = r["user_id"]
                if r["weight"] != 1.0:
                    entry["weight"] = r["weight"]
                out.append(entry)
            raw["backends"] = out
            with open(self.funnel_path, "w") as f:
                json.dump(raw, f, indent=2)
        except Exception as e:
            log.warning("failed to persist funnel.json: %s", e)

    def reload_clients(self) -> None:
        """Rebuild backend clients from database."""
        rows = db.list_backends(self.conn)
        self.clients = build_clients(rows, self.session)
        if self.poller:
            self.poller.clients = self.clients
        log.info("loaded %d backend clients", len(self.clients))

    def refresh_libraries(self) -> int:
        """Fetch items from all backends and update the media_items table."""
        total = 0
        for name, client in self.clients.items():
            try:
                items = client.fetch_items()
                db.clear_media_for_backend(self.conn, name)
                for it in items:
                    pub_id = hashlib.sha1(
                        f"{it['type']}:{it['title'].lower()}:{it.get('show_name','')}:"
                        f"{it.get('season',0)}:{it.get('episode',0)}".encode()
                    ).hexdigest()[:16]
                    it["id"] = pub_id
                    it["backend"] = name
                    it["backend_type"] = client.kind
                    existing = db.get_media_item(self.conn, pub_id)
                    if existing:
                        sources = json.loads(existing["sources"]) if existing["sources"] else {}
                    else:
                        sources = {}
                    sources[name] = it["remote_id"]
                    it["sources"] = sources
                    db.upsert_media_item(self.conn, it)
                    total += 1
                log.info("fetched %d items from %s", len(items), name)
            except Exception as e:
                log.warning("failed to fetch from %s: %s", name, e)
        log.info("aggregated %d items across %d backends", total, len(self.clients))
        return total


def create_app(funnel_path: str, data_dir: str = "./data") -> FastAPI:
    state = AppState(funnel_path, data_dir)
    state.load_funnel()
    state.reload_clients()
    state.refresh_libraries()

    state.poller = MetricsPoller(state.clients, state.balancer, state.conn, interval=5.0)
    state.poller.start()

    app = FastAPI(title="LibreStreamer", version="1.0.0")

    here = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(here, "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    uploads_dir = os.path.join(state.data_dir, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if path in ("/login", "/logout", "/healthz", "/favicon.ico") or path.startswith("/static/") or path.startswith("/uploads/"):
            return await call_next(request)
        if path.startswith("/System") or path.startswith("/Users") or path.startswith("/Videos") or path.startswith("/Audio") or path.startswith("/Items") or path.startswith("/Sessions") or path.startswith("/DisplayPreferences") or path.startswith("/Branding") or path.startswith("/web/"):
            request.state.user = None
            request.state.user_id = 0
            return await call_next(request)

        # Setup mode: no users exist yet — only allow /setup
        is_first_run = db.count_users(state.conn) == 0
        if is_first_run:
            if path == "/setup" or path.startswith("/api/setup/"):
                request.state.user = None
                request.state.user_id = 0
                return await call_next(request)
            if path.startswith("/api/"):
                return JSONResponse({"error": "setup_required"}, status_code=503)
            return RedirectResponse(url="/setup", status_code=302)

        # After setup is done, block /setup
        if path == "/setup":
            return RedirectResponse(url="/", status_code=302)

        session_cookie = request.cookies.get("ls_session")
        user_id = None
        if session_cookie:
            user_id = state.verify_session(session_cookie)
        if user_id:
            row = db.get_user_by_id(state.conn, user_id)
            request.state.user = row
            request.state.user_id = user_id
        else:
            request.state.user = None
            request.state.user_id = 0

        if path.startswith("/api/"):
            if path in ("/api/backends/register", "/api/backends/heartbeat"):
                return await call_next(request)
            if not user_id:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            if path.startswith("/api/admin/") and (not row or not row["is_admin"]):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            return await call_next(request)

        if not user_id:
            return RedirectResponse(url="/login", status_code=302)
        if path.startswith("/admin") and (not row or not row["is_admin"]):
            return RedirectResponse(url="/", status_code=302)

        return await call_next(request)

    app.include_router(get_api_router(state))
    app.include_router(get_jf_router(state))
    app.include_router(get_ui_router(state))

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "time": int(time.time())}

    app.state.ls = state
    return app


def main():
    parser = argparse.ArgumentParser(description="LibreStreamer frontend")
    parser.add_argument("--funnel", default=os.environ.get("LIBRESTREAMER_FUNNEL", "funnel.json"),
                        help="path to funnel.json")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--data-dir", default=os.environ.get("LIBRESTREAMER_DATA_DIR", "./data"))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    with open(args.funnel, "r") as f:
        funnel = json.load(f)
    frontend_cfg = funnel.get("frontend", {})
    host = args.host or os.environ.get("LIBRESTREAMER_FRONTEND_HOST") or frontend_cfg.get("host", "0.0.0.0")
    port = args.port or int(os.environ.get("LIBRESTREAMER_FRONTEND_PORT", 0)) or frontend_cfg.get("port", 3000)

    app = create_app(args.funnel, args.data_dir)
    log.info("LibreStreamer frontend on %s:%d (funnel=%s)", host, port, args.funnel)
    uvicorn.run(app, host=host, port=port, log_level="info" if not args.debug else "debug")


if __name__ == "__main__":
    main()
