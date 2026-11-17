#!/usr/bin/env bash
# Bot control script: init / run / start / stop / restart.
#
#   bash bot.sh init      build the archive (harvest + import) if DB is missing
#   bash bot.sh run       install missing deps, then run in the foreground
#   bash bot.sh start     install missing deps, then run in the background
#   bash bot.sh stop      stop the background bot
#   bash bot.sh restart   stop, then start again
#
# Env:
#   INSTALL_DEPS=0  skip the pip check entirely (offline/air-gapped)
#   BOOTSTRAP=0   never run bootstrap, use the DB as-is
#   SWEEP=1       include the page-sweep during bootstrap (3-8 h, resumable)
#   PIDFILE       pid file path   (default: bot.pid next to this script)
#   LOGFILE       background log  (default: bot.log next to this script)
#   DB / DUMP / PYTHON  same overrides as before (archive.db / haramain / python3)
set -uo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
DB=${DB:-${DB_PATH:-archive.db}}   # DB_PATH (bot.py's var) is accepted too
DUMP=${DUMP:-haramain}
PIDFILE=${PIDFILE:-$PWD/bot.pid}
LOGFILE=${LOGFILE:-$PWD/bot.log}
BOOT_WAIT=10   # seconds to wait for the background bot to die

# panels often drop the exec bit on upload -> never depend on it
find scripts -maxdepth 1 -name '*.sh' -exec chmod +x {} + 2>/dev/null || true

pid_live() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

running_pid() {
  [ -f "$PIDFILE" ] || return 1
  local pid
  pid=$(cat "$PIDFILE" 2>/dev/null)
  pid_live "$pid" && { echo "$pid"; return 0; }
  rm -f "$PIDFILE"          # stale pidfile
  return 1
}

init_ready() {
  if [ "${BOOTSTRAP:-1}" != "0" ] && [ ! -f "$DB" ]; then
    echo "[init] $DB missing -> harvesting via feed (resumable, safe to stop)"
    ( cd scripts && bash run.sh sitemap ) || exit 1
    ( cd scripts && bash run.sh feed )    || exit 1
    if [ "${SWEEP:-0}" = "1" ]; then
      echo "[init] SWEEP=1 -> page-sweep (3-8 h)"
      ( cd scripts && bash run.sh sweep ) || exit 1
    fi
    ( cd scripts && bash run.sh build )   || exit 1
    "$PY" import_dump.py --dump "$DUMP" --db "$DB" || exit 1
  else
    [ -f "$DB" ] && echo "[init] skip ($DB already exists)" \
                  || echo "[init] skip (BOOTSTRAP=0, $DB not rebuilt)"
  fi
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
  if pid=$(running_pid); then
    echo "[start] already running (pid $pid)"
    return 0
  fi
  ensure_deps || exit 1
  init_ready
  echo "[start] launching in background -> $LOGFILE (pidfile $PIDFILE)"
  nohup "$PY" bot.py >>"$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 2
  if pid=$(running_pid); then
    echo "[start] up (pid $pid)"
  else
    echo "[start] died within 2s — see $LOGFILE:" >&2
    tail -n 5 "$LOGFILE" >&2 || true
    rm -f "$PIDFILE"
    exit 1
  fi
}

cmd_stop() {
  if ! pid=$(running_pid); then
    echo "[stop] not running"
    return 0
  fi
  echo "[stop] sending TERM to pid $pid"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 "$BOOT_WAIT"); do
    pid_live "$pid" || break
    sleep 1
  done
  if pid_live "$pid"; then
    echo "[stop] still alive after ${BOOT_WAIT}s -> KILL"
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$PIDFILE"
  if pid_live "$pid"; then
    echo "[stop] pid $pid refused to die" >&2
    exit 1
  fi
  echo "[stop] stopped"
}


# --- dependencies -------------------------------------------------------------
# Distribution names, with importable aliases that also satisfy them. Using
# pip metadata (not `import X`) matters: kurigram installs the `pyrogram`
# module, so import-probing reports it missing forever.
DEPS="requests kurigram:pyrogram,pyrofork tgcrypto"
CORE_DEPS="requests"          # bot.py cannot run without these
EXTRA_DEPS="kurigram tgcrypto"  # /dl (MTProto) needs these

probe_deps() {
  # prints the distribution names that are NOT installed
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
  if [ "${INSTALL_DEPS:-1}" = "0" ]; then
    echo "[deps] INSTALL_DEPS=0 -> skipping check"
    return 0
  fi

  local missing core_missing="" extra_missing="" m
  missing=$(probe_deps $DEPS 2>/dev/null) || missing=""
  for m in $missing; do
    case " $CORE_DEPS " in *" $m "*) core_missing="$core_missing $m" ;; esac
    case " $EXTRA_DEPS " in *" $m "*) extra_missing="$extra_missing $m" ;; esac
  done
  [ -z "$core_missing$extra_missing" ] && return 0

  echo "[deps] missing:${core_missing}${extra_missing}"
  if ! "$PY" -m pip --version >/dev/null 2>&1; then
    echo "[deps] '$PY -m pip' is unusable — install manually:" >&2
    echo "       $PY -m ensurepip --upgrade && $PY -m pip install -r requirements.txt" >&2
    [ -n "$core_missing" ] && return 1
    return 0            # /dl reports the missing MTProto deps itself
  fi

  if [ -f requirements.txt ]; then
    "$PY" -m pip install -r requirements.txt -q || {
      echo "[deps] 'pip install -r requirements.txt' failed" >&2
      [ -n "$core_missing" ] && return 1
      return 0
    }
  else
    # shellcheck disable=SC2086
    "$PY" -m pip install $missing -q || {
      [ -n "$core_missing" ] && return 1
      return 0
    }
  fi

  # verify instead of trusting pip's exit code
  local still
  still=$(probe_deps $DEPS 2>/dev/null)
  if [ -n "$still" ]; then
    echo "[deps] still missing after install: $still" >&2
    for m in $still; do
      case " $CORE_DEPS " in *" $m "*) return 1 ;; esac
    done
    return 0
  fi
  echo "[deps] ready"
  return 0
}

case "${1:-}" in
  init)    init_ready ;;
  run)     cmd_run ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  *)
    cat <<'USAGE'
Usage: bot.sh <command>

  init      build the archive (harvest + import) if the DB is missing
  run       install missing deps, then run the bot in the foreground
  start     install missing deps, then run the bot in the background
  stop      stop the background bot
  restart   stop, then start again

Env: BOOTSTRAP=0  SWEEP=1  INSTALL_DEPS=0  PIDFILE  LOGFILE  DB / DUMP / PYTHON
USAGE
    exit 2
    ;;
esac