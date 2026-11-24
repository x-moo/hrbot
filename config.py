"""Shared repository paths and .env loading (process environment wins)."""
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def _parse_env(text):
    """Parse single-line dotenv assignments without expansion or execution."""
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assignment = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not assignment or "\0" in line:
            raise ValueError(f"invalid .env line {number}: expected KEY=value")
        key, value = assignment.groups()
        if value.startswith(("'", '"')):
            quote, result, index = value[0], [], 1
            while index < len(value) and value[index] != quote:
                char = value[index]
                if quote == '"' and char == "\\" and index + 1 < len(value):
                    index += 1
                    escaped = value[index]
                    char = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}.get(
                        escaped, "\\" + escaped)
                result.append(char)
                index += 1
            trailing = value[index + 1:].strip()
            if index == len(value) or (trailing and not trailing.startswith("#")):
                raise ValueError(f"invalid .env line {number}: malformed quoted value")
            value = "".join(result)
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            if value.startswith("#"):
                value = ""
        values.setdefault(key, value)
    return values


def load_env(root=ROOT):
    """Load the first .env in root or cwd; process values (even blank) win.

    Parse the whole file before mutating the environment, so malformed input
    fails without partial configuration. Shell expansion is never performed.
    """
    for candidate in (Path(root) / ".env", Path.cwd() / ".env"):
        if not candidate.exists():
            continue
        try:
            values = _parse_env(candidate.read_text(encoding="utf-8-sig"))
        except UnicodeError:
            raise ValueError(".env must be UTF-8 text") from None
        for key, value in values.items():
            os.environ.setdefault(key, value)
        return True
    return False


@dataclass(frozen=True)
class Settings:
    """Validated runtime values; credential fields are excluded from repr."""

    bot_token: str = field(repr=False)
    db_path: Path
    dump_path: Path
    work_path: Path
    download_dir: Path
    update_time: str
    update_max_age_hours: float
    update_retry_seconds: float
    update_timeout_seconds: float
    poll_sec: float
    longpoll_sec: int
    poll_backoff_max_sec: float
    queue_size: int
    telegram_api: int | None = field(default=None, repr=False)
    telegram_hash: str = field(default="", repr=False)
    user_session_string: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, environ=None, root=ROOT):
        root = Path(root).resolve()
        env = os.environ if environ is None else environ
        def path(*keys, default):
            value = next((env[key] for key in keys if env.get(key)), default)
            return (root / value).resolve()

        update_time = env.get("DB_UPDATE_TIME", "03:00").strip()
        if update_time and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", update_time):
            raise ValueError("DB_UPDATE_TIME must be HH:MM UTC or blank")

        def number(key, default, *, integer=False, allow_zero=False):
            rule = "a non-negative finite number" if allow_zero else (
                "a positive integer" if integer else "a positive finite number")
            try:
                value = (int if integer else float)(env.get(key, str(default)))
                valid = (integer or math.isfinite(value)) and (value >= 0 if allow_zero else value > 0)
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                raise ValueError(f"{key} must be {rule}") from None
            return value

        api = env.get("TELEGRAM_API", "").strip()
        api_hash = env.get("TELEGRAM_HASH", "").strip()
        if bool(api) != bool(api_hash):
            raise ValueError("TELEGRAM_API and TELEGRAM_HASH must be supplied together")
        api_id = number("TELEGRAM_API", 0, integer=True) if api else None
        if api_hash and not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            raise ValueError("TELEGRAM_HASH must contain 32 hexadecimal characters")

        return cls(
            bot_token=env.get("BOT_TOKEN", ""),
            db_path=path("DB_PATH", "DB", default="archive.db"),
            dump_path=path("DUMP", "OUT", default="haramain"),
            work_path=path("WORK", default="work"),
            download_dir=path("DOWNLOAD_DIR", default="downloads"),
            update_time=update_time,
            update_max_age_hours=number("DB_UPDATE_MAX_AGE_HOURS", 24, allow_zero=True),
            update_retry_seconds=number("DB_UPDATE_RETRY_SEC", 300),
            update_timeout_seconds=number("DB_UPDATE_TIMEOUT_SEC", 7200),
            poll_sec=number("POLL_SEC", 3),
            longpoll_sec=number("LONGPOLL_SEC", 25, integer=True),
            poll_backoff_max_sec=number("POLL_BACKOFF_MAX_SEC", 300),
            queue_size=number("LEECH_QUEUE_SIZE", 10, integer=True),
            telegram_api=api_id,
            telegram_hash=api_hash,
            user_session_string=env.get("USER_SESSION_STRING", ""),
        )


def load_settings(root=ROOT):
    """Load dotenv defaults, then validate the effective process environment."""
    load_env(root)
    return Settings.from_env(root=root)
