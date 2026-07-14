#!/usr/bin/env bash
# test.sh — one-shot local test environment for LibreStreamer
#
# Sets up two backend nodes + one frontend, all on localhost, with fake
# media files.  Everything runs on non-standard ports so it won't clash
# with an existing deployment.
#
# Usage:
#   ./test.sh         # build, start, run tests, print status
#   ./test.sh stop     # stop everything
#   ./test.sh test     # run tests against a running instance
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="$ROOT_DIR/.test"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_BIN="$TEST_DIR/server"

# Ports (high to avoid conflicts)
B1_PORT=18080
B2_PORT=18081
FE_PORT=13000
B1_METRICS=19090

# Shared secret
SECRET="test-secret-$(date +%s)"
SESSION_SECRET="test-session-$(date +%s)"

# ── helpers ──────────────────────────────────────────────────────────────

red(){    printf "\033[31m%s\033[0m\n" "$*"; }
green(){  printf "\033[32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }
bold(){   printf "\033[1m%s\033[0m\n" "$*"; }
dim(){    printf "\033[2m%s\033[0m\n" "$*"; }

pass(){ green "  ✓ $1"; }
fail(){ red "  ✗ $1"; FAILS=$((FAILS+1)); }
FAILS=0

# ── stop ──────────────────────────────────────────────────────────────────

stop_all(){
  bold "Stopping test environment..."
  for pidfile in "$TEST_DIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    local pid=$(cat "$pidfile" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 0.5
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  done
  green "All stopped."
}

# ── setup ─────────────────────────────────────────────────────────────────

setup_dirs(){
  bold "Creating test directories..."
  rm -rf "$TEST_DIR"
  mkdir -p "$TEST_DIR"/{b1/data,b2/data,frontend/data,media/movies,media/tv,media/music}
  green "Test dirs: $TEST_DIR"
}

setup_media(){
  bold "Creating fake media files..."
  # Movies
  mkdir -p "$TEST_DIR/media/movies/Dune Part Two"
  echo "fake mkv content for Dune" > "$TEST_DIR/media/movies/Dune Part Two/Dune Part Two (2024).mkv"
  mkdir -p "$TEST_DIR/media/movies/The Matrix"
  echo "fake mkv content for Matrix" > "$TEST_DIR/media/movies/The Matrix/The Matrix (1999).mkv"
  # TV
  mkdir -p "$TEST_DIR/media/tv/Breaking Bad/Season 01"
  echo "fake mp4 pilot" > "$TEST_DIR/media/tv/Breaking Bad/Season 01/S01E01 - Pilot.mp4"
  mkdir -p "$TEST_DIR/media/tv/Breaking Bad/Season 01"
  echo "fake mp4 cat" > "$TEST_DIR/media/tv/Breaking Bad/Season 01/S01E02 - Cat in the Bag.mp4"
  # Music
  echo "fake mp3 data" > "$TEST_DIR/media/music/track01.mp3"
  green "5 fake media files created"
}

setup_backend1_config(){
  cat > "$TEST_DIR/b1/config.json" <<JSON
{
  "server": {
    "id": "node-1",
    "name": "Backend One",
    "host": "127.0.0.1",
    "port": $B1_PORT,
    "data_dir": "$TEST_DIR/b1/data",
    "media_paths": ["$TEST_DIR/media/movies", "$TEST_DIR/media/tv"],
    "transcoding": {"enabled": false, "hardware_accel": "none", "max_concurrent_streams": 4}
  },
  "frontend": {
    "enabled": true,
    "frontend_host": "127.0.0.1",
    "frontend_port": $FE_PORT,
    "secret": "$SECRET",
    "heartbeat_interval": 5
  },
  "monitoring": {"enabled": false, "metrics_port": 0}
}
JSON
}

setup_backend2_config(){
  cat > "$TEST_DIR/b2/config.json" <<JSON
{
  "server": {
    "id": "node-2",
    "name": "Backend Two",
    "host": "127.0.0.1",
    "port": $B2_PORT,
    "data_dir": "$TEST_DIR/b2/data",
    "media_paths": ["$TEST_DIR/media/music"],
    "transcoding": {"enabled": false, "hardware_accel": "none", "max_concurrent_streams": 2}
  },
  "frontend": {
    "enabled": true,
    "frontend_host": "127.0.0.1",
    "frontend_port": $FE_PORT,
    "secret": "$SECRET",
    "heartbeat_interval": 5
  },
  "monitoring": {"enabled": false, "metrics_port": 0}
}
JSON
}

setup_frontend_config(){
  cat > "$TEST_DIR/frontend/funnel.json" <<JSON
{
  "backends": [
    {
      "name": "Backend One",
      "host": "127.0.0.1",
      "port": $B1_PORT,
      "secret": "$SECRET",
      "type": "librestreamer",
      "priority": 1,
      "max_streams": 4,
      "enabled": true
    },
    {
      "name": "Backend Two",
      "host": "127.0.0.1",
      "port": $B2_PORT,
      "secret": "$SECRET",
      "type": "librestreamer",
      "priority": 2,
      "max_streams": 2,
      "enabled": true
    }
  ],
  "frontend": {
    "host": "127.0.0.1",
    "port": $FE_PORT,
    "session_secret": "$SESSION_SECRET",
    "auto_load_balance": true,
    "failover_timeout": 15
  }
}
JSON
}

build_backend(){
  bold "Building backend..."
  ( cd "$BACKEND_DIR" && go build -o "$BACKEND_BIN" ./cmd/server )
  green "Backend built"
}

start_backend(){
  local id="$1" config="$2" logfile="$3" pidfile="$4"
  setsid "$BACKEND_BIN" -config "$config" >"$logfile" 2>&1 </dev/null &
  echo $! > "$pidfile"
  disown 2>/dev/null || true
  sleep 1
  if kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    green "Backend $id started (pid $(cat "$pidfile"))"
  else
    red "Backend $id failed! Check $logfile"
    tail -5 "$logfile" 2>/dev/null || true
    exit 1
  fi
}

start_frontend(){
  bold "Starting frontend..."
  cd "$FRONTEND_DIR"
  setsid env LIBRESTREAMER_FUNNEL="$TEST_DIR/frontend/funnel.json" \
    LIBRESTREAMER_FRONTEND_HOST=127.0.0.1 \
    LIBRESTREAMER_FRONTEND_PORT=$FE_PORT \
    LIBRESTREAMER_DATA_DIR="$TEST_DIR/frontend/data" \
    python3 -m app.main >"$TEST_DIR/frontend.log" 2>&1 </dev/null &
  echo $! > "$TEST_DIR/frontend.pid"
  disown 2>/dev/null || true
  sleep 3
  if kill -0 "$(cat "$TEST_DIR/frontend.pid")" 2>/dev/null; then
    green "Frontend started (pid $(cat "$TEST_DIR/frontend.pid"))"
  else
    red "Frontend failed! Check $TEST_DIR/frontend.log"
    tail -10 "$TEST_DIR/frontend.log" 2>/dev/null || true
    exit 1
  fi
}

# ── test runner ───────────────────────────────────────────────────────────

run_tests(){
  bold "Running tests..."
  local C="$TEST_DIR/cookies.txt"
  local FE="http://127.0.0.1:$FE_PORT"

  # ── health ──────────────────────────────────────────────────────────────
  echo ""
  bold "Health Checks"

  local r
  r=$(curl -s --max-time 5 "$FE/healthz" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"ok"'; then pass "frontend healthz"; else fail "frontend healthz: $r"; fi

  r=$(curl -s --max-time 5 -H "X-Librestreamer-Secret: $SECRET" "http://127.0.0.1:$B1_PORT/health" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"ok"'; then pass "backend 1 health"; else fail "backend 1 health: $r"; fi

  r=$(curl -s --max-time 5 -H "X-Librestreamer-Secret: $SECRET" "http://127.0.0.1:$B2_PORT/health" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"ok"'; then pass "backend 2 health"; else fail "backend 2 health: $r"; fi

  # ── auth ────────────────────────────────────────────────────────────────
  echo ""
  bold "Authentication"

  curl -s --max-time 5 -c "$C" -d "username=admin&password=admin" -L -o /dev/null "$FE/login"
  if [ -s "$C" ]; then pass "login as admin"; else fail "login as admin"; fi

  r=$(curl -s --max-time 5 "$FE/api/library" 2>/dev/null || echo "")
  if echo "$r" | grep -q "unauthorized"; then pass "unauth API blocked"; else fail "unauth API blocked: $r"; fi

  # ── library ─────────────────────────────────────────────────────────────
  echo ""
  bold "Library"

  local items
  items=$(curl -s --max-time 5 -b "$C" "$FE/api/library" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "0")
  if [ "$items" -ge 5 ]; then pass "library has $items items (>=5 expected)"; else fail "library has $items items (expected >=5)"; fi

  items=$(curl -s --max-time 5 -b "$C" "$FE/api/library?type=movie" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "0")
  if [ "$items" -eq 2 ]; then pass "movies: $items"; else fail "movies: $items (expected 2)"; fi

  items=$(curl -s --max-time 5 -b "$C" "$FE/api/library?type=show" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "0")
  if [ "$items" -eq 1 ]; then pass "shows: $items"; else fail "shows: $items (expected 1)"; fi

  items=$(curl -s --max-time 5 -b "$C" "$FE/api/library?type=episode" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "0")
  if [ "$items" -eq 2 ]; then pass "episodes: $items"; else fail "episodes: $items (expected 2)"; fi

  items=$(curl -s --max-time 5 -b "$C" "$FE/api/library?type=music" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "0")
  if [ "$items" -eq 1 ]; then pass "music: $items"; else fail "music: $items (expected 1)"; fi

  # ── streaming ──────────────────────────────────────────────────────────
  echo ""
  bold "Streaming"

  local MID
  MID=$(curl -s --max-time 5 -b "$C" "$FE/api/library?type=movie" | python3 -c "import sys,json; d=json.load(sys.stdin); print([i for i in d['items'] if 'Dune' in i['title']][0]['id'])" 2>/dev/null || echo "")
  if [ -n "$MID" ]; then
    pass "found Dune item: $MID"

    r=$(curl -s --max-time 5 -b "$C" -o /dev/null -w "%{http_code}" "$FE/api/stream/$MID")
    if [ "$r" = "200" ]; then pass "stream: $r"; else fail "stream: $r (expected 200)"; fi

    r=$(curl -s --max-time 5 -b "$C" -o /dev/null -w "%{http_code}" -H "Range: bytes=0-4" "$FE/api/stream/$MID")
    if [ "$r" = "206" ]; then pass "range request: $r"; else fail "range request: $r (expected 206)"; fi
  else
    fail "could not find Dune item for streaming test"
  fi

  # ── pages ──────────────────────────────────────────────────────────────
  echo ""
  bold "Web Pages"

  for path in "/" "/library" "/library?type=movie" "/search?q=Dune"; do
    r=$(curl -s --max-time 5 -b "$C" -o /dev/null -w "%{http_code}" "$FE$path")
    if [ "$r" = "200" ]; then pass "GET $path"; else fail "GET $path: $r"; fi
  done

  if [ -n "$MID" ]; then
    r=$(curl -s --max-time 5 -b "$C" -o /dev/null -w "%{http_code}" "$FE/item/$MID")
    if [ "$r" = "200" ]; then pass "GET /item/{id}"; else fail "GET /item/{id}: $r"; fi

    r=$(curl -s --max-time 5 -b "$C" -o /dev/null -w "%{http_code}" "$FE/watch/$MID")
    if [ "$r" = "200" ]; then pass "GET /watch/{id}"; else fail "GET /watch/{id}: $r"; fi
  fi

  # ── admin panel ────────────────────────────────────────────────────────
  echo ""
  bold "Admin Panel"

  for path in "/admin" "/admin/servers" "/admin/upload" "/admin/libraries" "/admin/users" "/admin/logs"; do
    r=$(curl -s --max-time 5 -b "$C" -o /dev/null -w "%{http_code}" "$FE$path")
    if [ "$r" = "200" ]; then pass "GET $path"; else fail "GET $path: $r"; fi
  done

  # ── admin stats ────────────────────────────────────────────────────────
  echo ""
  bold "Admin Stats API"

  r=$(curl -s --max-time 5 -b "$C" "$FE/api/admin/stats" 2>/dev/null || echo "{}")
  echo "$r" | python3 -c "
import sys,json
d=json.load(sys.stdin)
backends=d.get('backends',[])
tc=d.get('type_counts',{})
bc=d.get('backend_counts',{})
print(f'  backends: {len(backends)}')
print(f'  type_counts: {tc}')
print(f'  backend_counts: {bc}')
print(f'  total_items: {d.get(\"total_items\",0)}')
print(f'  auto_load_balance: {d.get(\"auto_load_balance\",False)}')
for b in backends:
    print(f'  {b[\"name\"]}: enabled={b.get(\"enabled\",0)} healthy={b.get(\"healthy\",False)} cpu={b.get(\"cpu_usage_pct\",-1):.0f}% gpu={b.get(\"gpu_name\",\"\")}')
" 2>/dev/null || fail "admin stats parse"

  local bcount
  bcount=$(echo "$r" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('backends',[])))" 2>/dev/null || echo "0")
  if [ "$bcount" -eq 2 ]; then pass "stats shows 2 backends"; else fail "stats shows $bcount backends (expected 2)"; fi

  # ── admin: toggle backend ──────────────────────────────────────────────
  echo ""
  bold "Admin: Toggle Backend"

  r=$(curl -s --max-time 5 -b "$C" -X POST "$FE/api/admin/backends/Backend%20Two/toggle" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"enabled":false'; then pass "disable Backend Two"; else fail "toggle disable: $r"; fi

  r=$(curl -s --max-time 5 -b "$C" -X POST "$FE/api/admin/backends/Backend%20Two/toggle" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"enabled":true'; then pass "re-enable Backend Two"; else fail "toggle enable: $r"; fi

  # ── admin: edit backend ────────────────────────────────────────────────
  echo ""
  bold "Admin: Edit Backend"

  r=$(curl -s --max-time 5 -b "$C" -X POST -H "Content-Type: application/json" \
    -d '{"priority": 5, "max_streams": 10}' \
    "$FE/api/admin/backends/Backend%20One/edit" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"ok"'; then pass "edit Backend One"; else fail "edit: $r"; fi

  r=$(curl -s --max-time 5 -b "$C" "$FE/api/admin/stats" 2>/dev/null || echo "{}")
  local pri
  pri=$(echo "$r" | python3 -c "import sys,json; d=json.load(sys.stdin); print([b for b in d['backends'] if b['name']=='Backend One'][0]['priority'])" 2>/dev/null || echo "0")
  if [ "$pri" = "5" ]; then pass "priority updated to $pri"; else fail "priority: $pri (expected 5)"; fi

  # ── admin: rescan ──────────────────────────────────────────────────────
  echo ""
  bold "Admin: Rescan"

  r=$(curl -s --max-time 5 -b "$C" -X POST "$FE/api/admin/backends/Backend%20One/rescan" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"ok"'; then pass "rescan Backend One"; else fail "rescan: $r"; fi

  r=$(curl -s --max-time 5 -b "$C" -X POST "$FE/api/admin/backends/Nonexistent/rescan" -o /dev/null -w "%{http_code}" 2>/dev/null || echo "")
  if [ "$r" = "404" ]; then pass "rescan nonexistent -> 404"; else fail "rescan nonexistent: should be 404, got: $r"; fi

  # ── admin: balancer toggle ────────────────────────────────────────────
  echo ""
  bold "Admin: Balancer Toggle"

  r=$(curl -s --max-time 5 -b "$C" -X POST "$FE/api/admin/balancer/toggle" 2>/dev/null || echo "")
  if echo "$r" | grep -q 'false'; then pass "auto_lb off"; else fail "auto_lb off: $r"; fi

  r=$(curl -s --max-time 5 -b "$C" -X POST "$FE/api/admin/balancer/toggle" 2>/dev/null || echo "")
  if echo "$r" | grep -q 'true'; then pass "auto_lb on"; else fail "auto_lb on: $r"; fi

  # ── admin: metrics history ────────────────────────────────────────────
  echo ""
  bold "Admin: Metrics History"

  r=$(curl -s --max-time 5 -b "$C" "$FE/api/admin/metrics/Backend%20One?limit=5" 2>/dev/null || echo '{"points":[]}')
  local pts
  pts=$(echo "$r" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('points',[])))" 2>/dev/null || echo "0")
  if [ "$pts" -ge 1 ]; then pass "metrics history: $pts points"; else fail "metrics history: $pts points (expected >=1)"; fi

  # ── admin: add/remove backend ──────────────────────────────────────────
  echo ""
  bold "Admin: Add/Remove Backend"

  r=$(curl -s --max-time 5 -b "$C" -X POST -H "Content-Type: application/json" \
    -d '{"name":"Temp Backend","type":"librestreamer","host":"127.0.0.1","port":9999,"secret":"temp","priority":3,"max_streams":2}' \
    "$FE/api/admin/backends/add" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"ok"'; then pass "add temp backend"; else fail "add: $r"; fi

  r=$(curl -s --max-time 5 -b "$C" "$FE/api/admin/stats" 2>/dev/null || echo "{}")
  bcount=$(echo "$r" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('backends',[])))" 2>/dev/null || echo "0")
  if [ "$bcount" -eq 3 ]; then pass "3 backends after add"; else fail "backends: $bcount (expected 3)"; fi

  r=$(curl -s --max-time 5 -b "$C" -X POST "$FE/api/admin/backends/Temp%20Backend/remove" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"ok"'; then pass "remove temp backend"; else fail "remove: $r"; fi

  r=$(curl -s --max-time 5 -b "$C" "$FE/api/admin/stats" 2>/dev/null || echo "{}")
  bcount=$(echo "$r" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('backends',[])))" 2>/dev/null || echo "0")
  if [ "$bcount" -eq 2 ]; then pass "2 backends after remove"; else fail "backends: $bcount (expected 2)"; fi

  # ── jellyfin emulation ────────────────────────────────────────────────
  echo ""
  bold "Jellyfin API Emulation"

  r=$(curl -s --max-time 5 "$FE/System/Info" 2>/dev/null || echo "")
  if echo "$r" | grep -q 'LibreStreamer'; then pass "System/Info"; else fail "System/Info: $r"; fi

  r=$(curl -s --max-time 5 "$FE/Users" 2>/dev/null || echo "[]")
  if echo "$r" | grep -q 'admin'; then pass "Users"; else fail "Users: $r"; fi

  r=$(curl -s --max-time 5 "$FE/Users/me/Items" 2>/dev/null || echo "{}")
  if echo "$r" | grep -q 'Items'; then pass "Users/me/Items"; else fail "Users/me/Items: $r"; fi

  # ── search ─────────────────────────────────────────────────────────────
  echo ""
  bold "Search"

  r=$(curl -s --max-time 5 -b "$C" "$FE/api/search?q=Dune" 2>/dev/null || echo '{"count":0}')
  local sc
  sc=$(echo "$r" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "0")
  if [ "$sc" -ge 1 ]; then pass "search 'Dune': $sc results"; else fail "search 'Dune': $sc"; fi

  r=$(curl -s --max-time 5 -b "$C" "$FE/api/search?q=Matrix" 2>/dev/null || echo '{"count":0}')
  sc=$(echo "$r" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "0")
  if [ "$sc" -ge 1 ]; then pass "search 'Matrix': $sc results"; else fail "search 'Matrix': $sc"; fi

  # ── watch history ──────────────────────────────────────────────────────
  echo ""
  bold "Watch History"

  if [ -n "$MID" ]; then
    r=$(curl -s --max-time 5 -b "$C" -X POST -H "Content-Type: application/json" \
      -d "{\"item_id\":\"$MID\",\"position\":45,\"duration\":120}" \
      "$FE/api/progress" 2>/dev/null || echo "")
    if echo "$r" | grep -q '"ok"'; then pass "report progress"; else fail "report progress: $r"; fi

    r=$(curl -s --max-time 5 -b "$C" "$FE/api/history" 2>/dev/null || echo '{"items":[]}')
    local hc
    hc=$(echo "$r" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('items',[])))" 2>/dev/null || echo "0")
    if [ "$hc" -ge 1 ]; then pass "history has $hc entries"; else fail "history: $hc (expected >=1)"; fi
  fi

  # ── admin: users ───────────────────────────────────────────────────────
  echo ""
  bold "Admin: Users"

  r=$(curl -s --max-time 5 -b "$C" -X POST -d "username=testuser&password=testpass" "$FE/admin/users/add" -o /dev/null -w "%{http_code}")
  if [ "$r" = "302" ]; then pass "add user (redirect)"; else fail "add user: $r"; fi

  r=$(curl -s --max-time 5 -b "$C" -X POST "$FE/admin/users/testuser/delete" -o /dev/null -w "%{http_code}")
  if [ "$r" = "302" ]; then pass "delete user (redirect)"; else fail "delete user: $r"; fi

  # ── i18n ───────────────────────────────────────────────────────────────
  echo ""
  bold "Internationalization"

  # Set language to Spanish
  r=$(curl -s --max-time 5 -b "$C" -X POST -H "Content-Type: application/json" \
    -d '{"lang":"es"}' "$FE/api/language" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"lang":"es"'; then pass "set language to es"; else fail "set language: $r"; fi

  # Check Spanish content on home page
  r=$(curl -s --max-time 5 -b "$C" "$FE/" 2>/dev/null || echo "")
  if echo "$r" | grep -q 'Inicio'; then pass "home page in Spanish"; else fail "home page not in Spanish"; fi

  # Check Spanish admin page
  r=$(curl -s --max-time 5 -b "$C" "$FE/admin" 2>/dev/null || echo "")
  if echo "$r" | grep -q 'Servidores'; then pass "admin page in Spanish"; else fail "admin page not in Spanish"; fi

  # Switch to French
  r=$(curl -s --max-time 5 -b "$C" -X POST -H "Content-Type: application/json" \
    -d '{"lang":"fr"}' "$FE/api/language" 2>/dev/null || echo "")
  if echo "$r" | grep -q '"lang":"fr"'; then pass "set language to fr"; else fail "set language fr: $r"; fi

  r=$(curl -s --max-time 5 -b "$C" "$FE/" 2>/dev/null || echo "")
  if echo "$r" | grep -q 'Accueil'; then pass "home page in French"; else fail "home page not in French"; fi

  # Switch to German
  r=$(curl -s --max-time 5 -b "$C" -X POST -H "Content-Type: application/json" \
    -d '{"lang":"de"}' "$FE/api/language" 2>/dev/null || echo "")
  r=$(curl -s --max-time 5 -b "$C" "$FE/" 2>/dev/null || echo "")
  if echo "$r" | grep -q 'Startseite'; then pass "home page in German"; else fail "home page not in German"; fi

  # Reset to English
  curl -s --max-time 5 -b "$C" -X POST -H "Content-Type: application/json" \
    -d '{"lang":"en"}' "$FE/api/language" >/dev/null 2>&1
  r=$(curl -s --max-time 5 -b "$C" "$FE/" 2>/dev/null || echo "")
  if echo "$r" | grep -q 'Home'; then pass "home page back in English"; else fail "home page not back in English"; fi

  # ── summary ───────────────────────────────────────────────────────────
  echo ""
  if [ "$FAILS" -eq 0 ]; then
    green "============================================"
    green "  ALL TESTS PASSED"
    green "============================================"
  else
    red "============================================"
    red "  $FAILS TEST(S) FAILED"
    red "============================================"
  fi
}

# ── status ─────────────────────────────────────────────────────────────────

show_status(){
  echo ""
  bold "Test Environment Status"
  echo ""
  echo "  Frontend:  http://127.0.0.1:$FE_PORT  (admin / admin)"
  echo "  Backend 1: http://127.0.0.1:$B1_PORT  (Backend One — movies + TV)"
  echo "  Backend 2: http://127.0.0.1:$B2_PORT  (Backend Two — music)"
  echo "  Media:     $TEST_DIR/media/{movies,tv,music}"
  echo "  Logs:      $TEST_DIR/backend1.log  $TEST_DIR/backend2.log  $TEST_DIR/frontend.log"
  echo "  Stop:      ./test.sh stop"
  echo ""
  dim "  Open http://127.0.0.1:$FE_PORT in your browser to test the UI."
  echo ""
}

# ── main ───────────────────────────────────────────────────────────────────

ACTION="${1:-all}"

case "$ACTION" in
  stop)
    stop_all
    ;;
  test)
    run_tests
    ;;
  all|"")
    bold "LibreStreamer Local Test Environment"
    echo ""

    # Check deps
    command -v go >/dev/null 2>&1 || { red "go not installed"; exit 1; }
    command -v python3 >/dev/null 2>&1 || { red "python3 not installed"; exit 1; }

    # Stop any previous instance
    stop_all 2>/dev/null || true

    # Setup
    setup_dirs
    setup_media
    setup_backend1_config
    setup_backend2_config
    setup_frontend_config
    echo ""

    # Install frontend deps if needed
    bold "Checking frontend dependencies..."
    if ! python3 -c "import fastapi" 2>/dev/null; then
      pip3 install -q --break-system-packages -r "$FRONTEND_DIR/requirements.txt" 2>/dev/null \
        || pip3 install -q -r "$FRONTEND_DIR/requirements.txt"
      green "Dependencies installed"
    else
      green "Dependencies OK"
    fi
    echo ""

    # Build
    build_backend
    echo ""

    # Start backends
    bold "Starting backends..."
    start_backend "1" "$TEST_DIR/b1/config.json" "$TEST_DIR/backend1.log" "$TEST_DIR/b1.pid"
    start_backend "2" "$TEST_DIR/b2/config.json" "$TEST_DIR/backend2.log" "$TEST_DIR/b2.pid"
    echo ""

    # Start frontend
    start_frontend
    echo ""

    # Wait for backends to register
    bold "Waiting for backends to register..."
    sleep 6
    green "Ready"
    echo ""

    # Run tests
    run_tests
    show_status
    ;;
  *)
    red "Unknown: $ACTION"
    echo "Usage: ./test.sh [stop|test]"
    echo "       (no arg = full setup + test)"
    exit 1
    ;;
esac
