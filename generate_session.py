#!/usr/bin/env python3
"""Generate a Pyrogram USER_SESSION_STRING for leech.py.

Uses the same TELEGRAM_API / TELEGRAM_HASH from .env (or the environment) and
asks Telegram for a login code. The printed string lets leech.py upload as
your account — required for files above 2 GB.

    python3 generate_session.py
"""
import os
import sys
from pathlib import Path

try:
    from pyrogram import Client
except ImportError:                                        # noqa: BLE001
    sys.exit("pyrogram is missing. Install it: pip install kurigram tgcrypto")

HERE = Path(__file__).resolve().parent


def load_env():
    """Read KEY=VALUE from .env next to the script (env vars win)."""
    for cand in (HERE / ".env", Path.cwd() / ".env"):
        if not cand.exists():
            continue
        with open(cand, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        return True
    return False


def main():
    load_env()
    api_hash = os.environ.get("TELEGRAM_HASH", "").strip()
    try:
        api_id = int(os.environ.get("TELEGRAM_API", "").strip())
    except ValueError:
        sys.exit("TELEGRAM_API / TELEGRAM_HASH missing or not numeric — set them in .env")
    if not api_hash:
        sys.exit("TELEGRAM_API / TELEGRAM_HASH missing — set them in .env")

    with Client(":memory:", api_id, api_hash, in_memory=True) as app:
        me = app.get_me()
        premium = getattr(me, "is_premium", False)
        print(f"\nlogged in as {me.first_name} (@{me.username or me.id})")
        print(f"premium: {premium}  ->  upload ceiling "
              f"{'4 GB' if premium else '2 GB'}\n")
        print("USER_SESSION_STRING=")
        print(app.export_session_string())
        print("\nPaste that value into .env as USER_SESSION_STRING=...")


if __name__ == "__main__":
    main()
