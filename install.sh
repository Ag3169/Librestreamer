#!/usr/bin/env bash
# install.sh — LibreStreamer auto-installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Ag3169/Librestreamer/main/install.sh | bash
#
# Downloads, builds, and starts LibreStreamer. After running, open the
# printed URL in your browser to complete the setup wizard.
set -euo pipefail

red(){    printf "\033[31m%s\033[0m\n" "$*"; }
green(){  printf "\033[32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }
bold(){   printf "\033[1m%s\033[0m\n" "$*"; }

INSTALL_DIR="${1:-$HOME/librestreamer}"
PORT="${LIBRESTREAMER_PORT:-3000}"

bold "LibreStreamer Auto-Installer"
echo ""

# ── check dependencies ────────────────────────────────────────────────────
bold "Checking dependencies..."

MISSING=""
if ! command -v go >/dev/null 2>&1; then
  MISSING="$MISSING go"
fi
if ! command -v python3 >/dev/null 2>&1; then
  MISSING="$MISSING python3"
fi
if ! command -v git >/dev/null 2>&1; then
  MISSING="$MISSING git"
fi

if [ -n "$MISSING" ]; then
  red "Missing dependencies:$MISSING"
  echo ""
  echo "Install them first:"
  echo "  Ubuntu/Debian:  sudo apt install golang python3 git"
  echo "  Fedora:         sudo dnf install golang python3 git"
  echo "  Arch:           sudo pacman -S go python git"
  echo "  macOS:          brew install go python3 git"
  exit 1
fi

green "Dependencies OK"
echo ""

# ── clone or update ───────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
  bold "Updating existing installation at $INSTALL_DIR..."
  cd "$INSTALL_DIR"
  git pull --ff-only
else
  bold "Cloning LibreStreamer to $INSTALL_DIR..."
  git clone https://github.com/Ag3169/Librestreamer.git "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi
echo ""

# ── build backend ─────────────────────────────────────────────────────────
bold "Building Go backend..."
( cd backend && go build -o librestreamer-server ./cmd/server )
green "Backend built"
echo ""

# ── install frontend deps ─────────────────────────────────────────────────
bold "Installing frontend dependencies..."
cd frontend
if ! python3 -c "import fastapi" 2>/dev/null; then
  pip3 install -q --break-system-packages -r requirements.txt 2>/dev/null \
    || pip3 install -q -r requirements.txt
fi
green "Frontend dependencies OK"
echo ""

# ── create config ─────────────────────────────────────────────────────────
bold "Creating configuration..."
DATA_DIR="$INSTALL_DIR/data"
mkdir -p "$DATA_DIR"

# Create minimal funnel.json (backends added via setup wizard)
cat > "$DATA_DIR/funnel.json" <<JSON
{
  "backends": [],
  "frontend": {
    "host": "0.0.0.0",
    "port": $PORT,
    "session_secret": "$(python3 -c 'import secrets; print(secrets.token_hex(32))')",
    "auto_load_balance": true,
    "failover_timeout": 10
  }
}
JSON

green "Config created at $DATA_DIR/funnel.json"
echo ""

# ── stop previous instance ────────────────────────────────────────────────
bold "Stopping any previous instance..."
for pidfile in "$DATA_DIR"/*.pid "$INSTALL_DIR/frontend.pid"; do
  [ -f "$pidfile" ] || continue
  pid=$(cat "$pidfile" 2>/dev/null || true)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
done
green "Done"
echo ""

# ── start frontend ────────────────────────────────────────────────────────
bold "Starting LibreStreamer frontend..."
cd frontend
export LIBRESTREAMER_FUNNEL="$DATA_DIR/funnel.json"
export LIBRESTREAMER_DATA_DIR="$DATA_DIR"
export LIBRESTREAMER_FRONTEND_HOST=0.0.0.0
export LIBRESTREAMER_FRONTEND_PORT=$PORT

setsid python3 -m app.main >"$DATA_DIR/frontend.log" 2>&1 </dev/null &
echo $! > "$INSTALL_DIR/frontend.pid"
sleep 3

if kill -0 "$(cat "$INSTALL_DIR/frontend.pid")" 2>/dev/null; then
  green "Frontend started (pid $(cat "$INSTALL_DIR/frontend.pid"))"
else
  red "Frontend failed to start! Check $DATA_DIR/frontend.log"
  tail -20 "$DATA_DIR/frontend.log" 2>/dev/null || true
  exit 1
fi

echo ""
green "============================================"
green "  LibreStreamer is running!"
green "============================================"
echo ""
bold "  Open this URL in your browser to complete setup:"
echo ""
  yellow "    http://localhost:$PORT"
echo ""
echo "  The setup wizard will guide you through:"
echo "    1. Creating your admin account"
echo "    2. Configuring your media backend"
echo "    3. Setting up your media libraries"
echo ""
echo "  Data directory: $DATA_DIR"
echo "  Logs:           $DATA_DIR/frontend.log"
echo "  Stop:           kill \$(cat $INSTALL_DIR/frontend.pid)"
echo ""
