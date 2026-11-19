#!/usr/bin/env python3
"""Stage 1 -- ground truth: fetch the sitemap and extract every post URL.

Usage:
    python3 sitemap_posts.py [--site URL] [-o posts.txt]

Feeds cap depth and silently omit old posts, so the sitemap list is the
denominator every later coverage claim is measured against.
"""
import argparse
import os
from pathlib import Path
import tempfile
import re
import sys
import urllib.request

SITEMAP_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
POST_RE = re.compile(r"\.(?:html?|php)/?$", re.I)


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="http://www.haramain.info",
                    help="site root (default: haramain.info)")
    ap.add_argument("-o", "--out", default="haramain_posts.txt")
    args = ap.parse_args()

    roots = [f"{args.site.rstrip('/')}/sitemap.xml"]
    seen_roots, posts = set(), []

    while roots:                                    # sitemap index -> children
        xml = fetch(roots.pop(0))
        locs = SITEMAP_RE.findall(xml)
        if "<sitemapindex" in xml.lower():
            for l in locs:
                if l not in seen_roots:
                    seen_roots.add(l)
                    roots.append(l)
        else:
            for l in locs:
                if POST_RE.search(l):
                    posts.append(l)

    posts = list(dict.fromkeys(posts))               # dedupe, keep order
    if not posts:
        sys.exit("no posts found -- check --site / sitemap path")
    out = Path(args.out)
    with tempfile.TemporaryDirectory(prefix=".sitemap-", dir=out.parent) as directory:
        stage = Path(directory) / "posts.txt"
        stage.write_text("\n".join(posts) + "\n", encoding="utf-8")
        os.replace(stage, out)
    print(f"post urls: {len(posts)} -> {out}")


if __name__ == "__main__":
    main()
