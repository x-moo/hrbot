#!/usr/bin/env python3
"""Telegram bot for the haramain.info media archive (stdlib + requests).

Commands:
    /start /help         command list
    /latest [n]          most recent media (default 5, max 10)
    /date YYYY-MM-DD     media from one date
    /search <word...>    search file names/slugs
    /year <yyyy>         per-year summary
    /stat                archive statistics
    Archive results include download buttons.

`/date` and `/search` results are paged (PAGE_SIZE per message) with inline
⬅️ Back / Next ➡️ buttons that re-render the message in place.

Config (.env next to the script, or env vars):
    BOT_TOKEN       token from @BotFather   (required)
    DB_PATH         path to archive.db      (default: next to this script)
    POLL_SEC        poll interval           (default: 3)
    LONGPOLL_SEC    long-poll timeout       (default: 25)
"""
import itertools
from contextvars import copy_context, ContextVar
import secrets
import os
import re
import sqlite3
from archive.repository import Archive, PAGE_SIZE
import sys
import threading
import time
from pathlib import Path
from config import load_env

from bot.telegram import NO_PREVIEW, TgError, tg_call, send, send_long
from bot.i18n import Preferences, language, translate, localize_markup

preferences = Preferences()
current_chat = ContextVar('current_chat', default=None)
_DOWNLOADS = {}
_DOWNLOAD_LOCK = threading.Lock()


def _download_rows(rows, chat):
    """Opaque, expiring archive URL snapshots; never accept a callback URL."""
    buttons = []
    with _DOWNLOAD_LOCK:
        now = time.time()
        for key in [k for k, value in _DOWNLOADS.items() if value['expires'] < now]:
            del _DOWNLOADS[key]
        for row in rows:
            if not row['url'].lower().startswith(('http://', 'https://')):
                continue
            key = secrets.token_urlsafe(12)
            _DOWNLOADS[key] = {'chat': chat, 'url': row['url'], 'expires': now + VIEW_TTL}
            buttons.append([{'text': '⬇️ Download · ' + (row['fname'] or 'file')[:45],
                             'callback_data': 'get:' + key}])
    return buttons
from bot.commands import configure_menu


def _configure_menu(token, **options):
    """Menu setup failure must not prevent normal bot operation."""
    try:
        configure_menu(token, tg_call, **options)
    except (TgError, RuntimeError) as error:
        print(f'command menu setup failed: {error}', flush=True)

HERE = Path(__file__).resolve().parents[1]
DEFAULT_DB = HERE / "archive.db"
DL_LINES = 10          # lines kept in a per-download status message
DL_JOBS = 2            # concurrent downloads per chat
VIEW_TTL = 900         # seconds a result message's buttons keep working



def fmt_count(n):
    """Compact count for a button label: 947, 1.2k, 12k."""
    if n < 1000:
        return str(n)
    if n < 10000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n // 1000}k"


def fmt_item(r):
    if r["year"] and r["month"]:
        date = f"{r['year']:04d}-{r['month']:02d}"
        if r["day"]:
            date += f"-{r['day']:02d}"
    else:                       # rare: no parsed /YYYY/MM/ post path
        date = str(r["year"]) if r["year"] else "?"
    name = r["fname"] or r["slug"] or "?"
    icon = "\U0001F3A5" if (r["kind"] or "").startswith("video") else "\U0001F3A7"
    return f"{icon} [{name}]({r['url']})\n   `{date}` · [source]({r['post']})"


# --- paged result views (Next/Back inline buttons) ----------------------------
_VIEWS = {}                 # (chat_id, message_id) -> {"spec": ..., "expires": t}
_VIEW_LOCK = threading.Lock()


def _remember(chat, message_id, spec):
    with _VIEW_LOCK:
        now = time.time()
        for k in [k for k, v in _VIEWS.items() if v["expires"] < now]:
            _VIEWS.pop(k, None)                 # expired views are dropped lazily
        _VIEWS[(chat, message_id)] = {"spec": spec, "expires": now + VIEW_TTL}


def _recall(chat, message_id):
    with _VIEW_LOCK:
        v = _VIEWS.get((chat, message_id))
        if not v or v["expires"] < time.time():
            return None
        return v["spec"]


def _mk(rows):
    """Inline keyboard from rows, dropping empty ones."""
    return {"inline_keyboard": [r for r in rows if r]}


def _pager_rows(page, pages, prev_data, next_data, extra=()):
    """Back · counter · Next row (only the buttons that apply) + extra rows."""
    if pages <= 1:
        return _mk(list(extra)) if extra else None
    row = []
    if page > 1:
        row.append({"text": "⬅️ Back", "callback_data": prev_data})
    row.append({"text": f"📄 {page}/{pages}", "callback_data": "c:x"})
    if page < pages:
        row.append({"text": "Next ➡️", "callback_data": next_data})
    return _mk([row, *extra])


def _grid(items, per_row=3, extra=()):
    """Lay [{text, callback_data}] out in rows of `per_row`."""
    rows = [items[i:i + per_row] for i in range(0, len(items), per_row)]
    return _mk([*rows, *extra])


HOME = {"text": "🔝 Menu", "callback_data": "c:menu"}


def menu_markup():
    """The /start keyboard: every command as a button."""
    return _mk([
        [{"text": "🆕 Latest", "callback_data": "c:latest"},
         {"text": "🔎 Search", "callback_data": "c:search"}],
        [{"text": "❓ Help", "callback_data": "c:help"},
         {"text": "🌐 Language", "callback_data": "c:language"}],
    ])


def years_markup(db, more_rows=()):
    """One button per year (newest first), 3 per row."""
    items = [{"text": f"{y} ({fmt_count(n)})", "callback_data": f"c:y:{y}"}
             for y, n in db.years()]
    return _grid(items, 3, (*more_rows, [HOME]))


def months_markup(db, y, extra=()):
    """One button per month of `y`, 3 per row."""
    names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    items = [{"text": f"{names[m - 1]} ({fmt_count(n)})",
              "callback_data": f"c:m:{y}:{m}"} for m, n in db.months_of(y)]
    return _grid(items, 3, (*extra, [{"text": "⬅️ Years",
                                      "callback_data": "c:years"}, HOME]))


def _render(spec, page, db):
    """-> (markdown text, reply_markup or None) for one page of a stored view."""
    kind = spec["kind"]
    if kind == "search":
        fetch = lambda p: db.search_page(spec["terms"], p)
        title = "🔎 " + " ".join(spec["terms"])
        back = [{"text": "⬅️ Menu", "callback_data": "c:menu"}]
        prev_data, next_data = f"v:{page - 1}", f"v:{page + 1}"
    elif kind == "month":                       # a whole year-month window
        y, m = spec["y"], spec["m"]
        fetch = lambda p: db.by_date_page(y, m, None, p)
        title = f"🗓 {y}-{m:02d}"
        back = [{"text": "⬅️ Months", "callback_data": f"c:y:{y}"}, HOME]
        prev_data, next_data = f"m:{y}:{m}:{page - 1}", f"m:{y}:{m}:{page + 1}"
    else:                                       # "date": one exact day
        y, m, d = spec["y"], spec["m"], spec.get("d")
        fetch = lambda p: db.by_date_page(y, m, d, p)
        title = f"📅 {y}-{m:02d}" + (f"-{d:02d}" if d else "")
        back = [{"text": "⬅️ Date", "callback_data": "c:date"}, HOME]
        prev_data, next_data = f"d:{y}:{m}:{d or 0}:{page - 1}", f"d:{y}:{m}:{d or 0}:{page + 1}"

    rows, total = fetch(page)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if not 1 <= page <= pages:                  # stale button after a re-import
        page = min(max(page, 1), pages)
        rows, total = fetch(page)

    body = "\n\n".join(fmt_item(r) for r in rows) or "_(no media on this page)_"
    text = f"{title}\n{total:,} result(s) · page {page}/{pages}\n\n{body}"
    return text, _pager_rows(page, pages, prev_data, next_data,
                             extra=(*_download_rows(rows, current_chat.get()), back))


def _push(token, chat, text, markup=None, edit_of=None):
    """Send a screen, or edit it in place. Falls back to plain text on parse errors."""
    payload = {"chat_id": chat, "text": translate(text), "parse_mode": "Markdown", **NO_PREVIEW}
    if markup:
        payload["reply_markup"] = localize_markup(markup)

    if edit_of is not None:
        try:
            tg_call(token, "editMessageText", {**payload, "message_id": edit_of},
                    timeout=15)
            return edit_of
        except TgError as e:
            if "not modified" in e.description.lower():
                return edit_of
            # too old to edit / message gone -> fall through and send a new one
            print(f"edit failed ({e.description}) — resending", flush=True)

    try:
        sent = tg_call(token, "sendMessage", payload, timeout=15, tries=3)
    except TgError as e:
        if "parse" in e.description.lower():     # markdown fallback
            payload.pop("parse_mode", None)
            sent = tg_call(token, "sendMessage", payload, timeout=15, tries=3)
        else:
            raise
    return sent["message_id"]


def _send_view(token, chat, spec, page, db, edit_of=None):
    """Render one page of a stored spec and remember it for its buttons."""
    text, markup = _render(spec, page, db)
    message_id = _push(token, chat, text, markup, edit_of)
    _remember(chat, message_id, spec)
    return message_id


def _screen_menu(db):
    (total, uniq, y0, y1), _ = db.stats()
    head = f"{uniq:,} unique URLs" + (f" · {y0}–{y1}" if y0 else "")
    return ("Assalamu'alaikum 🕌 *haramain.info* media archive\n"
            f"{head}\n\n"
            "Pilih menu di bawah, atau pakai perintah langsung\n"
            "(/latest, /search, /date, /year, /stat, /language).\n"
            "Hasil pencarian muncul 5 per halaman — tombol ⬅️ Back / Next ➡️.",
            menu_markup())


def _screen_language():
    return ('🌐 Choose language / Pilih bahasa', _mk([
        [{'text': '🇬🇧 English', 'callback_data': 'lang:en'},
         {'text': '🇮🇩 Indonesia', 'callback_data': 'lang:id'}], [HOME]]))


def _screen_help(db):
    return help_text(), _mk([[HOME]])


def _screen_stat(db):
    (total, uniq, y0, y1), kinds = db.stats()
    names = {"audio": "🎵 audio", "video": "🎥 video"}
    lines = ["📊 *Archive statistics*",
             f"• total rows: {total:,}",
             f"• unique URLs: {uniq:,}"]
    lines += [f"• {names.get(k, k)}: {c:,}" for k, c in sorted(kinds.items())]
    if y0:
        lines.append(f"• period: {y0}–{y1}")
    return "\n".join(lines), _mk([[HOME]])


def _screen_latest(db, n=5):
    rows = db.latest(n)
    text = (f'🆕 *Latest {n}*\n\n' + '\n\n'.join(fmt_item(r) for r in rows)
            if rows else 'Archive is empty — import first: `python3 import_dump.py`')
    return text, _mk([*_download_rows(rows, current_chat.get()), [HOME]])


def _screen_search(db):
    return ("🔎 *Search*\n\n"
            "Kirim: `/search <kata>` — mis. `/search sudais fajr`\n"
            "Hasil 5 per halaman, geser pakai tombol Back/Next.",
            _mk([[{"text": "⬅️ Menu", "callback_data": "c:menu"}]]))


def _screen_date(db):
    return ("📅 *By date*\n\n"
            "Kirim: `/date YYYY-MM-DD` — mis. `/date 2026-10-28`\n"
            "Atau telusuri per bulan lewat tombol 🗓 Year.",
            _mk([[{"text": "🗓 Year", "callback_data": "c:years"}, HOME]]))


def _screen_years(db):
    years = db.years()
    if not years:
        return "Archive is empty — import first: `python3 import_dump.py`", _mk([[HOME]])
    (_, uniq, _, _), _kind = db.stats()
    text = (f"🗓 *Year*\n\n{uniq:,} URLs across {len(years)} years "
            f"({years[-1][0]}–{years[0][0]}).\nPilih tahun:")
    return text, years_markup(db)


def _screen_year(db, y):
    """Months of one year, with that year's audio/video totals as the header."""
    months = db.months_of(y)
    if not months:
        return f"No data for {y}.", years_markup(db)
    names = {"audio": "🎵 audio", "video": "🎥 video"}
    per = " · ".join(f"{names.get(k, k)}: {c:,}" for k, c in sorted(db.year_stats(y)))
    total = sum(n for _, n in months)
    return (f"🗓 *{y}* — {total:,} URLs\n{per}\nPilih bulan:",
            months_markup(db, y))


def _handle_callback(cb, db, token):
    """Every inline tap: menu screens, year/month drill-down, result paging."""
    data = cb.get("data") or ""
    msg = cb.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")

    def answer(text=None, alert=False):
        payload = {"callback_query_id": cb.get("id")}
        if text:
            payload.update({"text": translate(text), "show_alert": alert})
        try:
            tg_call(token, "answerCallbackQuery", payload)
        except TgError as e:
            print(f"answerCallbackQuery: {e}", flush=True)

    def screen():
        """(text, markup) for a c: tap, or None when it is a no-op."""
        if data == "c:language":
            return _screen_language()
        if data == "c:menu":
            return _screen_menu(db)
        if data == "c:help":
            return _screen_help(db)
        if data == "c:stat":
            return _screen_stat(db)
        if data in ("c:latest", "c:latest:refresh"):
            return _screen_latest(db)
        if data == "c:search":
            return _screen_search(db)
        if data == "c:date":
            return _screen_date(db)
        if data == "c:years":
            return _screen_years(db)
        if data.startswith("c:y:"):
            return _screen_year(db, int(data.rsplit(":", 1)[1]))
        return None

    if chat is None or message_id is None:
        answer()
        return

    if data.startswith('get:'):
        with _DOWNLOAD_LOCK:
            item = _DOWNLOADS.get(data[4:])
            valid = item and item['chat'] == chat and item['expires'] >= time.time()
        if not valid:
            answer(translate('Results have expired — run the command again.'), alert=True)
            return
        answer()
        note, _ = start_leech(token, chat, item['url'], None,
                              os.environ.get('LEECH_SPLIT_SIZE'),
                              os.environ.get('THUMB_PATH') or None,
                              False, to_self=False)
        send(token, chat, note)
        return

    if data in ("lang:en", "lang:id"):
        code = data.split(":")[1]
        preferences.set(chat, code)
        language.set(code)
        answer()
        _configure_menu(token, chat=chat, code=code,
                        private=(msg.get('chat') or {}).get('type') == 'private')
        text, markup = _screen_menu(db)
        _push(token, chat, text, markup, edit_of=message_id)
        return

    if data.startswith("c:"):
        if data == "c:x":                       # the page-counter button
            answer()
            return
        if data.startswith("c:m:"):             # c:m:Y:M -> first page of a month
            _, _, y, m = data.split(":")
            answer()
            spec = {"kind": "month", "y": int(y), "m": int(m)}
            try:
                _send_view(token, chat, spec, 1, db, edit_of=message_id)
            except TgError as e:
                print(f"month error: {e}", flush=True)
            return
        out = screen()
        answer()
        if out is not None:
            try:
                _push(token, chat, out[0], out[1], edit_of=message_id)
            except TgError as e:
                print(f"menu error: {e}", flush=True)
        return

    # Paging taps carry (year, month, exact-day) inline, so a month view can
    # move straight between "the month" and "one day of it" with no lookup.
    try:
        if data.startswith("m:"):                    # m:Y:M:page
            _, y, m, page = data.split(":")
            spec = {"kind": "month", "y": int(y), "m": int(m)}
        elif data.startswith("d:"):                  # d:Y:M:D:page  (D=0 -> none)
            _, y, m, d, page = data.split(":")
            spec = {"kind": "date", "y": int(y), "m": int(m), "d": int(d) or None}
        elif data.startswith("v:"):                  # v:page — search results
            _, page = data.split(":")
            spec = _recall(chat, message_id)
        else:
            answer()
            return
    except ValueError:
        answer()
        return
    if not str(page).isdigit():          # legacy/unknown tap -> just stop spinner
        answer()
        return

    if spec is None:
        answer("Hasil sudah kadaluarsa — jalankan perintah lagi.", alert=True)
        return
    answer()
    try:
        _send_view(token, chat, spec, int(page), db, edit_of=message_id)
    except TgError as e:
        print(f"view error: {e}", flush=True)


# --- leeching (/dl) -----------------------------------------------------------
def mask_url(url):
    """Short, safe label for a URL: host + file name, no query/token."""
    m = re.match(r"^[a-z]+://([^/?#]+)(?:/(.*))?$", url, re.I)
    if not m:
        return url.split("?", 1)[0][:40] + "..." if len(url) > 40 else url
    host, rest = m.group(1), m.group(2) or ""
    name = rest.rsplit("/", 1)[-1].split("?")[0]
    return f"{host}/.../{name}" if name else host


def prune_downloads():
    """Remove empty download directories left behind by earlier runs."""
    root = HERE / "downloads"
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir():
            try:
                child.rmdir()          # fails while anything is still inside
            except OSError:
                continue
    try:
        root.rmdir()
    except OSError:
        pass


def _run_leech(job, url, chat, name, split_size, thumb, as_doc, to_self):
    """Download and upload `url`, editing one status message as it goes.

    Runs on a worker thread: leech.py drives its own asyncio loop, so it must
    not run inside the bot's polling loop. leech.py calls sys.exit() when its
    deps are absent, hence SystemExit alongside ImportError.
    """
    try:
        from leech import cli as leech
    except (ImportError, SystemExit) as e:
        job["note"] = f"leech unavailable: {e}"
        _dl_finish(job)
        return

    def note(text):
        job["lines"].append(translate(text))
        del job["lines"][:-DL_LINES]
        _dl_status(job)

    try:
        files = leech.run_leech_sync(
            url, chat=chat, name=name, split_size=split_size, thumb=thumb,
            as_doc=as_doc, to_self=to_self, progress=note,
            target_dir=HERE / "downloads" / f"dl{int(job['started'])}")
        job["files"] = [str(f) for f in files]
    except Exception as e:                                  # noqa: BLE001
        job["error"] = str(e)
    finally:
        _dl_finish(job)


def _dl_text(job):
    head = f"⬇️ #{job['id']} · {mask_url(job['url'])}"
    body = "\n".join(job["lines"]) or "(starting...)"
    return f"{head}\n```\n{body}\n```"


def _dl_status(job):
    try:
        tg_call(job["token"], "editMessageText", {
            "chat_id": job["chat"], "message_id": job["message_id"],
            "text": _dl_text(job), "parse_mode": "Markdown",
            "link_preview_options": {"is_disabled": True}})
    except Exception:                                       # noqa: BLE001
        pass        # status is best-effort; the task keeps running


def _dl_finish(job):
    lines = list(job["lines"])
    if job["note"]:
        tail = f"❌ {job['note']}"
    elif job["error"]:
        tail = f"❌ failed: {job['error']}"
    elif job["files"]:
        names = ", ".join(Path(f).name for f in job["files"])
        tail = f"✅ done — {len(job['files'])} file(s): {names}"
    else:
        tail = "✅ done"
    send_long(job["token"], job["chat"], lines + ["", tail])
    try:
        tg_call(job["token"], "deleteMessage",
                {"chat_id": job["chat"], "message_id": job["message_id"]})
    except Exception:                                       # noqa: BLE001
        pass
    with job["lock"]:
        job["pool"].pop(job["id"], None)


_DL_POOL = {}
_DL_POOL_LOCK = threading.Lock()
_DL_IDS = itertools.count(1)      # monotonic: len(pool)+1 would reuse live ids


def start_leech(token, chat, url, name, split_size, thumb, as_doc, to_self):
    """Queue a leech; returns (status_text, started_bool)."""
    with _DL_POOL_LOCK:
        active = [j for j in _DL_POOL.values() if j["chat"] == chat]
        if len(active) >= DL_JOBS:
            return (f"⏳ {len(active)} downloads already running — "
                    f"try again shortly.", False)
        job = {"id": next(_DL_IDS), "token": token, "chat": chat,
               "url": url, "lines": [], "files": [], "error": "", "note": "",
               "started": time.time(), "lock": _DL_POOL_LOCK, "pool": _DL_POOL}
        _DL_POOL[job["id"]] = job
    try:
        msg = tg_call(token, "sendMessage", {
            "chat_id": chat,
            "text": translate(f"⏳ #{job['id']} starting · {mask_url(url)}"),
            "disable_notification": True,
            "link_preview_options": {"is_disabled": True}})
        job["message_id"] = msg["message_id"]
    except TgError as e:
        with _DL_POOL_LOCK:
            _DL_POOL.pop(job["id"], None)
        return (f"cannot post status: {e}", False)
    threading.Thread(target=copy_context().run,
                     args=(_run_leech, job, url, chat, name, split_size, thumb, as_doc, to_self),
                     daemon=True).start()
    return (f"⬇️ #{job['id']} started · {mask_url(url)}", True)


def help_text():
    return ("Assalamu'alaikum 🕌 *haramain.info* media archive bot.\n\n"
            "Tekan /start untuk menu tombol — semua perintah di bawah ini\n"
            "juga bisa dijalankan tanpa mengetik.\n\n"
            "• /latest [n] — n rekaman terbaru\n"
            "• /search <kata> — cari nama file (mis. `/search sudais fajr`)\n"
            "• /date YYYY-MM-DD — media satu tanggal\n"
            "• /year [yyyy] — telusuri per tahun → bulan\n"
            "• /stat — statistik arsip\n"
            "Klik tombol Unduh pada hasil arsip untuk menerima berkas.\n\n"
            "Hasil panjang tampil 5 per halaman dengan tombol ⬅️ Back / Next ➡️.")


def handle_update(upd, db, token):
    """Scope localization to this update, keeping chats independent."""
    msg = (upd.get('callback_query') or {}).get('message') or upd.get('message') or upd.get('edited_message') or {}
    chat = (msg.get('chat') or {}).get('id')
    context = language.set(preferences.get(chat) if chat is not None else 'id')
    chat_context = current_chat.set(chat)
    try:
        return _handle_update(upd, db, token)
    finally:
        language.reset(context)
        current_chat.reset(chat_context)


def _handle_update(upd, db, token):
    if upd.get("callback_query"):
        _handle_callback(upd["callback_query"], db, token)
        return

    msg = upd.get("message") or upd.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat", {}).get("id")
    if not text or chat is None:
        return

    parts = text.split()
    cmd = parts[0].split("@")[0].lower()   # /search@Botname -> /search
    args = parts[1:]

    if cmd in ("/language", "/lang"):
        text, markup = _screen_language()
        _push(token, chat, text, markup)
    elif cmd in ("/start", "/help"):
        if cmd == '/start':
            _configure_menu(token, chat=chat, code=language.get(),
                            private=msg.get('chat', {}).get('type') == 'private')
        out = _screen_menu(db) if cmd == "/start" else _screen_help(db)
        _push(token, chat, out[0], out[1])
    elif cmd == "/latest":
        n = min(int(args[0]) if args and args[0].isdigit() else 5, 10)
        out = _screen_latest(db, n)
        _push(token, chat, out[0], out[1])
    elif cmd == "/date":
        m = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?", args[0] if args else "")
        if not m:
            out = _screen_date(db)
            _push(token, chat, out[0], out[1])
            return
        spec = {"kind": "date", "y": int(m[1]), "m": int(m[2]),
                "d": int(m[3]) if m[3] else None}
        if not db.by_date_page(spec["y"], spec["m"], spec["d"])[1]:
            send(token, chat, f"No media found for {m[0]}.")
            return
        _send_view(token, chat, spec, 1, db)
    elif cmd == "/search":
        if not args:
            out = _screen_search(db)
            _push(token, chat, out[0], out[1])
            return
        spec = {"kind": "search", "terms": [a.lower() for a in args]}
        if not db.search_page(spec["terms"])[1]:
            send(token, chat, "No results. Try other keywords.")
            return
        _send_view(token, chat, spec, 1, db)
    elif cmd == "/year":
        y = int(args[0]) if args and args[0].isdigit() else None
        out = _screen_year(db, y) if y else _screen_years(db)
        _push(token, chat, out[0], out[1])
    elif cmd == "/stat":
        out = _screen_stat(db)
        _push(token, chat, out[0], out[1])
    else:
        send(token, chat, "Unknown command. Use /help for the list.")


def main():
    load_env()
    token = os.environ.get("BOT_TOKEN", "")
    db_path = os.environ.get("DB_PATH", str(DEFAULT_DB))
    poll = float(os.environ.get("POLL_SEC", "3"))
    lp_timeout = int(os.environ.get("LONGPOLL_SEC", "25"))

    if not token:
        sys.exit("BOT_TOKEN missing. Fill in .env or export BOT_TOKEN=...")
    if not os.path.exists(db_path):
        sys.exit(f"DB not found: {db_path}\n"
                 "Run first: python3 import_dump.py --dump ../haramain")

    db = Archive(db_path)
    (total, uniq, y0, y1), _ = db.stats()
    print(f"db: {total} rows, {uniq} unique, years {y0}-{y1}")
    prune_downloads()
    _configure_menu(token)

    offset = 0            # next getUpdates offset; updates below it are confirmed
    print("polling...")
    while True:
        try:
            # requests timeout must exceed the server-side long-poll wait
            updates = tg_call(token, "getUpdates", {
                "offset": offset, "timeout": lp_timeout,
                "allowed_updates": ["message", "callback_query"]},
                timeout=lp_timeout + 15)
        except KeyboardInterrupt:
            print("bye")
            return
        except TgError as e:
            # honor retry_after, but never spin faster than POLL_SEC
            wait = max(e.retry_after, poll) if e.retry_after is not None else poll
            if e.error_code == 401:
                sys.exit(f"unauthorized: {e.description}")
            print(f"poll error: {e} — retry in {wait:.0f}s", flush=True)
            time.sleep(wait)
            continue
        except Exception as e:
            print(f"poll error: {e} — retry in {poll}s", flush=True)
            time.sleep(poll)
            continue

        for up in updates:
            # confirm the update BEFORE handling: a crash mid-handle must not
            # replay it forever
            offset = up["update_id"] + 1
            try:
                handle_update(up, db, token)
            except KeyboardInterrupt:
                print("bye")
                return
            except TgError as e:
                if e.migrate_chat_id:
                    print(f"chat migrated -> {e.migrate_chat_id}", flush=True)
                else:
                    print(f"send error (update {up['update_id']}): {e}", flush=True)
            except Exception as e:
                print(f"handle error: {e}", flush=True)


if __name__ == "__main__":
    main()
