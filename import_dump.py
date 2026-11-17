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
import os
import re
import sqlite3
import sys

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
    for tsv in sorted(glob.glob(os.path.join(dump_dir, "years", "*", "*_with_source.tsv"))):
        kind = "audio" if "audio" in os.path.basename(tsv) else "video"
        with open(tsv, encoding="utf-8", errors="ignore") as f:
            for line in f:
                c = line.rstrip("\n").split("\t")
                if len(c) == 2:
                    yield c[0], c[1], kind


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", required=True, help="dump dir (from build_dump.py)")
    ap.add_argument("--db", default="archive.db", help="output sqlite path")
    args = ap.parse_args()

    rows = list(collect_rows(args.dump))
    if not rows:
        sys.exit(f"no media rows found. Check {args.dump}/csv/catalog.csv")

    db = sqlite3.connect(args.db)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY,
        url TEXT NOT NULL,
        post TEXT NOT NULL,
        kind TEXT,
        year INT, month INT, day INT,
        slug TEXT, fname TEXT)""")
    db.execute("DELETE FROM media")

    now = datetime.datetime.now().isoformat(timespec="seconds")
    n, batch = 0, []
    for url, post, kind in rows:
        y, mo, d, slug = parse_post(post)
        n += 1
        batch.append((url, post, kind, y, mo, d, slug, fname_of(url)))
        if len(batch) >= 5000:
            db.executemany("INSERT INTO media VALUES (NULL,?,?,?,?,?,?,?,?)", batch)
            batch = []
            print(f"  {n} rows...", flush=True)
    if batch:
        db.executemany("INSERT INTO media VALUES (NULL,?,?,?,?,?,?,?,?)", batch)

    db.execute("CREATE INDEX IF NOT EXISTS idx_media_slug ON media(slug)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_media_ymd ON media(year, month, day)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_media_kind ON media(kind)")
    db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    db.execute("INSERT OR REPLACE INTO meta VALUES ('rows', ?)", (str(n),))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('built', ?)", (now,))
    db.commit()

    uniq = db.execute("SELECT COUNT(DISTINCT url) FROM media").fetchone()[0]
    print(f"import done: {n} rows ({uniq} unique urls) -> {args.db}")
    print(f"db size: {os.path.getsize(args.db) / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()