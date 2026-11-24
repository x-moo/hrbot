#!/usr/bin/env python3
"""Import a dump tree (built by build_dump.py) into SQLite for the bot.

    python3 import_dump.py --dump ../haramain --db archive.db

Primary source: <dump>/csv/catalog.csv  (year,kind,url,host,source_post)
Fallback      : <dump>/years/*/*_with_source.tsv

DB schema: one `media` table (url, post, kind, year, month, day, slug, fname)
plus a `meta` table (row count, import time). ~100K rows -> a few MB.
"""

import argparse
import csv
import datetime
import glob
import itertools
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

YMD_RE = re.compile(r"/(\d{4})/(\d{2})/([^/]+?)\.html?$")


def parse_post(post_url):
    """Post URL -> (year, month, day, slug); all None/'' if no match.

    'day' is the numeric day found in the post slug (e.g. 'madeenah-isha-
    18th-october-2026' -> 18); None when the slug carries no number.
    """
    m = YMD_RE.search(post_url)
    if not m:
        return None, None, None, ""
    slug = m.group(3)
    dm = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\b", slug)
    day = int(dm.group(1)) if dm else None
    return int(m.group(1)), int(m.group(2)), day, slug


def fname_of(url):
    path = url.split("?", 1)[0].split("#", 1)[0]
    return path.rsplit("/", 1)[-1].lower()


def collect_rows(dump_dir):
    """Yield (url, post, kind) from catalog.csv, or per-year TSVs as fallback."""
    catalog = os.path.join(dump_dir, "csv", "catalog.csv")
    if os.path.exists(catalog):
        with open(catalog, newline="", encoding="utf-8", errors="ignore") as f:
            for r in csv.reader(f):
                # header: year,kind,url,host,source_post -> skipped via r[1]
                if len(r) >= 3 and r[1] in ("audio", "video"):
                    yield r[2], (r[4] if len(r) > 4 else ""), r[1]
        return
    for tsv in sorted(
        glob.glob(os.path.join(dump_dir, "years", "*", "*_with_source.tsv"))
    ):
        kind = "audio" if "audio" in os.path.basename(tsv) else "video"
        with open(tsv, encoding="utf-8", errors="ignore") as f:
            for line in f:
                c = line.rstrip("\n").split("\t")
                if len(c) == 2:
                    yield c[0], c[1], kind


def import_catalog(dump_dir, database):
    """Publish rows and metadata in one transaction without replacing the DB inode.

    WAL readers continue seeing the old catalog until commit; exceptions roll
    back DELETE/INSERT/metadata together. Keeping the file (rather than rename)
    also keeps long-lived bot connections attached to the refreshed database.
    """
    rows = iter(collect_rows(dump_dir))
    first = next(rows, None)
    if first is None:
        raise ValueError(f"no media rows found. Check {dump_dir}/csv/catalog.csv")
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=30)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY, url TEXT NOT NULL, post TEXT NOT NULL,
                kind TEXT, year INT, month INT, day INT, slug TEXT, fname TEXT)""")
            db.execute("DELETE FROM media")
            count, batch = 0, []
            for url, post, kind in itertools.chain((first,), rows):
                if not url or not url.lower().startswith(("http://", "https://")):
                    raise ValueError("catalog contains an invalid media URL")
                y, month, day, slug = parse_post(post)
                batch.append((url, post, kind, y, month, day, slug, fname_of(url)))
                count += 1
                if len(batch) >= 5000:
                    db.executemany(
                        "INSERT INTO media VALUES (NULL,?,?,?,?,?,?,?,?)", batch
                    )
                    batch.clear()
            if batch:
                db.executemany("INSERT INTO media VALUES (NULL,?,?,?,?,?,?,?,?)", batch)
            for name, columns in (
                ("slug", "slug"),
                ("ymd", "year, month, day"),
                ("kind", "kind"),
            ):
                db.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_media_{name} ON media({columns})"
                )
            db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            built = datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            )
            db.executemany(
                "INSERT OR REPLACE INTO meta VALUES (?,?)",
                [("rows", str(count)), ("built", built)],
            )
            unique = db.execute("SELECT COUNT(DISTINCT url) FROM media").fetchone()[0]
            db.commit()
        except BaseException:
            db.rollback()
            raise
        # Do not force WAL truncation while other read transactions are active.
        db.execute("PRAGMA wal_checkpoint(PASSIVE)")
    return {"rows": count, "unique_urls": unique, "built": built}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dump", required=True, help="dump dir (from build_dump.py)")
    parser.add_argument("--db", default="archive.db", help="output sqlite path")
    args = parser.parse_args()
    try:
        result = import_catalog(args.dump, args.db)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"import failed: {exc}\n")
    print(
        f"import done: {result['rows']} rows ({result['unique_urls']} unique urls) -> {args.db}",
        flush=True,
    )


if __name__ == "__main__":
    main()
