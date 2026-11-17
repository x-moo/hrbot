#!/usr/bin/env python3
"""Download a URL with aria2c, then upload it to Telegram over MTProto.

The hosted Bot API caps uploads at 50 MB; MTProto does not, so the real
ceiling is the per-file limit (2 GB, 4 GB with Premium) and files above
LEECH_SPLIT_SIZE are split first.

Usage:
    python3 leech.py <url> [options]

Config (.env next to the script, or env vars):
    TELEGRAM_API / TELEGRAM_HASH   api_id and api_hash from my.telegram.org
    BOT_TOKEN                      bot token from @BotFather
    USER_SESSION_STRING            optional account session (needed > 2 GB)
    LEECH_CHAT                     default target chat
    DOWNLOAD_DIR                   default download directory
    LEECH_SPLIT_SIZE               split threshold while uploading
    THUMB_PATH                     default thumbnail path
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

try:
    from pyrogram import Client, enums
    from pyrogram.errors import FloodWait, RPCError
    from pyrogram.types import LinkPreviewOptions
except ImportError:                                         # noqa: BLE001
    sys.exit("pyrogram is missing. Install it: pip install kurigram tgcrypto")

HERE = Path(__file__).resolve().parent
MB = 1024 * 1024
DEFAULT_SPLIT = 2000 * MB           # MTProto cap for a non-premium account
PREMIUM_SPLIT = 4000 * MB           # MTProto cap with Premium
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SECRET_KEYS = ("TELEGRAM_HASH", "BOT_TOKEN", "USER_SESSION_STRING")
UPDATE_EVERY = 3.0                  # seconds between chat status updates


# --- log hygiene --------------------------------------------------------------
def redact(text):
    """Strip credentials out of anything that may reach a log or a chat."""
    if text is None:
        return ""
    out = str(text)
    out = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b", "<bot-token>", out)
    out = re.sub(r"\b[0-9a-f]{32}\b", "<api-hash>", out)
    out = re.sub(r"\b[A-Za-z0-9+/]{300,}={0,2}\b", "<session>", out)
    for key in SECRET_KEYS:
        val = os.environ.get(key, "")
        if val and len(val) >= 8:
            out = out.replace(val, f"<{key.lower()}>")
    return out


def log_line(text):
    print(f"{time.strftime('%H:%M:%S')} {redact(text)}", flush=True)


def emit(text, on_line=None):
    """Log a landmark line (redacted) and forward it to the caller's hook."""
    log_line(text)
    if on_line:
        on_line(redact(text))


# --- config -------------------------------------------------------------------
def load_env():
    """Read KEY=VALUE from .env next to the script (or cwd). Existing wins."""
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


def env_int(name, default=0):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def parse_size(text, default):
    """'2.5gb' / '1000mb' / '40000000' -> bytes (default when unparsable)."""
    if text is None:
        return default
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(b|kb|mb|gb|k|m|g)?", str(text).strip().lower())
    if not m:
        return default
    factor = {"b": 1, "k": 1024, "kb": 1024, "m": MB, "mb": MB,
              "g": 1024 * MB, "gb": 1024 * MB}[m.group(2) or "b"]
    return int(float(m.group(1)) * factor)


# --- aria2c -------------------------------------------------------------------
def aria2c_path():
    """Path to aria2c, installing it once with the system package manager."""
    found = shutil.which("aria2c")
    if found:
        return found
    if os.geteuid() == 0:
        for cmd in (["apt-get", "-y", "install", "aria2"],
                    ["apk", "add", "aria2"],
                    ["dnf", "-y", "install", "aria2"]):
            if not shutil.which(cmd[0]):
                continue
            log_line(f"aria2c missing — installing with {cmd[0]}")
            try:
                subprocess.run(cmd, capture_output=True, timeout=300)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if (found := shutil.which("aria2c")):
                return found
    raise SystemExit("aria2c is required and not installed (apt install aria2)")


def progress_of(line):
    """Safe one-liner from an aria2c line, or None. Never returns the raw line."""
    m = re.search(r"(\d{1,3})%", line)
    if m:
        return f"downloading... {m.group(1)}%"
    m = re.search(r"DL:([\d.]+\w+)(?:\s+ETA:(\S+))?", line)
    if m:
        return f"downloading at {m.group(1)}/s"
    return None


def download(url, out_dir, name, headers, jobs, on_line=None):
    """Fetch `url` into out_dir with aria2c. -> list of produced file paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    cmd = [aria2c_path(), "-x", str(jobs), "-s", str(jobs), "-k", "1M",
           "--file-allocation=none", "--summary-interval=5",
           "--console-log-level=warn", "--allow-overwrite=false",
           "--auto-file-renaming=false", "--user-agent", UA,
           "-d", str(out_dir), "-o", name]
    for h in headers:
        cmd += ["--header", h]
    cmd.append(url)

    log_line("aria2c: downloading")
    last = ""
    sent = 0.0
    with subprocess.Popen(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          errors="replace", bufsize=1) as proc:
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            summary = progress_of(line)
            if not summary:
                log_line(f"aria2c: {line}")     # only warn-level output remains
                continue
            # one line every UPDATE_EVERY seconds to both the log and the chat
            if time.time() - sent >= UPDATE_EVERY:
                sent = time.time()
                if summary != last:
                    log_line(f"aria2c: {summary}")
                    last = summary
                if on_line:
                    on_line(summary)
    if proc.returncode != 0 or not target.exists():
        raise SystemExit(f"aria2c failed (exit {proc.returncode})")
    return [str(target)]


# --- splitting ----------------------------------------------------------------
def split_file(path, split_size, on_line=None):
    """Split `path` into <name>.001, .002 ... (returns the file list)."""
    path = Path(path)
    size = path.stat().st_size
    if split_size <= 0 or size <= split_size:
        return [str(path)]
    emit(f"splitting {fmt_size(size)} into {fmt_size(split_size)} parts", on_line)
    rc = subprocess.call(["split", "--numeric-suffixes=1", "--suffix-length=3",
                          f"--bytes={split_size}", str(path), f"{path}."])
    if rc != 0:
        raise SystemExit("split failed")
    parts = sorted(str(p) for p in path.parent.glob(path.name + ".[0-9][0-9][0-9]"))
    if not parts:
        raise SystemExit("split produced no parts")
    path.unlink()               # originals go away once the parts exist
    return parts


# --- upload -------------------------------------------------------------------
def media_kind(path):
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in {"mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "wma", "ra"}:
        return "audio"
    if ext in {"mp4", "mkv", "avi", "mov", "webm", "wmv", "flv", "3gp", "ts", "m4v"}:
        return "video"
    if ext in {"jpg", "jpeg", "png", "webp", "gif", "bmp"}:
        return "photo"
    return "doc"


def fmt_size(nbytes):
    """Human-readable size: 512 KB, 12.7 MB, 2.4 GB."""
    n = float(nbytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return (f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}")
        n /= 1024


def kind_label(path):
    return {"doc": "file", "photo": "image"}.get(media_kind(path), media_kind(path))


async def upload(client, chat, path, as_doc=False, thumb=None, caption=None):
    """Send one file with the Pyrogram method matching its kind."""
    kind = "doc" if as_doc else media_kind(path)
    cap = caption if caption is not None else Path(path).name
    try:
        if kind == "video":
            return await client.send_video(chat, path, caption=cap,
                                           supports_streaming=True, thumb=thumb)
        if kind == "audio":
            return await client.send_audio(chat, path, caption=cap, thumb=thumb)
        if kind == "photo":
            return await client.send_photo(chat, path, caption=cap)
        return await client.send_document(chat, path, caption=cap, thumb=thumb)
    except FloodWait as f:
        wait = int(getattr(f, "value", None) or f.args[0] or 0)
        print(f"[upload] flood wait {wait}s", flush=True)
        await asyncio.sleep(wait + 3)
        return await upload(client, chat, path, as_doc, thumb, caption)
    except RPCError as e:
        # a "media invalid" style rejection still delivers as a plain document
        if kind != "doc" and "MEDIA" in str(e).upper():
            print(f"[upload] {e} -> retrying as document", flush=True)
            return await client.send_document(chat, path, caption=cap, thumb=thumb)
        raise


def discard(path):
    """Best-effort unlink; storage is scarce and a stray file is expensive."""
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False


def prune_dir(path, stop=None):
    """Remove `path` and empty parents up to (but not including) `stop`."""
    path = Path(path)
    stop = Path(stop or path.anchor or "/")
    while path != stop and stop in path.parents:
        try:
            path.rmdir()
        except OSError:
            return False          # not empty -> leave the rest alone
        path = path.parent
    return True


def cleanup_partials(files):
    for f in list(files):
        for suffix in (".part", ".aria2"):
            discard(Path(f + suffix))


# --- orchestration ------------------------------------------------------------
async def run(args):
    api_id = env_int("TELEGRAM_API")
    api_hash = os.environ.get("TELEGRAM_HASH", "")
    bot_token = os.environ.get("BOT_TOKEN", "")
    session = os.environ.get("USER_SESSION_STRING", "").strip()
    if not api_id or not api_hash:
        raise SystemExit("TELEGRAM_API / TELEGRAM_HASH missing (my.telegram.org)")
    if not bot_token and not session:
        raise SystemExit("need BOT_TOKEN or USER_SESSION_STRING to upload")

    out_dir = args.out or os.environ.get("DOWNLOAD_DIR") or str(HERE / "downloads")
    name = args.name or Path(args.url.split("?")[0]).name or "download.bin"
    chat, _ = resolve_target(args.chat or os.environ.get("LEECH_CHAT", ""))
    headers = list(args.header or [])
    split_size = parse_size(args.split_size or os.environ.get("LEECH_SPLIT_SIZE"),
                            DEFAULT_SPLIT)
    thumb = args.thumb or os.environ.get("THUMB_PATH") or None

    started = time.time()
    log_line(f"start: {name}")        # no local path: it can leak account data
    produced = download(args.url, out_dir, name, headers, args.jobs, args.on_line)
    got = os.path.getsize(produced[0])
    emit(f"downloaded {fmt_size(got)} in {time.time() - started:.1f}s", args.on_line)

    files = produced
    if not args.no_split:
        files = (split_file(produced[0], split_size, args.on_line)
                 if len(produced) == 1 else produced)
        if len(files) > 1:
            emit(f"{len(files)} parts ready", args.on_line)

    if args.dry_run:
        log_line(f"dry-run: {len(files)} file(s) would be uploaded to {chat}")
        return files
    if chat is None:
        raise SystemExit("no target chat: pass --chat or set LEECH_CHAT")

    uploader = (Client("haramain_leech_user", api_id, api_hash,
                       session_string=session, workdir=str(HERE),
                       parse_mode=enums.ParseMode.HTML,
                       link_preview_options=LinkPreviewOptions(is_disabled=True))
                if session and not bot_token else None)
    bot = (Client("haramain_leech_bot", api_id, api_hash, bot_token=bot_token,
                  workdir=str(HERE), parse_mode=enums.ParseMode.HTML,
                  link_preview_options=LinkPreviewOptions(is_disabled=True))
           if bot_token else None)

    delete_after = not args.keep and os.environ.get("KEEP_FILES", "0") != "1"
    freed = 0
    user_premium = False
    try:
        # session identity is never logged: usernames/names are account data
        if bot is not None:
            await bot.start()
            log_line("session: bot ready")
        if uploader is not None:
            await uploader.start()
            me = await uploader.get_me()
            user_premium = bool(getattr(me, "is_premium", False))
            log_line(f"session: user ready (premium={user_premium})")

        total = len(files)
        for i, f in enumerate(files, 1):
            size = os.path.getsize(f)
            if uploader is not None and size > DEFAULT_SPLIT:
                if not user_premium:
                    log_line(f"{fmt_size(size)} exceeds the 2000 MB non-premium limit")
                client = uploader
            else:
                client = bot or uploader
            emit(f"uploading {i}/{total} · {Path(f).name} · "
                 f"{fmt_size(size)} · {kind_label(f)}", args.on_line)
            await upload(client, chat, f, as_doc=args.as_doc, thumb=thumb)
            emit(f"sent {i}/{total} ✓", args.on_line)
            if delete_after:
                # free the disk as soon as Telegram confirmed the file
                if discard(f):
                    freed += size
                else:
                    log_line(f"could not remove {Path(f).name}")
    finally:
        for c in (uploader, bot):
            if c is not None:
                try:
                    await c.stop()
                except Exception:                                # noqa: BLE001
                    pass

    cleanup_partials(files)
    if delete_after:
        for f in files:
            extra = os.path.getsize(f) if os.path.exists(f) else 0
            if extra and discard(f):
                freed += extra
        prune_dir(out_dir)
        if freed:
            emit(f"storage freed: {fmt_size(freed)}", args.on_line)
    emit(f"done: {len(files)} file(s) in {time.time() - started:.1f}s", args.on_line)
    return files


def resolve_target(chat, *, to_self=False):
    """Resolve a bot command target.

    Returns the chat id/@username, or ("me", None) when the command came from
    a private chat and `to_self` is set — a user session cannot post to its own
    private chat, but "me" (Saved Messages) always works.
    """
    if to_self:
        return "me", None
    if chat is None:
        return None, "no target chat to upload to"
    if isinstance(chat, int):
        return chat, None
    text = str(chat).strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text), None
    if text.lower() in ("me", "self"):
        return "me", None
    return text, None


def run_leech_sync(url, *, chat=None, name=None, out=None, headers=None,
                   jobs=4, split_size=None, as_doc=False, thumb=None,
                   no_split=False, to_self=False, keep=False, progress=None,
                   target_dir=None):
    """Blocking wrapper around run() for a bot handler.

    `progress(text)` is called for each download/upload line so the caller can
    edit a status message. Returns the uploaded file paths; raises RuntimeError.
    """
    chat_id, err = resolve_target(chat, to_self=to_self)
    if err and target_dir is None:
        raise RuntimeError(err)
    ns = SimpleNamespace(
        url=url, out=str(target_dir) if target_dir is not None else out, name=name,
        chat=str(chat_id) if chat_id not in (None, "me") else "me",
        header=list(headers or []), jobs=jobs, no_split=no_split,
        split_size=split_size, keep=keep, as_doc=as_doc, thumb=thumb,
        dry_run=False, on_line=progress)
    _check_credentials()
    try:
        return asyncio.run(run(ns))
    except SystemExit as e:                    # -> ordinary error for the bot
        raise RuntimeError(str(e)) from None


def _check_credentials():
    if not env_int("TELEGRAM_API") or not os.environ.get("TELEGRAM_HASH"):
        raise RuntimeError("TELEGRAM_API / TELEGRAM_HASH missing in .env")
    if not os.environ.get("BOT_TOKEN") and \
            not os.environ.get("USER_SESSION_STRING", "").strip():
        raise RuntimeError("need BOT_TOKEN or USER_SESSION_STRING in .env")


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("-o", "--out", help="download directory")
    ap.add_argument("-n", "--name", help="output file name")
    ap.add_argument("-c", "--chat", help="target chat id / @username")
    ap.add_argument("-H", "--header", action="append",
                    help="extra request header (repeatable)")
    ap.add_argument("-j", "--jobs", type=int, default=4,
                    help="aria2c connections (default 4)")
    ap.add_argument("--no-split", action="store_true",
                    help="upload as one file, never split")
    ap.add_argument("-s", "--split-size", help="split threshold, e.g. 1900mb")
    ap.add_argument("--as-doc", action="store_true",
                    help="send as document even for video/audio")
    ap.add_argument("-t", "--thumb", help="thumbnail image path")
    ap.add_argument("--keep", action="store_true",
                    help="leave the downloaded files on disk (default: delete "
                         "each one right after it is uploaded)")
    ap.add_argument("--dry-run", action="store_true",
                    help="download/split only, do not connect to Telegram")
    return ap


def main():
    load_env()
    args = build_parser().parse_args()
    args.on_line = None                    # CLI already prints everything
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log_line("interrupted")


if __name__ == "__main__":
    main()
