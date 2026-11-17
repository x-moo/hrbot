#!/usr/bin/env python3
"""Stage 2 -- structured bulk route: dump the Blogger RSS feed.

Subcommands:
    global  Paginate /feeds/posts/default from start-index=1 by page size
            until a short page (cheap first pass over the whole archive).
    fill    Reconcile against the sitemap list, find the months still missing
            posts, and re-fetch ONLY those month windows (published-min/max).

Usage:
    python3 feed_dump.py global -d feeds/ [--site URL] [--page-size 150]
    python3 feed_dump.py fill   -d feeds/ --posts posts.txt [--page-size 150] [-j 4]
"""
import argparse
import concurrent.futures as cf
import os
import re
import time
import urllib.parse
import urllib.request

ITEM_RE = re.compile(r"<item>", re.I)
LINK_RE = re.compile(r"<link>\s*([^<]+?)\s*</link>", re.I)
MONTH_RE = re.compile(r"/(\d{4})/(\d{2})/")


def fetch(url, timeout=90, tries=3):
    """GET a text URL with retries; raise RuntimeError if every try fails."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(3 + i * 4)
    raise RuntimeError(f"fetch failed after {tries} tries: {url} ({last})")


def feed_url(site, page_size, start, pmin=None, pmax=None):
    q = {"alt": "rss", "max-results": page_size, "start-index": start}
    if pmin:
        q["published-min"] = pmin
    if pmax:
        q["published-max"] = pmax
    return f"{site}/feeds/posts/default?{urllib.parse.urlencode(q)}"


def save(path, xml):
    """Write atomically; return the number of items on this page."""
    n = len(ITEM_RE.findall(xml))
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(xml)
    os.replace(tmp, path)
    return n


def cmd_global(args):
    os.makedirs(args.dir, exist_ok=True)
    start, pages, items = 1, 0, 0
    while start <= args.max_index:
        out = os.path.join(args.dir, f"f_{start:06d}.xml")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            n = len(ITEM_RE.findall(open(out, errors="ignore").read()))
        else:
            xml = fetch(feed_url(args.site, args.page_size, start))
            n = save(out, xml)
            pages, items = pages + 1, items + n
            print(f"  page start={start} items={n}", flush=True)
        if n < args.page_size:
            break
        start += args.page_size
    print(f"global: {pages} pages fetched, {items} items, dir={args.dir}")


def month_range(ym):
    y, m = (int(x) for x in ym.split("-"))
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return (f"{y}-{m:02d}-01T00:00:00Z", f"{ny}-{nm:02d}-01T00:00:00Z")


def cmd_fill(args):
    posts = [l.strip() for l in open(args.posts) if l.strip()]
    have = set()
    for fn in os.listdir(args.dir):
        if fn.endswith(".xml"):
            t = open(os.path.join(args.dir, fn), errors="ignore").read()
            have.update(m.group(1).strip() for m in LINK_RE.finditer(t))
    missing = [p for p in posts if p not in have]
    months = sorted({f"{m.group(1)}-{m.group(2)}" for p in missing
                     if (m := MONTH_RE.search(p))})
    print(f"missing posts: {len(missing)} across {len(months)} months")

    def fill_month(ym):
        pmin, pmax = month_range(ym)
        start, pages = 1, 0
        while start <= args.max_index:
            out = os.path.join(args.dir, f"{ym.replace('-', '')}_p{start:05d}.xml")
            if os.path.exists(out) and os.path.getsize(out) > 0:
                n = len(ITEM_RE.findall(open(out, errors="ignore").read()))
            else:
                n = save(out, fetch(feed_url(args.site, args.page_size, start, pmin, pmax)))
                pages += 1
                time.sleep(1)
            if n < args.page_size:
                break
            start += args.page_size
        return ym, pages

    ok = 0
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for ym, pages in ex.map(fill_month, months):
            ok += 1
            print(f"  {ym}: {pages} pages", flush=True)
    print(f"fill: {ok}/{len(months)} months done, dir={args.dir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--site", default="http://www.haramain.info")
    common.add_argument("-d", "--dir", required=True, help="output dir for .xml")
    common.add_argument("--page-size", type=int, default=150)
    common.add_argument("--max-index", type=int, default=100000)

    g = sub.add_parser("global", parents=[common], help="paginate whole archive")
    g.set_defaults(func=cmd_global)

    f = sub.add_parser("fill", parents=[common],
                       help="fetch only months still missing posts")
    f.add_argument("--posts", required=True, help="sitemap posts list")
    f.add_argument("-j", "--jobs", type=int, default=4)
    f.set_defaults(func=cmd_fill)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
