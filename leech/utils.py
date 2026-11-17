"""Formatting, redaction and configuration helpers."""
import os
import re
import time
from pathlib import Path
MB = 1024 * 1024
SECRET_KEYS = ("TELEGRAM_HASH", "BOT_TOKEN", "USER_SESSION_STRING")

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
