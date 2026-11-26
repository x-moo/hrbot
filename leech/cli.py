#!/usr/bin/env python3
"""Download a URL with bounded Python streaming, then upload over MTProto.

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
import html
import os
import re
import sys
import time
from pathlib import Path
from config import load_env
from types import SimpleNamespace
from .utils import redact, log_line, emit, env_int, parse_size, media_kind, fmt_size, kind_label
from .downloader import download, split_file
from .session import upload_slot
from .control import check_cancelled, report_stage, await_controlled, TransferError, TransferCancelled

try:
    from pyrogram import Client, enums
    from pyrogram.errors import FloodWait, RPCError, BadRequest
    from pyrogram.types import LinkPreviewOptions
except ImportError:                                         # noqa: BLE001
    sys.exit("pyrogram is missing. Install it: pip install kurigram tgcrypto")

HERE = Path(__file__).resolve().parents[1]
MB = 1024 * 1024
DEFAULT_SPLIT = 2000 * MB           # MTProto cap for a non-premium account


# --- upload -------------------------------------------------------------------
async def upload_with_timeout(client, chat, path, *, timeout=None, **options):
    """Bound library retries; do not blindly resend an ambiguous delivery."""
    if timeout is None:
        timeout = max(1, env_int('LEECH_UPLOAD_TIMEOUT', 1800))
    try:
        check_cancelled()
        result = await await_controlled(upload(client, chat, path, **options), timeout)
        if not getattr(result, "id", None):
            raise RuntimeError("Upload not confirmed by a message id; file retained")
        return result
    except TimeoutError:
        raise RuntimeError('Upload timed out; file retained. Check chat before retrying.') from None


async def upload(client, chat, path, as_doc=False, thumb=None, caption=None):
    """Send one file with the Pyrogram method matching its kind."""
    kind = "doc" if as_doc else media_kind(path)
    cap = html.escape(caption if caption is not None else Path(path).name)
    retries = 0
    while True:
        check_cancelled()
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
            if retries >= 3:
                raise RuntimeError('FloodWait retry limit exceeded; file retained') from None
            retries += 1
            wait = int(getattr(f, "value", None) or f.args[0] or 0)
            if wait < 0 or wait > 60:
                raise RuntimeError('FloodWait exceeds 60s limit; file retained') from None
            log_line(f"upload: flood wait {wait}s")
            await await_controlled(asyncio.sleep(wait + 3), timeout=wait + 4)
        except RPCError as e:
            # A media rejection is explicit non-delivery, not a network retry.
            if (kind != "doc" and isinstance(e, BadRequest)
                    and getattr(e, "ID", "").startswith("MEDIA_")):
                log_line('upload: media rejected; retrying as document')
                kind = 'doc'
            else:
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


# --- orchestration ------------------------------------------------------------
async def run(args):
    outcome = SimpleNamespace(files=[], sent=0)
    try:
        check_cancelled()
        return await _run(args, outcome)
    except Exception as error:
        error_type = TransferCancelled if isinstance(error, TransferCancelled) else TransferError
        raise error_type(redact(str(error)), sent=outcome.sent,
                         total=len(outcome.files),
                         retained=[f for f in outcome.files if Path(f).exists()]) from error


async def _run(args, outcome):
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

    if chat is None and not args.dry_run:
        raise SystemExit("no target chat: pass --chat or set LEECH_CHAT")

    started = time.time()
    log_line(f"start: {name}")        # no local path: it can leak account data
    produced = download(args.url, out_dir, name, headers, args.on_line)
    outcome.files = produced
    got = os.path.getsize(produced[0])
    emit(f"downloaded {fmt_size(got)} in {time.time() - started:.1f}s", args.on_line)

    files = produced
    if not args.no_split:
        files = (split_file(produced[0], split_size, args.on_line)
                 if len(produced) == 1 else produced)
        outcome.files = files
        if len(files) > 1:
            emit(f"{len(files)} parts ready", args.on_line)
    outcome.files = files

    if args.dry_run:
        log_line(f"dry-run: {len(files)} file(s) would be uploaded to {chat}")
        return files

    emit("waiting for upload slot", args.on_line)
    async with upload_slot():
        uploader = None
        bot = (Client("haramain_leech_bot", api_id, api_hash, bot_token=bot_token,
                      in_memory=True, no_updates=True, sleep_threshold=0,
                      workdir=str(HERE), parse_mode=enums.ParseMode.HTML,
                      link_preview_options=LinkPreviewOptions(is_disabled=True))
               if bot_token else None)

        delete_after = not args.keep and os.environ.get("KEEP_FILES", "0") != "1"
        freed = 0
        user_premium = False
        try:
            # session identity is never logged: usernames/names are account data
            if bot is not None:
                await await_controlled(bot.start(), timeout=120)
                log_line("session: bot ready")
            report_stage('uploading')
            total = len(files)
            for i, f in enumerate(files, 1):
                check_cancelled()
                size = os.path.getsize(f)
                needs_user = bool(session and (bot is None or chat == "me" or size > DEFAULT_SPLIT))
                if needs_user and uploader is None:
                    uploader = Client("haramain_leech_user", api_id, api_hash,
                                      session_string=session, workdir=str(HERE),
                                      sleep_threshold=0,
                                      parse_mode=enums.ParseMode.HTML,
                                      link_preview_options=LinkPreviewOptions(is_disabled=True))
                    await await_controlled(uploader.start(), timeout=120)
                    me = await await_controlled(uploader.get_me(), timeout=120)
                    user_premium = bool(getattr(me, "is_premium", False))
                    log_line(f"session: user ready (premium={user_premium})")
                if uploader is not None and (chat == "me" or size > DEFAULT_SPLIT):
                    if not user_premium and size > DEFAULT_SPLIT:
                        raise RuntimeError("File exceeds non-premium upload limit; file retained")
                    client = uploader
                else:
                    client = bot or uploader
                if client is bot and size > DEFAULT_SPLIT:
                    raise RuntimeError("File exceeds bot upload limit; file retained")
                emit(f"uploading {i}/{total} · {Path(f).name} · "
                     f"{fmt_size(size)} · {kind_label(f)}", args.on_line)
                await upload_with_timeout(client, chat, f, as_doc=args.as_doc, thumb=thumb)
                outcome.sent += 1
                emit(f"sent {i}/{total} ✓", args.on_line)
                if delete_after:
                    # free the disk as soon as Telegram confirmed the file
                    if discard(f):
                        freed += size
                    else:
                        log_line(f"could not remove {Path(f).name}")
        finally:
            transfer_failed = sys.exc_info()[0] is not None
            teardown_error = None
            for c in (uploader, bot):
                if c is not None:
                    try:
                        await _stop_client(c)
                    except Exception as error:
                        teardown_error = error
            if teardown_error is not None and not transfer_failed:
                raise teardown_error

    # The downloader owns and cleans its partials; never guess sidecar ownership.
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


async def _stop_client(client):
    """Stop a client, falling back to disconnect for partial startup."""
    try:
        await asyncio.wait_for(client.stop(), timeout=30)
        return
    except Exception as exc:  # noqa: BLE001
        log_line(f"session: stop failed ({type(exc).__name__}); disconnecting")
    try:
        await asyncio.wait_for(client.disconnect(), timeout=30)
    except Exception as exc:  # noqa: BLE001
        log_line(f"session: disconnect failed ({type(exc).__name__})")
        raise


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
    if not text:
        return None, "no target chat to upload to"
    if re.fullmatch(r"-?\d+", text):
        return int(text), None
    if text.lower() in ("me", "self"):
        return "me", None
    return text, None


def run_leech_sync(url, *, chat=None, name=None, out=None, headers=None,
                   split_size=None, as_doc=False, thumb=None,
                   no_split=False, to_self=False, keep=False, progress=None,
                   target_dir=None):
    """Blocking wrapper around run() for a bot handler.

    `progress(text)` is called for each download/upload line so the caller can
    edit a status message. Returns the uploaded file paths (even if cleaned).
    TransferError exposes sent (acknowledgements), total and retained (paths).
    TransferCancelled is a TransferError; wrap this call in transfer_control
    inside the worker thread for cancellation and machine-readable stages.
    """
    chat_id, err = resolve_target(chat, to_self=to_self)
    if err and target_dir is None:
        raise RuntimeError(err)
    ns = SimpleNamespace(
        url=url, out=str(target_dir) if target_dir is not None else out, name=name,
        chat=str(chat_id) if chat_id not in (None, "me") else "me",
        header=list(headers or []), no_split=no_split,
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
                    help="compatibility option; ignored by the streaming downloader")
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
