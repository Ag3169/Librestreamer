#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_BIN="$BACKEND_DIR/librestreamer-server"
BACKEND_PID="$ROOT_DIR/backend.pid"
FRONTEND_PID="$ROOT_DIR/frontend.pid"

red(){    printf "\033[31m%s\033[0m\n" "$*"; }
green(){  printf "\033[32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }
bold(){   printf "\033[1m%s\033[0m\n" "$*"; }
dim(){    printf "\033[2m%s\033[0m\n" "$*"; }

require(){ command -v "$1" >/dev/null 2>&1 || { red "ERROR: '$1' not installed."; exit 1; }; }
is_running(){ [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
stop_pid(){ local p="$1" l="$2"; if is_running "$p"; then local pid=$(cat "$p"); yellow "Stopping $l (pid $pid)..."; kill "$pid" 2>/dev/null || true; sleep 1; kill -9 "$pid" 2>/dev/null || true; rm -f "$p"; green "$l stopped."; else rm -f "$p"; fi; }

# ---------------------------------------------------------------------------
# Config writers
# ---------------------------------------------------------------------------

write_backend_config(){
  local id="$1" name="$2" host="$3" port="$4" data_dir="$5" media_dir="$6"
  local transcode="$7" hwaccel="$8" max_streams="$9"
  local fe_enable="${10}" fe_host="${11}" fe_port="${12}" fe_secret="${13}"

  cat > "$BACKEND_DIR/config.json" <<JSON
{
  "server": {
    "id": "$id",
    "name": "$name",
    "host": "$host",
    "port": $port,
    "data_dir": "$data_dir",
    "media_paths": ["$media_dir/movies", "$media_dir/tv", "$media_dir/music"],
    "transcoding": {
      "enabled": $transcode,
      "hardware_accel": "$hwaccel",
      "max_concurrent_streams": $max_streams
    }
  },
  "frontend": {
    "enabled": $fe_enable,
    "frontend_host": "$fe_host",
    "frontend_port": $fe_port,
    "secret": "$fe_secret",
    "heartbeat_interval": 30
  },
  "monitoring": {
    "enabled": true,
    "metrics_port": 9090
  }
}
JSON
}

write_frontend_funnel(){
  local backend_name="$1" backend_host="$2" backend_port="$3" backend_secret="$4"
  local fe_host="$5" fe_port="$6" session_secret="$7"

  cat > "$FRONTEND_DIR/funnel.json" <<JSON
{
  "backends": [
    {
      "name": "$backend_name",
      "host": "$backend_host",
      "port": $backend_port,
      "secret": "$backend_secret",
      "type": "librestreamer",
      "priority": 1,
      "max_streams": 4,
      "enabled": true
    }
  ],
  "frontend": {
    "host": "$fe_host",
    "port": $fe_port,
    "session_secret": "$session_secret",
    "auto_load_balance": true,
    "failover_timeout": 10
  }
}
JSON
}

# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------

setup_media(){
  bold "Setting up media folders..."
  local mp="$BACKEND_DIR/media"
  mkdir -p "$mp/movies" "$mp/tv" "$mp/music"
  green "Media folders ready: $mp/{movies,tv,music}"
  dim "  Drop your media files into these folders, or edit config.json to point elsewhere."
}

setup_backend(){
  bold "Building backend..."
  require go
  ( cd "$BACKEND_DIR" && go build -o "$BACKEND_BIN" ./cmd/server )
  green "Backend built: $BACKEND_BIN"
}

start_backend(){
  if is_running "$BACKEND_PID"; then yellow "Backend already running."; return; fi
  bold "Starting backend..."
  cd "$BACKEND_DIR"
  setsid "$BACKEND_BIN" -config config.json >"$ROOT_DIR/backend.log" 2>&1 </dev/null &
  echo $! > "$BACKEND_PID"; disown 2>/dev/null || true; sleep 1
  if is_running "$BACKEND_PID"; then green "Backend started (pid $(cat "$BACKEND_PID"))"; else red "Backend failed. Check backend.log"; exit 1; fi
}

setup_frontend(){
  bold "Setting up frontend..."
  require python3; require pip3
  echo "Installing Python dependencies..."
  pip3 install -q --break-system-packages -r "$FRONTEND_DIR/requirements.txt" 2>/dev/null || pip3 install -q -r "$FRONTEND_DIR/requirements.txt"
  green "Frontend dependencies installed."
}

start_frontend(){
  if is_running "$FRONTEND_PID"; then yellow "Frontend already running."; return; fi
  bold "Starting frontend..."
  cd "$FRONTEND_DIR"
  setsid python3 -m app.main --funnel funnel.json >"$ROOT_DIR/frontend.log" 2>&1 </dev/null &
  echo $! > "$FRONTEND_PID"; disown 2>/dev/null || true; sleep 2
  if is_running "$FRONTEND_PID"; then green "Frontend started (pid $(cat "$FRONTEND_PID"))"; else red "Frontend failed. Check frontend.log"; exit 1; fi
}

stop_all(){ stop_pid "$FRONTEND_PID" "frontend"; stop_pid "$BACKEND_PID" "backend"; }

# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

prompt(){ # var prompt default
  local var="$1" text="$2" default="$3"
  read -rp "$(yellow "$text") [$default]: " val
  eval "$var=\"\${val:-$default}\""
}

prompt_yesno(){ # var prompt default
  local var="$1" text="$2" default="$3"
  local hint="y/N"; [ "$default" = "y" ] && hint="Y/n"
  read -rp "$(yellow "$text") ($hint): " val
  val="${val:-$default}"
  case "$val" in y|Y|yes|YES) eval "$var=y";; *) eval "$var=n";; esac
}

gen_secret(){ head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32; echo; }

# ---------------------------------------------------------------------------
# Deployment modes
# ---------------------------------------------------------------------------

deploy_single(){
  bold "=== Single Server Deployment ==="
  dim "  Backend (Go media server) + Frontend (FastAPI web UI) on this machine."
  echo ""

  prompt BNAME  "  Server name"          "Media Server"
  prompt BPORT  "  Backend port"          "8080"
  prompt FEPORT "  Web UI port"           "3000"
  prompt_yesno TRANSCODE "  Enable transcoding (needs ffmpeg)" "n"

  HWACCEL="none"
  if [ "$TRANSCODE" = "y" ]; then
    echo "  Available hardware accelerators:"
    dim "    none     - Software only (libx264, works on any CPU)"
    dim "    nvenc    - NVIDIA GPU"
    dim "    vaapi    - Intel/AMD GPU (Linux)"
    dim "    qsv      - Intel QuickSync"
    dim "    amf      - AMD AMF"
    prompt HWACCEL "  Hardware accel" "none"
  fi

  prompt MEDIA_DIR "  Media directory" "$BACKEND_DIR/media"
  SECRET=$(gen_secret)
  SESSION_SECRET=$(gen_secret)

  echo ""
  bold "Configuring..."

  write_backend_config \
    "node-1" "$BNAME" "0.0.0.0" "$BPORT" \
    "$BACKEND_DIR/data" "$MEDIA_DIR" \
    "$TRANSCODE" "$HWACCEL" 4 \
    "true" "127.0.0.1" "$FEPORT" "$SECRET"

  write_frontend_funnel \
    "$BNAME" "127.0.0.1" "$BPORT" "$SECRET" \
    "0.0.0.0" "$FEPORT" "$SESSION_SECRET"

  setup_media
  echo ""
  setup_backend
  echo ""
  setup_frontend
  echo ""
  start_backend
  echo ""
  start_frontend

  echo ""
  bold "=== Setup Complete ==="
  echo ""
  echo "  Web UI:   http://localhost:$FEPORT"
  echo "  Login:    admin / admin"
  echo "  Backend:  http://localhost:$BPORT"
  echo "  Media:    $MEDIA_DIR/{movies,tv,music}"
  echo "  Logs:     backend.log  frontend.log"
  echo "  Stop:     ./setup.sh stop"
  echo ""
  dim "  Drop media into $MEDIA_DIR/{movies,tv,music} and it will appear in the UI."
}

deploy_backend_only(){
  bold "=== Backend Node Setup ==="
  dim "  This machine will store media and stream it."
  dim "  It will register with a frontend running on another machine."
  echo ""

  prompt BNAME  "  Server name"          "Media Server"
  prompt BPORT  "  Backend port"          "8080"
  prompt FEHOST "  Frontend host (IP)"    "127.0.0.1"
  prompt FEPORT "  Frontend port"         "3000"
  prompt_yesno TRANSCODE "  Enable transcoding (needs ffmpeg)" "n"

  HWACCEL="none"
  if [ "$TRANSCODE" = "y" ]; then
    echo "  Available hardware accelerators:"
    dim "    none     - Software only (libx264, works on any CPU)"
    dim "    nvenc    - NVIDIA GPU"
    dim "    vaapi    - Intel/AMD GPU (Linux)"
    dim "    qsv      - Intel QuickSync"
    dim "    amf      - AMD AMF"
    prompt HWACCEL "  Hardware accel" "none"
  fi

  prompt MEDIA_DIR "  Media directory" "$BACKEND_DIR/media"
  prompt_yesno GENSECRET "  Auto-generate frontend secret" "y"
  if [ "$GENSECRET" = "y" ]; then
    SECRET=$(gen_secret)
  else
    prompt SECRET "  Frontend secret (must match frontend)" "change-me"
  fi

  echo ""
  bold "Configuring..."

  write_backend_config \
    "node-$(date +%s)" "$BNAME" "0.0.0.0" "$BPORT" \
    "$BACKEND_DIR/data" "$MEDIA_DIR" \
    "$TRANSCODE" "$HWACCEL" 4 \
    "true" "$FEHOST" "$FEPORT" "$SECRET"

  setup_media
  echo ""
  setup_backend
  echo ""
  start_backend

  echo ""
  bold "=== Backend Node Ready ==="
  echo ""
  echo "  Backend:  http://$(hostname -I 2>/dev/null | awk '{print $1:-0.0.0.0}'):$BPORT"
  echo "  Frontend: $FEHOST:$FEPORT (will auto-register via heartbeat)"
  echo "  Media:   $MEDIA_DIR/{movies,tv,music}"
  echo "  Secret:  $SECRET"
  echo "  Log:     backend.log"
  echo "  Stop:    ./setup.sh stop"
  echo ""
  dim "  Make sure the frontend is running and its secret matches."
  dim "  The backend will auto-register with the frontend on startup."
}

deploy_frontend_only(){
  bold "=== Frontend (Web UI) Setup ==="
  dim "  This machine will serve the web UI and aggregate backend nodes."
  dim "  Backend nodes will auto-register with this frontend."
  echo ""

  prompt FEPORT "  Web UI port"           "3000"
  SESSION_SECRET=$(gen_secret)
  SECRET=$(gen_secret)
  echo ""

  bold "Configuring..."

  cat > "$FRONTEND_DIR/funnel.json" <<JSON
{
  "backends": [],
  "frontend": {
    "host": "0.0.0.0",
    "port": $FEPORT,
    "session_secret": "$SESSION_SECRET",
    "auto_load_balance": true,
    "failover_timeout": 10
  }
}
JSON

  echo ""
  bold "Registration Secret (use this when setting up backend nodes):"
  yellow "  $SECRET"
  echo ""
  dim "  When you run ./setup.sh on a backend node, enter this same secret."
  dim "  Or add backends manually via the Admin panel."
  echo ""

  setup_frontend
  echo ""
  start_frontend

  echo ""
  bold "=== Frontend Ready ==="
  echo ""
  echo "  Web UI:   http://$(hostname -I 2>/dev/null | awk '{print $1:-0.0.0.0}'):$FEPORT"
  echo "  Login:    admin / admin"
  echo "  Log:      frontend.log"
  echo "  Stop:     ./setup.sh stop"
  echo ""
  dim "  Run ./setup.sh on backend nodes and point them at this frontend."
  dim "  They will appear in the admin panel automatically."
}

# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

show_menu(){
  bold "LibreStreamer — Jellyfin Alternative"
  echo ""
  echo "  Choose deployment mode:"
  echo ""
  echo "    1) Single Server   — Backend + web UI on one machine (recommended)"
  echo "       Everything runs here. Simplest setup, like a normal media server."
  echo ""
  echo "    2) Multi Server    — Separate frontend and backend nodes"
  echo "       Run backend nodes on multiple machines, frontend on one."
  echo ""
  echo "    3) Backend Only    — Just the media server (connects to external frontend)"
  echo "       For adding a media node to an existing frontend."
  echo ""
  echo "    4) Frontend Only   — Just the web UI / aggregator (connects to backends)"
  echo "       For setting up the frontend separately from media nodes."
  echo ""
  echo "    s) Stop            — Stop all running services"
  echo "    q) Quit"
  echo ""
  read -rp "$(yellow "Select [1-4/s/q]: ")" choice
}

ACTION="${1:-}"

if [ -n "$ACTION" ]; then
  case "$ACTION" in
    single)   deploy_single ;;
    backend)  deploy_backend_only ;;
    frontend) deploy_frontend_only ;;
    stop)     stop_all; green "All services stopped." ;;
    build)
      setup_backend
      setup_frontend
      green "Build complete."
      ;;
    *)
      red "Unknown: $ACTION"
      echo "Usage: ./setup.sh [single|backend|frontend|stop|build]"
      echo "       (no arg = interactive menu)"
      exit 1
      ;;
  esac
  exit 0
fi

show_menu
case "${choice:-}" in
  1|single)  deploy_single ;;
  2|multi)
    bold "Multi Server: choose what to set up on this machine:"
    echo ""
    echo "    a) Backend node (stores media, streams to frontend)"
    echo "    b) Frontend node  (web UI, aggregates backends)"
    echo ""
    read -rp "$(yellow "Select [a/b]: ")" sub
    case "$sub" in
      a|A) deploy_backend_only ;;
      b|B) deploy_frontend_only ;;
      *)   red "Invalid choice."; exit 1 ;;
    esac
    ;;
  3|backend)  deploy_backend_only ;;
  4|frontend) deploy_frontend_only ;;
  s|S|stop)   stop_all; green "All services stopped." ;;
  q|Q|quit)   exit 0 ;;
  *)          red "Invalid choice."; exit 1 ;;
esac
