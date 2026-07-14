"""SQLite database for the frontend.

Stores users, aggregated media items, backends, play sessions, and metrics history.
"""
from __future__ import annotations

import sqlite3
import os
import time
import json
from dataclasses import dataclass, field
from typing import Any


def open_db(data_dir: str) -> sqlite3.Connection:
    os.makedirs(data_dir, exist_ok=True
    )
    db_path = os.path.join(data_dir, "frontend.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0,
    created_at REAL DEFAULT 0,
    preferences TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS backends (
    name TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    host TEXT DEFAULT '',
    port INTEGER DEFAULT 0,
    secret TEXT DEFAULT '',
    url TEXT DEFAULT '',
    api_key TEXT DEFAULT '',
    user_id TEXT DEFAULT '',
    ssl INTEGER DEFAULT 0,
    priority INTEGER DEFAULT 1,
    max_streams INTEGER DEFAULT 4,
    enabled INTEGER DEFAULT 1,
    weight REAL DEFAULT 1.0,
    registered INTEGER DEFAULT 0,
    last_heartbeat REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS media_items (
    id TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    backend_type TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER DEFAULT 0,
    parent_id TEXT DEFAULT '',
    show_name TEXT DEFAULT '',
    season INTEGER DEFAULT 0,
    episode INTEGER DEFAULT 0,
    size INTEGER DEFAULT 0,
    mime_type TEXT DEFAULT '',
    resolution TEXT DEFAULT '',
    codec TEXT DEFAULT '',
    duration INTEGER DEFAULT 0,
    has_thumbnail INTEGER DEFAULT 0,
    sources TEXT DEFAULT '{}',
    updated_at REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_media_type ON media_items(type);
CREATE INDEX IF NOT EXISTS idx_media_parent ON media_items(parent_id);
CREATE INDEX IF NOT EXISTS idx_media_backend ON media_items(backend);

CREATE TABLE IF NOT EXISTS play_sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    item_id TEXT,
    backend TEXT,
    quality TEXT DEFAULT '',
    start_time REAL DEFAULT 0,
    position INTEGER DEFAULT 0,
    duration INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS watch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    item_title TEXT DEFAULT '',
    item_type TEXT DEFAULT '',
    position INTEGER DEFAULT 0,
    duration INTEGER DEFAULT 0,
    watched_at REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_history_user ON watch_history(user_id);

CREATE TABLE IF NOT EXISTS metrics_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backend TEXT NOT NULL,
    cpu_usage REAL DEFAULT 0,
    memory_usage REAL DEFAULT 0,
    gpu_usage REAL DEFAULT 0,
    active_streams INTEGER DEFAULT 0,
    timestamp REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_metrics_backend ON metrics_history(backend);
CREATE INDEX IF NOT EXISTS idx_metrics_time ON metrics_history(timestamp);
""")
    conn.commit()


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]


# ---- user operations ----

def get_user_by_name(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

def get_user_by_id(conn: sqlite3.Connection, uid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM users ORDER BY id").fetchall()

def add_user(conn: sqlite3.Connection, username: str, password_hash: str, is_admin: bool = False) -> bool:
    try:
        conn.execute("INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,?,?)",
                     (username, password_hash, 1 if is_admin else 0, time.time()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def remove_user(conn: sqlite3.Connection, username: str) -> bool:
    if username == "admin":
        return False
    cur = conn.execute("DELETE FROM users WHERE username=?", (username,))
    conn.commit()
    return cur.rowcount > 0

def change_password(conn: sqlite3.Connection, username: str, password_hash: str) -> bool:
    cur = conn.execute("UPDATE users SET password_hash=? WHERE username=?", (password_hash, username))
    conn.commit()
    return cur.rowcount > 0

def update_user_preferences(conn: sqlite3.Connection, uid: int, prefs: dict) -> bool:
    cur = conn.execute("UPDATE users SET preferences=? WHERE id=?", (json.dumps(prefs), uid))
    conn.commit()
    return cur.rowcount > 0


# ---- backend operations ----

def list_backends(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM backends ORDER BY priority").fetchall()

def get_backend(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM backends WHERE name=?", (name,)).fetchone()

def upsert_backend(conn: sqlite3.Connection, b: dict) -> None:
    conn.execute("""
        INSERT INTO backends (name,type,host,port,secret,url,api_key,user_id,ssl,priority,max_streams,enabled,weight,registered,last_heartbeat)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
        ON CONFLICT(name) DO UPDATE SET
            type=excluded.type, host=excluded.host, port=excluded.port,
            secret=excluded.secret, url=excluded.url, api_key=excluded.api_key,
            user_id=excluded.user_id, ssl=excluded.ssl, priority=excluded.priority,
            max_streams=excluded.max_streams, enabled=excluded.enabled, weight=excluded.weight
    """, (b["name"], b.get("type","librestreamer"), b.get("host",""), b.get("port",0),
          b.get("secret",""), b.get("url",""), b.get("api_key",""), b.get("user_id",""),
          1 if b.get("ssl") else 0, b.get("priority",1), b.get("max_streams",4),
          1 if b.get("enabled",True) else 0, b.get("weight",1.0)))
    conn.commit()

def remove_backend(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("DELETE FROM backends WHERE name=?", (name,))
    conn.execute("DELETE FROM media_items WHERE backend=?", (name,))
    conn.execute("DELETE FROM metrics_history WHERE backend=?", (name,))
    conn.execute("DELETE FROM play_sessions WHERE backend=?", (name,))
    conn.commit()
    return cur.rowcount > 0

def update_backend_enabled(conn: sqlite3.Connection, name: str, enabled: bool) -> bool:
    cur = conn.execute("UPDATE backends SET enabled=? WHERE name=?", (1 if enabled else 0, name))
    conn.commit()
    return cur.rowcount > 0

def update_backend(conn: sqlite3.Connection, name: str, updates: dict) -> bool:
    allowed = {"host", "port", "secret", "priority", "max_streams", "weight",
               "api_key", "ssl", "user_id", "url", "enabled"}
    sets = []
    vals = []
    for k, v in updates.items():
        if k in allowed:
            if k == "ssl" or k == "enabled":
                v = 1 if v else 0
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return False
    vals.append(name)
    cur = conn.execute(f"UPDATE backends SET {','.join(sets)} WHERE name=?", vals)
    conn.commit()
    return cur.rowcount > 0

def update_heartbeat(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("UPDATE backends SET last_heartbeat=?, registered=1 WHERE name=?", (time.time(), name))
    conn.commit()

def count_media_by_backend(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT backend, COUNT(*) as count FROM media_items GROUP BY backend ORDER BY backend"
    ).fetchall()

def count_media_types(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT type, COUNT(*) as count FROM media_items GROUP BY type ORDER BY type"
    ).fetchall()

def get_active_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM play_sessions WHERE active=1 ORDER BY start_time DESC"
    ).fetchall()

def close_session_by_id(conn: sqlite3.Connection, sid: str) -> bool:
    cur = conn.execute("UPDATE play_sessions SET active=0 WHERE id=?", (sid,))
    conn.commit()
    return cur.rowcount > 0


# ---- media item operations ----

def clear_media_for_backend(conn: sqlite3.Connection, backend: str) -> None:
    conn.execute("DELETE FROM media_items WHERE backend=?", (backend,))
    conn.commit()

def upsert_media_item(conn: sqlite3.Connection, item: dict) -> None:
    conn.execute("""
        INSERT INTO media_items (id,backend,backend_type,remote_id,type,title,year,parent_id,
            show_name,season,episode,size,mime_type,resolution,codec,duration,has_thumbnail,sources,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            backend=excluded.backend, backend_type=excluded.backend_type,
            remote_id=excluded.remote_id, title=excluded.title, year=excluded.year,
            parent_id=excluded.parent_id, show_name=excluded.show_name,
            season=excluded.season, episode=excluded.episode, size=excluded.size,
            mime_type=excluded.mime_type, resolution=excluded.resolution, codec=excluded.codec,
            duration=excluded.duration, has_thumbnail=excluded.has_thumbnail,
            sources=excluded.sources, updated_at=excluded.updated_at
    """, (item["id"], item["backend"], item["backend_type"], item["remote_id"],
          item["type"], item["title"], item.get("year",0), item.get("parent_id",""),
          item.get("show_name",""), item.get("season",0), item.get("episode",0),
          item.get("size",0), item.get("mime_type",""), item.get("resolution",""),
          item.get("codec",""), item.get("duration",0),
          1 if item.get("has_thumbnail") else 0,
          json.dumps(item.get("sources",{})), time.time()))
    conn.commit()

def get_media_items(conn: sqlite3.Connection, type_filter: str | None = None) -> list[sqlite3.Row]:
    if type_filter:
        return conn.execute("SELECT * FROM media_items WHERE type=? ORDER BY title", (type_filter,)).fetchall()
    return conn.execute("SELECT * FROM media_items ORDER BY title").fetchall()

def get_media_item(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM media_items WHERE id=?", (item_id,)).fetchone()

def get_children(conn: sqlite3.Connection, parent_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM media_items WHERE parent_id=? ORDER BY season,episode", (parent_id,)).fetchall()

def search_media(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[sqlite3.Row]:
    q = f"%{query}%"
    return conn.execute(
        "SELECT * FROM media_items WHERE title LIKE ? OR show_name LIKE ? ORDER BY title LIMIT ?",
        (q, q, limit)
    ).fetchall()


# ---- play sessions ----

def create_session(conn: sqlite3.Connection, user_id: int, item_id: str, backend: str, quality: str = "") -> str:
    import secrets
    sid = secrets.token_hex(8)
    conn.execute(
        "INSERT INTO play_sessions (id,user_id,item_id,backend,quality,start_time,active) VALUES (?,?,?,?,?,?,1)",
        (sid, user_id, item_id, backend, quality, time.time())
    )
    conn.commit()
    return sid

def close_session(conn: sqlite3.Connection, sid: str, position: int = 0, duration: int = 0) -> None:
    conn.execute("UPDATE play_sessions SET active=0, position=?, duration=? WHERE id=?", (position, duration, sid))
    conn.commit()


# ---- watch history ----

def add_watch_history(conn: sqlite3.Connection, user_id: int, item_id: str, title: str, itype: str,
                      position: int = 0, duration: int = 0) -> None:
    conn.execute(
        "INSERT INTO watch_history (user_id,item_id,item_title,item_type,position,duration,watched_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, item_id, title, itype, position, duration, time.time())
    )
    conn.commit()

def get_watch_history(conn: sqlite3.Connection, user_id: int, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM watch_history WHERE user_id=? ORDER BY watched_at DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()


# ---- metrics history ----

def add_metrics(conn: sqlite3.Connection, backend: str, cpu: float, mem: float, gpu: float, streams: int) -> None:
    conn.execute(
        "INSERT INTO metrics_history (backend,cpu_usage,memory_usage,gpu_usage,active_streams,timestamp) VALUES (?,?,?,?,?,?)",
        (backend, cpu, mem, gpu, streams, time.time())
    )
    conn.commit()

def get_metrics_history(conn: sqlite3.Connection, backend: str, limit: int = 60) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM metrics_history WHERE backend=? ORDER BY timestamp DESC LIMIT ?",
        (backend, limit)
    ).fetchall()
