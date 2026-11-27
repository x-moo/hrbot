"""Bot API transport, retries and text delivery."""
import time
import requests
from bot.i18n import translate

API = "https://api.telegram.org/bot{token}/{method}"
NO_PREVIEW = {"link_preview_options": {"is_disabled": True}}

def chunk_text(lines, limit=3800):
    """Pack lines within `limit` UTF-16 units, splitting oversized lines."""
    if limit < 1:
        raise ValueError("limit must be positive")
    out, buf, size = [], "", 0
    for line in lines:
        line_size = sum(2 if ord(char) > 0xFFFF else 1 for char in line)
        if buf and size + 1 + line_size > limit:
            out.append(buf)
            buf, size = "", 0
        if line_size <= limit:
            size += line_size + bool(buf)
            buf = (buf + "\n" + line) if buf else line
            continue
        start = 0
        for index, char in enumerate(line):
            width = 2 if ord(char) > 0xFFFF else 1
            if width > limit:
                raise ValueError("limit cannot fit a single character")
            if size + width > limit:
                out.append(line[start:index])
                start, size = index, 0
            size += width
        buf = line[start:]
    if buf:
        out.append(buf)
    return out


class TgError(RuntimeError):
    """Telegram API error: `error_code`, `description`, optional `retry_after`."""

    def __init__(self, method, data):
        self.method = method
        self.error_code = data.get("error_code", 0)
        self.description = data.get("description", "?")
        params = data.get("parameters") or {}
        self.retry_after = params.get("retry_after")
        self.migrate_chat_id = params.get("migrate_to_chat_id")
        super().__init__(f"tg {method}: {self.description}")


def tg_call(token, method, payload=None, timeout=30, tries=3, _retried=0):
    """POST one Bot API call and return `result`. Retries 429 flood control."""
    if payload is None:
        payload = {}
    try:
        r = requests.post(API.format(token=token, method=method),
                          json=payload, timeout=timeout)
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise TgError(method, {"error_code": 0, "description": str(e)}) from e

    if data.get("ok"):
        return data["result"]

    err = TgError(method, data)
    # retry_after can legitimately be 0 -> test `is not None`, never truthiness
    if err.retry_after is not None and _retried < tries:
        time.sleep(err.retry_after + 0.3)
        return tg_call(token, method, payload, timeout, tries, _retried + 1)
    raise err


def _send_one(token, chat, part, with_format):
    payload = {"chat_id": chat, "text": part, **NO_PREVIEW}
    if with_format:
        payload["parse_mode"] = "Markdown"
    try:
        return tg_call(token, "sendMessage", payload, timeout=15, tries=3)
    except TgError as e:
        # chat migrated group->supergroup: resend against the new id (once)
        if e.migrate_chat_id:
            payload["chat_id"] = e.migrate_chat_id
            return tg_call(token, "sendMessage", payload, timeout=15, tries=3)
        # markdown can't be parsed -> the same text without formatting is safe
        if with_format and "parse" in e.description.lower():
            payload.pop("parse_mode", None)
            return tg_call(token, "sendMessage", payload, timeout=15, tries=3)
        raise


def send(token, chat, text, *, localize=True):
    """Send Markdown text; prelocalized presenters may preserve literal data."""
    for part in chunk_text([translate(text) if localize else text]):
        _send_one(token, chat, part, with_format=True)


def send_long(token, chat, lines, *, localize=True):
    for part in chunk_text([translate(line) if localize else line for line in lines]):
        _send_one(token, chat, part, with_format=True)
