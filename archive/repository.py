"""Read-only SQLite queries for the media archive."""
import sqlite3

PAGE_SIZE = 5


class Archive:
    """Read-only view over the SQLite dump produced by import_dump.py."""

    COLS = "url, post, kind, year, month, day, slug, fname"

    def __init__(self, path):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self._stats_cache = None

    def stats(self):
        version = self.db.execute("PRAGMA data_version").fetchone()[0]
        cached = self._stats_cache
        if cached is not None and cached[0] == version:
            totals, by_kind = cached[1]
            return totals, dict(by_kind)

        self.db.execute("BEGIN")
        try:
            totals = self.db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT url), MIN(year), MAX(year) FROM media"
            ).fetchone()
            by_kind = dict(self.db.execute(
                "SELECT kind, COUNT(DISTINCT url) FROM media GROUP BY kind"
            ).fetchall())
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        self._stats_cache = (version, (totals, by_kind))
        return totals, dict(by_kind)

    def latest(self, n=5):
        return self.db.execute(
            f"SELECT {self.COLS} FROM media "
            "ORDER BY year DESC, month DESC, day DESC, id DESC LIMIT ?", (n,)
        ).fetchall()

    # --- paged variants: (rows, total) so a caller can show "page i/n" ---
    def by_date_page(self, y, m, d=None, page=1):
        where, args = "year=? AND month=?", [y, m]
        if d:
            where, args = where + " AND day=?", args + [d]
        total = self.db.execute(
            f"SELECT COUNT(*) FROM media WHERE {where}", args).fetchone()[0]
        rows = self.db.execute(
            f"SELECT {self.COLS} FROM media WHERE {where} "
            "ORDER BY id LIMIT ? OFFSET ?",
            args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
        return rows, total

    def search_page(self, terms, page=1):
        where = ["(slug LIKE ? OR fname LIKE ?)"] * len(terms)
        args = [f"%{t}%" for t in terms for _ in (0, 1)]
        cond = " AND ".join(where)
        total = self.db.execute(
            f"SELECT COUNT(*) FROM media WHERE {cond}", args).fetchone()[0]
        rows = self.db.execute(
            f"SELECT {self.COLS} FROM media WHERE {cond} LIMIT ? OFFSET ?",
            args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
        return rows, total

    def year_stats(self, y):
        return self.db.execute(
            "SELECT kind, COUNT(DISTINCT url) FROM media WHERE year=? GROUP BY kind", (y,)
        ).fetchall()

    # --- year browser: years -> months -> paged items ---
    def years(self):
        """[(year, url count)] newest first."""
        return self.db.execute(
            "SELECT year, COUNT(DISTINCT url) FROM media "
            "WHERE year IS NOT NULL GROUP BY year ORDER BY year DESC").fetchall()

    def months_of(self, y):
        """[(month, url count)] for one year, oldest first."""
        return self.db.execute(
            "SELECT month, COUNT(DISTINCT url) FROM media "
            "WHERE year=? AND month IS NOT NULL GROUP BY month ORDER BY month",
            (y,)).fetchall()
