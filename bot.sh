#!/usr/bin/env bash
# Bot control script: init / run / start / stop / restart / update.
set -uo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
DB=${DB:-${DB_PATH:-archive.db}}
DUMP=${DUMP:-haramain}
PIDFILE=${PIDFILE:-$PWD/bot.pid}
LOGFILE=${LOGFILE:-$PWD/bot.log}
BOOT_WAIT=10

pid_live() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
running_pid() {
  [ -f "$PIDFILE" ] || return 1
  local pid
  pid=$(cat "$PIDFILE" 2>/dev/null)
  pid_live "$pid" && { echo "$pid"; return 0; }
  rm -f "$PIDFILE"
  return 1
}

init_ready() {
  if [ "${BOOTSTRAP:-1}" != "0" ] && [ ! -f "$DB" ]; then
    echo "[init] $DB missing -> harvesting"
    ( cd pipeline && bash run.sh sitemap ) || exit 1
    ( cd pipeline && bash run.sh feed ) || exit 1
    if [ "${SWEEP:-0}" = "1" ]; then
      ( cd pipeline && bash run.sh sweep ) || exit 1
    fi
    ( cd pipeline && bash run.sh build ) || exit 1
    "$PY" archive/import_dump.py --dump "$DUMP" --db "$DB" || exit 1
  else
    [ -f "$DB" ] && echo "[init] skip ($DB already exists)" \
                  || echo "[init] skip (BOOTSTRAP=0, $DB not rebuilt)"
  fi
}

cmd_update() {
  "$PY" -m bot.update
}

cmd_run() {
  ensure_deps || exit 1
  init_ready
  if running_pid >/dev/null; then
    echo "[run] a background bot is running (pid $(running_pid)) — use 'stop' first" >&2
    exit 1
  fi
  echo "[run] start (foreground)"
  exec "$PY" bot.py
}
cmd_start() {
  if pid=$(running_pid); then echo "[start] already running (pid $pid)"; return 0; fi
  ensure_deps || exit 1
  init_ready
  echo "[start] launching in background -> $LOGFILE (pidfile $PIDFILE)"
  nohup "$PY" bot.py >>"$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 2
  if pid=$(running_pid); then echo "[start] up (pid $pid)"; else
    echo "[start] died within 2s — see $LOGFILE" >&2
    tail -n 5 "$LOGFILE" >&2 || true
    rm -f "$PIDFILE"
    exit 1
  fi
}
cmd_stop() {
  if ! pid=$(running_pid); then echo "[stop] not running"; return 0; fi
  echo "[stop] sending TERM to pid $pid"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 "$BOOT_WAIT"); do pid_live "$pid" || break; sleep 1; done
  if pid_live "$pid"; then kill -9 "$pid" 2>/dev/null || true; sleep 1; fi
  rm -f "$PIDFILE"
  if pid_live "$pid"; then echo "[stop] pid $pid refused to die" >&2; exit 1; fi
  echo "[stop] stopped"
}

# --- dependencies -------------------------------------------------------------
DEPS="requests kurigram:pyrogram,pyrofork tgcrypto"
CORE_DEPS="requests"
EXTRA_DEPS="kurigram tgcrypto"
probe_deps() {
  "$PY" - "$@" <<'PY'
import importlib.metadata as md
import sys
missing = []
for spec in sys.argv[1:]:
    dist, _, alias = spec.partition(":")
    names = [dist] + [a for a in alias.split(",") if a]
    for name in names:
        try:
            md.version(name)
            break
        except Exception:
            continue
    else:
        missing.append(dist)
print(" ".join(missing))
PY
}
ensure_deps() {
  if [ "${INSTALL_DEPS:-1}" = "0" ]; then echo "[deps] skipping check"; return 0; fi
  local missing core_missing="" extra_missing="" m
  missing=$(probe_deps $DEPS 2>/dev/null) || missing=""
  for m in $missing; do
    case " $CORE_DEPS " in *" $m "*) core_missing="$core_missing $m" ;; esac
    case " $EXTRA_DEPS " in *" $m "*) extra_missing="$extra_missing $m" ;; esac
  done
  [ -z "$core_missing$extra_missing" ] && return 0
  echo "[deps] missing:${core_missing}${extra_missing}"
  if ! "$PY" -m pip --version >/dev/null 2>&1; then
    echo "[deps] '$PY -m pip' unavailable — install requirements manually" >&2
    [ -n "$core_missing" ] && return 1
    return 0
  fi
  if [ -f requirements.txt ]; then
    "$PY" -m pip install -r requirements.txt -q || {
      echo "[deps] pip install failed" >&2
      [ -n "$core_missing" ] && return 1
      return 0
    }
  else
    "$PY" -m pip install $missing -q || { [ -n "$core_missing" ] && return 1; return 0; }
  fi
  local still
  still=$(probe_deps $DEPS 2>/dev/null)
  if [ -n "$still" ]; then
    echo "[deps] still missing after install: $still" >&2
    for m in $still; do case " $CORE_DEPS " in *" $m "*) return 1 ;; esac; done
    return 0
  fi
  echo "[deps] ready"
}

case "${1:-}" in
  init) init_ready ;;
  update) cmd_update ;;
  run) cmd_run ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  *)
    cat <<'USAGE'
Usage: bot.sh <command>
  init      build archive/database if DB missing
  update    refresh archive and rebuild database
  run       run bot in foreground
  start     run bot in background
  stop      stop background bot
  restart   stop then start
USAGE
    exit 2
    ;;
esac
