"""Curated IMAAMS Quran recitations catalog backed by Archive.org metadata."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from contextlib import closing
from pathlib import Path

# http://www.haramain.info/ — IMAAMS QU'RAN RECITATIONS.
# 39 source links / 38 unique identifiers (Dossary is linked twice).
# Distinct legacy items keep their source names. Audit: .cache/imaams-source.json.
RECITERS = [
    {"name": "Sheikh Abdullah Quraafi", "identifier": "HaramainQuraafi"},
    {"name": "Sheikh Badr Al Turki", "identifier": "HaramainTurki"},
    {"name": "Sheikh Muhammad Burhaji", "identifier": "HaramainBurhaji"},
    {"name": "Sheikh Salaah Ba Uthmaan", "identifier": "HaramainBaUthman"},
    {"name": "Sheikh Yasser Al Dossary", "identifier": "HaramainDossary"},
    {"name": "Sheikh Maher Al Muayqali", "identifier": "HaramainMaahir"},
    {"name": "Sheikh Abdullah Buayjaan", "identifier": "HaramainBuayjaan"},
    {"name": "Sheikh Ahmad Taleb Hameed", "identifier": "HaramainHameed"},
    {"name": "Sheikh Bander Baleelah", "identifier": "HaramainBaleelah"},
    {"name": "Sheikh Abdul Bari Ath Thubaiti", "identifier": "HaramainThubaiti"},
    {"name": "Sheikh Abdul Muhsin Al Qasim", "identifier": "HaramainQasim"},
    {"name": "Sheikh Abdul Rahman As Sudais", "identifier": "HaramainSudais"},
    {"name": "Sheikh Abdul Wadood Hanif", "identifier": "HaramainHanif"},
    {"name": "Sheikh Abdullah Awaad Al Juhany", "identifier": "HaramainJuhany"},
    {"name": "Sheikh Abdullah Khayaat", "identifier": "HaramainKhayaaat"},
    {"name": "Sheikh Abdullah Khulayfee", "identifier": "HaramainKhulayfee"},
    {"name": "Sheikh Adil Kalbani", "identifier": "HaramainKalbani"},
    {"name": "Sheikh Ahmad Al Hudhayfee", "identifier": "HaramainHudhayfeee"},
    {"name": "Sheikh Ali Al Hudhayfee", "identifier": "HaramainHudhayfee"},
    {"name": "Sheikh Ali Jaber", "identifier": "HaramainAliJaber"},
    {"name": "Sheikh Faysal Ghazzawi", "identifier": "HaramainGhazzawi"},
    {"name": "Sheikh Hussayn Aal Sheikh", "identifier": "HaramainSheikh"},
    {"name": "Sheikh Ibraheem Al Akhdhar", "identifier": "HaramainAkhdhar"},
    {"name": "Sheikh Imaad Zuhair Hafiz", "identifier": "HaramainHafiz"},
    {"name": "Sheikh Khalid Al Ghamdi", "identifier": "HaramainGhamdi"},
    {"name": "Sheikh Khalid Al Muhanna", "identifier": "HaramainMuhanna"},
    {"name": "Sheikh Maher Al Muayqali", "identifier": "HaramainMaher"},
    {"name": "Sheikh Mahmood Khaleel Al Qari", "identifier": "HaramainKQari"},
    {"name": "Sheikh Muhammad Ayub", "identifier": "HaramainAyub"},
    {"name": "Sheikh Muhammad Khaleel Al Qari", "identifier": "HaramainMQari"},
    {"name": "Sheikh Muhammad Subbayil", "identifier": "HaramainSubbayil"},
    {"name": "Sheikh Saad Al Ghamdi", "identifier": "HaramainGhamdii"},
    {"name": "Sheikh Salaah Al Budair", "identifier": "HaramainBudair"},
    {"name": "Sheikh Salaah Ba Uthmaan", "identifier": "HaramainBaUthmaan"},
    {"name": "Sheikh Saleh Aal Taleb", "identifier": "HaramainTaleb"},
    {"name": "Sheikh Saleh Bin Humaid", "identifier": "HaramainHumaid"},
    {"name": "Sheikh Saud Ash Shuraim", "identifier": "HaramainShuraim"},
    {"name": "Sheikh Usaamah Khayaat", "identifier": "HaramainKhayaat"},
]
RECITER_BY_ID = {item["identifier"]: item for item in RECITERS}
for _reciter in RECITERS:
    _reciter["url"] = f"https://archive.org/details/{_reciter['identifier']}"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{4,99}$")
_MAX_METADATA_BYTES = 8 * 1024 * 1024
_CACHE_TTL = 6 * 60 * 60
_RETRY_COOLDOWN = 300


def archive_download_url(identifier, filename):
    if (not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier)
            or not isinstance(filename, str) or not filename):
        raise ValueError("invalid Archive.org item or filename")
    if ("\\" in filename or any(ord(char) < 32 or ord(char) == 127 for char in filename)
            or any(part in ("", ".", "..") for part in filename.split("/"))
            or re.search(r"%(?:2e|2f|5c|25|00)", filename, re.IGNORECASE)):
        raise ValueError("invalid Archive.org filename")
    return "https://archive.org/download/{}/{}".format(
        urllib.parse.quote(identifier, safe=""), urllib.parse.quote(filename, safe="/"))


def _fetch_metadata(identifier):
    request = urllib.request.Request(
        f"https://archive.org/metadata/{urllib.parse.quote(identifier, safe='')}",
        headers={"User-Agent": "haramain-bot/imaams"})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(_MAX_METADATA_BYTES + 1)
        if len(body) > _MAX_METADATA_BYTES:
            raise ValueError("Archive.org metadata response too large")
        return json.loads(body.decode("utf-8"))


class ImaamsCatalog:
    """Separate SQLite metadata cache; stale data survives bounded API outages."""
    def __init__(self, cache_path, fetcher=None):
        self.path = Path(cache_path)
        self.fetcher = fetcher or _fetch_metadata
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (identifier TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS retries (identifier TEXT PRIMARY KEY, retry_after REAL NOT NULL)")

    def metadata(self, identifier, max_age=_CACHE_TTL):
        if identifier not in RECITER_BY_ID:
            raise ValueError("unknown reciter")
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                "SELECT payload, fetched "
                "FROM metadata WHERE identifier=?", (identifier,)).fetchone()
            retry = db.execute("SELECT retry_after FROM retries WHERE identifier=?", (identifier,)).fetchone()
        now = time.time()
        cached = self._decode_cached(row)
        if cached is None:
            row = None
        if row and now - row[1] < max_age:
            return cached
        if retry and retry[0] > now:
            if row:
                return cached
            raise RuntimeError("Archive.org temporarily unavailable; retry later")
        try:
            payload = self.fetcher(identifier)
            self._validate_metadata(payload)
        except Exception:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("INSERT OR REPLACE INTO retries VALUES (?, ?)",
                           (identifier, time.time() + _RETRY_COOLDOWN))
            if row:
                return cached
            raise
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?, ?)",
                       (identifier, json.dumps(payload, ensure_ascii=False), now))
            db.execute("DELETE FROM retries WHERE identifier=?", (identifier,))
        return payload

    @staticmethod
    def _validate_metadata(payload):
        if not isinstance(payload, dict):
            raise ValueError("invalid Archive.org metadata")
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        if payload.get("is_dark"):
            raise RuntimeError("Archive.org item unavailable")
        if not isinstance(payload.get("files"), list):
            raise ValueError("invalid Archive.org metadata files")

    @classmethod
    def _decode_cached(cls, row):
        if row:
            try:
                payload = json.loads(row[0])
                cls._validate_metadata(payload)
                return payload
            except (ValueError, TypeError, RuntimeError):
                pass
        return None

    def files(self, identifier):
        payload = self.metadata(identifier)
        return self._audio_files(identifier, payload)

    def cached_files(self, identifier, max_age=_CACHE_TTL):
        """Return cached audio metadata only when its age is within max_age."""
        if identifier not in RECITER_BY_ID:
            raise ValueError("unknown reciter")
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                "SELECT payload, fetched "
                "FROM metadata WHERE identifier=?", (identifier,)).fetchone()
        if not row or time.time() - row[1] >= max_age:
            return None
        payload = self._decode_cached(row)
        return self._audio_files(identifier, payload) if payload is not None else None

    @staticmethod
    def _audio_files(identifier, payload):
        files = []
        for item in payload.get("files", []):
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            if (not isinstance(name, str) or item.get("source") != "original"
                    or not name.lower().endswith((".mp3", ".m4a", ".ogg", ".wav"))):
                continue
            try:
                url = archive_download_url(identifier, name)
            except ValueError:
                continue
            audio = {**item, "url": url}
            title = item.get("title")
            audio["title"] = title.strip() if isinstance(title, str) and title.strip() else Path(name).stem
            for field in ("length", "size"):
                value = item.get(field)
                try:
                    number = float(value) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else -1
                    valid = math.isfinite(number) and number >= 0
                except (ValueError, OverflowError):
                    valid = False
                if not valid:
                    audio.pop(field, None)
            files.append(audio)
        def order(item):
            track = str(item.get("track", "")).split("/", 1)[0].strip()
            if len(track) <= 9 and track.isascii() and track.isdigit() and int(track) > 0:
                number = int(track)
            else:
                match = re.search(r"[0-9]+", Path(item["name"]).stem)
                number = int(match.group()) if match and len(match.group()) <= 9 else math.inf
            return number, item["name"]
        return sorted(files, key=order)
