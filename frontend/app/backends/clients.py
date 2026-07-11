"""Backend clients for librestreamer and Jellyfin servers.

Both implement the same interface so the frontend can treat them uniformly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger("librestreamer.backends")

TIMEOUT = 8.0


@dataclass
class BackendInfo:
    name: str
    type: str  # "librestreamer" | "jellyfin"
    host: str = ""
    port: int = 0
    secret: str = ""
    url: str = ""
    api_key: str = ""
    user_id: str = ""
    ssl: bool = False
    priority: int = 1
    max_streams: int = 4
    enabled: bool = True
    weight: float = 1.0

    @property
    def base_url(self) -> str:
        if self.type == "jellyfin":
            scheme = "https" if self.ssl else "http"
            return f"{scheme}://{self.host}:{self.port}".rstrip("/")
        return f"http://{self.host}:{self.port}"


class BackendClient:
    name: str
    kind: str
    info: BackendInfo

    def fetch_items(self) -> list[dict]: ...
    def fetch_metrics(self) -> dict[str, Any] | None: ...
    def rescan(self) -> bool: ...
    def stream_url(self, remote_id: str) -> str: ...
    def thumbnail_url(self, remote_id: str) -> str | None: ...
    def upload_url(self) -> str | None: ...
    def dir_url(self) -> str | None: ...
    def supports_upload(self) -> bool: ...
    @property
    def headers(self) -> dict[str, str]: ...


class LibrestreamerClient(BackendClient):
    def __init__(self, info: BackendInfo, session: requests.Session):
        self.info = info
        self.session = session
        self.name = info.name
        self.kind = "librestreamer"
        self.base = info.base_url
        self._headers = {"X-Librestreamer-Secret": info.secret}

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._headers)

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def health(self) -> bool:
        try:
            r = self.session.get(f"{self.base}/health", timeout=TIMEOUT)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def fetch_items(self) -> list[dict]:
        r = self.session.get(self._url("/api/library"), headers=self._headers, timeout=TIMEOUT)
        r.raise_for_status()
        out = []
        for raw in r.json().get("items", []):
            out.append({
                "remote_id": raw.get("id", raw.get("ID", "")),
                "type": raw.get("type", raw.get("Type", "movie")),
                "title": raw.get("title", raw.get("Title", "")),
                "year": int(raw.get("year", raw.get("Year", 0)) or 0),
                "parent_id": raw.get("parent_id", raw.get("ParentID", "")) or "",
                "show_name": raw.get("show_name", raw.get("ShowName", "")) or "",
                "season": int(raw.get("season", raw.get("Season", 0)) or 0),
                "episode": int(raw.get("episode", raw.get("Episode", 0)) or 0),
                "size": int(raw.get("size", raw.get("Size", 0)) or 0),
                "mime_type": raw.get("mime_type", raw.get("MimeType", "")) or "",
                "resolution": raw.get("resolution", raw.get("Resolution", "")) or "",
                "codec": raw.get("codec", raw.get("Codec", "")) or "",
                "duration": int(raw.get("duration", raw.get("Duration", 0)) or 0),
                "has_thumbnail": bool(raw.get("has_thumbnail", raw.get("HasThumbnail", False))),
            })
        return out

    def fetch_metrics(self) -> dict[str, Any] | None:
        try:
            r = self.session.get(self._url("/api/metrics"), headers=self._headers, timeout=TIMEOUT)
            if r.status_code != 200:
                return None
            return r.json()
        except requests.RequestException:
            return None

    def rescan(self) -> bool:
        try:
            r = self.session.post(self._url("/api/rescan"), headers=self._headers, timeout=TIMEOUT)
            return r.status_code in (200, 202)
        except requests.RequestException:
            return False

    def stream_url(self, remote_id: str) -> str:
        return self._url(f"/api/stream/{remote_id}")

    def hls_url(self, remote_id: str) -> str:
        return self._url(f"/api/hls/{remote_id}")

    def thumbnail_url(self, remote_id: str) -> str | None:
        return self._url(f"/api/thumbnail/{remote_id}")

    def upload_url(self) -> str | None:
        return self._url("/api/upload")

    def dir_url(self) -> str | None:
        return self._url("/api/dir")

    def supports_upload(self) -> bool:
        return True


_JF_TYPE_MAP = {"Movie": "movie", "Series": "show", "Episode": "episode", "Audio": "music"}


class JellyfinClient(BackendClient):
    def __init__(self, info: BackendInfo, session: requests.Session):
        self.info = info
        self.session = session
        self.name = info.name
        self.kind = "jellyfin"
        self.base = info.base_url
        self._auth = info.api_key
        if not info.user_id:
            info.user_id = self._resolve_user()
        self._headers = {"X-Emby-Token": info.api_key, "X-MediaBrowser-Token": info.api_key}

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._headers)

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _params(self, extra: dict | None = None) -> dict:
        p = {"api_key": self._auth}
        if extra:
            p.update(extra)
        return p

    def _resolve_user(self) -> str:
        r = self.session.get(self._url("/Users"), params=self._params(),
                             headers=self._headers, timeout=TIMEOUT)
        r.raise_for_status()
        users = r.json()
        return users[0]["Id"] if users else ""

    def health(self) -> bool:
        try:
            r = self.session.get(self._url("/System/Info"), headers=self._headers, timeout=TIMEOUT)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def fetch_items(self) -> list[dict]:
        r = self.session.get(
            self._url(f"/Users/{self.info.user_id}/Items"),
            params=self._params({"Recursive": "true", "Fields": "BasicSyncInfo,MediaSources",
                                 "EnableImages": "false"}),
            headers=self._headers, timeout=2 * TIMEOUT,
        )
        r.raise_for_status()
        out = []
        for raw in r.json().get("Items", []):
            jtype = raw.get("Type", "")
            itype = _JF_TYPE_MAP.get(jtype, "movie")
            iid = raw.get("Id", "")
            sources = raw.get("MediaSources") or []
            size = int(sources[0].get("Size", 0)) if sources else 0
            mime = sources[0].get("Container", "") if sources else ""
            if mime and "." not in mime:
                mime = f"video/{mime}" if itype != "music" else f"audio/{mime}"
            out.append({
                "remote_id": iid,
                "type": itype,
                "title": raw.get("Name", ""),
                "year": int(raw.get("ProductionYear", 0) or 0),
                "parent_id": raw.get("ParentId") or "",
                "show_name": raw.get("SeriesName", "") or "",
                "season": int(raw.get("ParentIndexNumber", 0) or 0),
                "episode": int(raw.get("IndexNumber", 0) or 0),
                "size": size,
                "mime_type": mime,
                "resolution": "",
                "codec": "",
                "duration": int(raw.get("RunTimeTicks", 0) or 0) // 10000000,
                "has_thumbnail": bool(raw.get("ImageTags", {}).get("Primary")),
            })
        return out

    def fetch_metrics(self) -> dict[str, Any] | None:
        try:
            r = self.session.get(self._url("/System/Info"), headers=self._headers, timeout=TIMEOUT)
            if r.status_code != 200:
                return None
            info = r.json()
            return {
                "server_id": info.get("Id", ""),
                "server_name": info.get("ServerName", self.name),
                "cpu_usage_pct": -1.0,
                "memory_usage_pct": -1.0,
                "gpu_usage_pct": -1.0,
                "active_streams": 0,
                "timestamp": 0,
            }
        except requests.RequestException:
            return None

    def rescan(self) -> bool:
        return True

    def stream_url(self, remote_id: str) -> str:
        return self._url(f"/Videos/{remote_id}/stream") + f"?api_key={self._auth}&Static=true"

    def thumbnail_url(self, remote_id: str) -> str | None:
        return self._url(f"/Items/{remote_id}/Images/Primary") + f"?api_key={self._auth}"

    def upload_url(self) -> str | None:
        return None

    def dir_url(self) -> str | None:
        return None

    def supports_upload(self) -> bool:
        return False


def build_clients(backend_rows: list, session: requests.Session) -> dict[str, BackendClient]:
    """Build client instances from DB backend rows."""
    clients = {}
    for row in backend_rows:
        if not row["enabled"]:
            continue
        info = BackendInfo(
            name=row["name"], type=row["type"], host=row["host"], port=row["port"],
            secret=row["secret"], url=row["url"], api_key=row["api_key"],
            user_id=row["user_id"], ssl=bool(row["ssl"]),
            priority=row["priority"], max_streams=row["max_streams"],
            enabled=bool(row["enabled"]), weight=row["weight"],
        )
        try:
            if info.type == "librestreamer":
                clients[info.name] = LibrestreamerClient(info, session)
            elif info.type == "jellyfin":
                clients[info.name] = JellyfinClient(info, session)
        except Exception as e:
            log.warning("failed to build client for %s: %s", info.name, e)
    return clients
