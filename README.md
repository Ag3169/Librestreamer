# LibreStreamer

A Jellyfin alternative — a personal media server with a clean web UI.
Run it as a single all-in-one server or split into separate frontend/backend nodes.

## Quick Start

```bash
./setup.sh          # interactive setup (single or multi server)
./setup.sh stop     # stop
```

Default login: `admin` / `admin`

```
 ┌──────────────┐      ┌──────────────┐
 │  Backend (Go)│      │  Backend (Go)│
 │  config.json │      │  config.json │
 │  /media/...  │      │  /media/...  │
 └──────┬───────┘      └──────┬───────┘
        │ secret               │ secret
        └──────────┬───────────┘
                   │ funnel.json
          ┌────────┴────────┐
          │Frontend (Python)│
          │ FastAPI + WebUI │
          │ Load balancer   │
          │ Jellyfin compat │
          └────────┬────────┘
                   │
              :3000 (web)
```

## Architecture

### Backend (Go) — `backend/`

Go media server node with SQLite library database, metadata extraction via
ffprobe, thumbnail generation via ffmpeg, HLS streaming, hardware transcoding
(NVENC/VAAPI/QSV/AMF), real-time WebSocket metrics, and frontend registration.

**Works on any machine** — no GPU required. The backend gracefully degrades:
- No GPU: metrics report CPU/RAM only, direct streaming works perfectly
- No ffmpeg/ffprobe: scanner indexes files without metadata, direct streaming
  works, HLS falls back to direct streaming
- No hardware transcoding: HLS uses software encoding (libx264) or falls back
  to direct streaming

**Config:** `backend/config.json`
```json
{
  "server": {
    "id": "node-1",
    "name": "Media Server",
    "host": "0.0.0.0",
    "port": 8080,
    "data_dir": "./data",
    "media_paths": ["./media/movies", "./media/tv", "./media/music"],
    "transcoding": {"enabled": false, "hardware_accel": "none", "max_concurrent_streams": 4}
  },
  "frontend": {
    "enabled": false,
    "frontend_host": "127.0.0.1",
    "frontend_port": 3000,
    "secret": "change-me",
    "heartbeat_interval": 30
  },
  "monitoring": {"enabled": true, "metrics_port": 9090}
}
```

**API Endpoints:**
| Endpoint | Description |
|---|---|
| `GET /health` | Health check |
| `GET /api/library?type=` | Library listing |
| `GET /api/library/{type}/{id}` | Item details |
| `GET /api/stream/{id}` | Direct stream (HTTP Range) |
| `GET /api/hls/{id}` | HLS playlist |
| `GET /api/thumbnail/{id}` | Thumbnail image |
| `GET /api/metrics` | CPU/RAM/GPU metrics |
| `WS /ws/metrics` | Real-time metrics stream |
| `POST /api/rescan` | Rescan media paths |
| `POST /api/upload` | Upload files (multipart) |
| `GET /api/dir` | List directory contents |

### Frontend (Python/FastAPI) — `frontend/`

Web UI that aggregates backends, serves the interface, load balances streams,
and emulates the Jellyfin API for client compatibility.

**Config:** `frontend/funnel.json`
```json
{
  "backends": [
    {"name": "Home Server", "host": "127.0.0.1", "port": 8080,
     "secret": "change-me", "type": "librestreamer", "priority": 1, "enabled": true}
  ],
  "frontend": {"host": "0.0.0.0", "port": 3000, "session_secret": "change-me",
               "auto_load_balance": true, "failover_timeout": 10}
}
```

**Features:**
- User/pass authentication with sessions
- Library aggregation across backend nodes
- Intelligent load balancing (CPU/RAM/GPU/active streams scoring)
- Auto-failover to backup backends
- Jellyfin API emulation (`/System/Info`, `/Users`, `/Items`, `/Videos/{id}/stream`)
- SQLite database (users, media_items, backends, play_sessions, metrics_history)
- Search across all backends
- Admin panel: dashboard, server management, drag-and-drop upload, users, logs
- WebSocket metrics for real-time dashboard

## Docker

```bash
docker-compose up -d
```

## Project Structure

```
librestreamer/
├── backend/                      # Go media server
│   ├── cmd/server/main.go        # entry point
│   ├── internal/
│   │   ├── config/               # config.json loader
│   │   ├── db/                   # SQLite library database
│   │   ├── scanner/              # media discovery + ffprobe metadata
│   │   ├── api/                  # REST + WebSocket handlers
│   │   ├── stream/               # HLS/DASH + direct streaming
│   │   ├── metrics/              # CPU/RAM/GPU monitoring
│   │   ├── nvidia/               # nvidia-smi GPU probe
│   │   ├── upload/               # file upload handler
│   │   └── frontend/             # frontend registration + heartbeat
│   ├── config.json
│   └── Dockerfile
├── frontend/                     # Python FastAPI frontend
│   ├── app/
│   │   ├── main.py               # FastAPI app + middleware
│   │   ├── auth.py               # password hashing
│   │   ├── api/                  # unified API + Jellyfin emulation
│   │   ├── backends/             # backend clients
│   │   ├── balancer/             # load balancing engine
│   │   ├── monitor/              # metrics polling
│   │   ├── db/                   # SQLite database
│   │   ├── ui/                   # web routes + templates
│   │   ├── static/               # CSS + JS
│   │   └── templates/            # Jinja2 (admin + public)
│   ├── funnel.json
│   ├── requirements.txt
│   └── Dockerfile
├── setup.sh
├── docker-compose.yml
└── README.md
```
