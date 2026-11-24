#!/usr/bin/env bash
# Prepare the runtime, then replace this process with the foreground bot.
set -euo pipefail
if [ "$#" -ne 0 ]; then
  printf '%s\n' '[start] this foreground entrypoint accepts no arguments' >&2
  exit 2
fi
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
export PYTHON="$PY" PYTHONUNBUFFERED=1
exec "$PY" - <<'PY'
import os
import subprocess
import sys

from config import load_settings

try:
    settings = load_settings()
except (OSError, ValueError) as exc:
    sys.exit(f"[start] configuration error: {exc}")

python = os.environ["PYTHON"]
# The shell fixes PYTHON before loading dotenv: all probes and children use it.
os.environ.update({
    "DB_PATH": str(settings.db_path), "DB": str(settings.db_path),
    "DUMP": str(settings.dump_path), "OUT": str(settings.dump_path),
    "WORK": str(settings.work_path), "DOWNLOAD_DIR": str(settings.download_dir),
    "PYTHONUNBUFFERED": "1",
})


def ensure_deps():
    if os.environ.get("INSTALL_DEPS", "1") == "0":
        print("[start] skipping dependency check", flush=True)
        return
    probe = '''import importlib.metadata as md
import sys
missing = []
for spec in sys.argv[1:]:
    names = spec.split(":")
    for name in names:
        try:
            md.version(name)
            break
        except md.PackageNotFoundError:
            pass
    else:
        missing.append(names[0])
print(" ".join(missing))
'''
    specs = ["requests"]
    if settings.telegram_api is not None:
        specs.extend(["kurigram:pyrogram:pyrofork", "tgcrypto"])

    def missing_deps():
        result = subprocess.run([python, "-c", probe, *specs], check=True,
                                text=True, stdout=subprocess.PIPE)
        return result.stdout.split()

    missing = missing_deps()
    if missing:
        print("[start] missing: " + " ".join(missing), flush=True)
        subprocess.run([python, "-m", "pip", "install", *missing, "-q"], check=True)
        if missing_deps():
            raise RuntimeError("dependencies still missing after installation")
        print("[start] dependencies ready", flush=True)


try:
    ensure_deps()
    db = settings.db_path
    if os.environ.get("BOOTSTRAP", "1") != "0" and not db.is_file():
        print(f"[start] {db} missing -> harvesting", flush=True)
        subprocess.run([python, "-m", "bot.update"], check=True)
    else:
        reason = f"{db} already exists" if db.is_file() else f"BOOTSTRAP=0, {db} not rebuilt"
        print(f"[start] skip bootstrap ({reason})", flush=True)
    print("[start] running bot in foreground", flush=True)
    os.execvp(python, [python, "bot.py"])
except subprocess.CalledProcessError as exc:
    print(f"[start] child command failed (exit {exc.returncode})", file=sys.stderr, flush=True)
    sys.exit(exc.returncode if exc.returncode > 0 else 1)
except (OSError, RuntimeError) as exc:
    sys.exit(f"[start] startup failed: {exc}")
PY
