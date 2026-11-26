"""Read-only SQLite queries for the media archive."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

PAGE_SIZE = 5


def database_usable(path):
    """True when the read-only database has all columns the bot queries."""
    target = Path(path)
    try:
        with closing(
            sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(media)")}
            required = {name.strip() for name in Archive.COLS.split(",")}
            if not required <= columns:
                return False
            db.execute("SELECT COUNT(*) FROM media").fetchone()
        return True
    except (OSError, sqlite3.Error, ValueError):
        return False


class Archive:
    """Read-only view over the SQLite dump produced by import_dump.py."""

    COLS = "url, post, kind, year, month, day, slug, fname"

    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self._stats_cache = None

    def _precomputed(self):
        """Aggregates written by import_dump.py; None for older databases."""
        try:
            row = self.db.execute("SELECT v FROM meta WHERE k='stats'").fetchone()
        except sqlite3.OperationalError:
            return None
        if not row or not row[0]:
            return None
        try:
            payload = json.loads(row[0])
            payload["total"] = int(payload["total"])
            payload["unique"] = int(payload["unique"])
        except (KeyError, TypeError, ValueError):
            return None
        return payload

    def stats(self):
        version = self.db.execute("PRAGMA data_version").fetchone()[0]
        cached = self._stats_cache
        if cached is not None and cached[0] == version:
            totals, by_kind = cached[1]
            return totals, dict(by_kind)

        payload = self._precomputed()
        if payload is not None:
            totals = (
                payload["total"],
                payload["unique"],
                payload.get("min_year"),
                payload.get("max_year"),
            )
            by_kind = dict(payload.get("kinds") or {})
        else:
            self.db.execute("BEGIN")
            try:
                totals = self.db.execute(
                    "SELECT COUNT(*), COUNT(DISTINCT url), MIN(year), MAX(year) FROM media"
                ).fetchone()
                by_kind = dict(
                    self.db.execute(
                        "SELECT kind, COUNT(DISTINCT url) FROM media GROUP BY kind"
                    ).fetchall()
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

        self._stats_cache = (version, (totals, by_kind))
        return totals, dict(by_kind)

    def built(self):
        """UTC timestamp of the last import, or None when it is unknown."""
        try:
            row = self.db.execute("SELECT v FROM meta WHERE k='built'").fetchone()
        except sqlite3.OperationalError:
            return None
        return row[0] if row else None

    def latest(self, n=5):
        return self.db.execute(
            f"SELECT {self.COLS} FROM media "
            "ORDER BY year DESC, month DESC, day DESC, id DESC LIMIT ?",
            (n,),
        ).fetchall()

    def videos_for(self, post):
        """YouTube references for a post; empty when the dump predates them."""
        if not post:
            return []
        try:
            rows = self.db.execute(
                "SELECT url FROM video WHERE post=? ORDER BY url", (post,)
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [row[0] for row in rows]

    # --- paged variants: (rows, total) so a caller can show "page i/n" ---
    def by_date_page(self, y, m, d=None, page=1):
        where, args = "year=? AND month=?", [y, m]
        if d:
            where, args = where + " AND day=?", args + [d]
        total = self.db.execute(
            f"SELECT COUNT(*) FROM media WHERE {where}", args
        ).fetchone()[0]
        rows = self.db.execute(
            f"SELECT {self.COLS} FROM media WHERE {where} ORDER BY id LIMIT ? OFFSET ?",
            args + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()
        return rows, total

    def search_page(self, terms, page=1):
        where = ["(slug LIKE ? OR fname LIKE ?)"] * len(terms)
        args = [f"%{t}%" for t in terms for _ in (0, 1)]
        cond = " AND ".join(where)
        total = self.db.execute(
            f"SELECT COUNT(*) FROM media WHERE {cond}", args
        ).fetchone()[0]
        rows = self.db.execute(
            f"SELECT {self.COLS} FROM media WHERE {cond} LIMIT ? OFFSET ?",
            args + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()
        return rows, total

    def year_stats(self, y):
        return self.db.execute(
            "SELECT kind, COUNT(DISTINCT url) FROM media WHERE year=? GROUP BY kind",
            (y,),
        ).fetchall()

    # --- year browser: years -> months -> paged items ---
    def years(self):
        """[(year, url count)] newest first."""
        payload = self._precomputed()
        if payload is not None and payload.get("years") is not None:
            return [(int(year), int(number)) for year, number in payload["years"]]
        return [
            (row[0], row[1])
            for row in self.db.execute(
                "SELECT year, COUNT(DISTINCT url) FROM media "
                "WHERE year IS NOT NULL GROUP BY year ORDER BY year DESC"
            )
        ]

    def months_of(self, y):
        """[(month, url count)] for one year, oldest first."""
        return self.db.execute(
            "SELECT month, COUNT(DISTINCT url) FROM media "
            "WHERE year=? AND month IS NOT NULL GROUP BY month ORDER BY month",
            (y,),
        ).fetchall()
