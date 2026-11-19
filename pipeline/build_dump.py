#!/usr/bin/env python3
"""Stage 4 -- assemble the tidy dump tree from all collection routes.

    python3 build_dump.py --posts posts.txt --feeds feeds/ --feeds feeds2/ \
        --page-tsv missing_post_media.tsv --out haramain/

Inputs (posts required; feeds and scraped rows may be combined):
  --posts      sitemap list of every post URL (the ground truth)
  --feeds      dir(s) of Blogger feed .xml batches (repeatable)
  --page-tsv   rows scraped straight from post pages: url<TAB>post

Output layout:
    <out>/years/<YYYY>/{sources,audio_urls,video_urls}.txt
                       {audio,video}_with_source.tsv
    <out>/index/{all_posts,all_audio_urls,all_video_urls,
                 posts_without_media,hosts,video_hosts}.txt
    <out>/csv/catalog.csv       year,type,url,host,source_post
    <out>/README.md             coverage + per-year table
"""
import argparse
import collections
import glob
import html
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.common import (DISALLOWED, MEDIA_RE, classify, host_of,  # noqa: E402
                             is_bad_host, read_lines, year_of)

ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
LINK_RE = re.compile(r"<link>(.*?)</link>", re.S)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
PUB_RE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)
MONTH_PAGE_RE = re.compile(r"[0-9]{6}_p[0-9]+\.xml")


def parse_feed_files(dirs):
    """-> (posts_seen, audio_by_post, video_by_post, items_scanned)."""
    posts_seen = {}
    audio, video = collections.defaultdict(list), collections.defaultdict(list)
    items = 0
    files = []
    for d in dirs:
        files.extend(sorted(glob.glob(os.path.join(d, "*.xml"))))
    # Global pages are refreshed first; month windows only fill their gaps.
    global_posts = set()
    for f in sorted(files, key=lambda path: not Path(path).match("f_[0-9]*.xml")):
        is_global = Path(f).match("f_[0-9]*.xml")
        is_month = MONTH_PAGE_RE.fullmatch(Path(f).name)
        t = Path(f).read_text(encoding="utf-8", errors="ignore")
        for it in ITEM_RE.findall(t):
            items += 1
            L = LINK_RE.search(it)
            if not L:
                continue
            url = html.unescape(L.group(1)).strip()
            if not url.endswith(".html"):
                continue
            if is_month and url in global_posts:
                continue
            if is_global:
                global_posts.add(url)
            if url not in posts_seen:
                T, P = TITLE_RE.search(it), PUB_RE.search(it)
                posts_seen[url] = (html.unescape(T.group(1)).strip() if T else "",
                                   P.group(1).strip() if P else "")
            dec = html.unescape(it)
            for u in MEDIA_RE.findall(dec):
                classify(u.rstrip('.,;"'), audio[url], video[url],
                         set(audio[url]), set(video[url]))
    return posts_seen, audio, video, items


def parse_page_tsv(path, audio, video):
    """Merge rows scraped from raw pages. -> (rows, posts_covered)."""
    rows, covered = 0, 0
    if not path:
        return 0, 0
    with open(path) as f:
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) != 2 or not cols[1].endswith(".html"):
                continue
            u, p = cols
            rows += 1
            had_media = bool(audio.get(p) or video.get(p))
            classify(u, audio[p], video[p], set(audio[p]), set(video[p]))
            if not had_media and (audio[p] or video[p]):
                covered += 1
    return rows, covered


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--posts", required=True, help="sitemap posts list")
    ap.add_argument("--feeds", action="append", default=[],
                    help="feed dir (repeatable)")
    ap.add_argument("--page-tsv", help="page-scraped media TSV")
    ap.add_argument("--out", default="haramain")
    ap.add_argument("--site", default="http://www.haramain.info")
    args = ap.parse_args()

    posts = read_lines(args.posts)
    if not posts:
        ap.error("a nonempty sitemap post list is required")
    for feed_dir in args.feeds:
        if not os.path.isdir(feed_dir) or not glob.glob(os.path.join(feed_dir, "*.xml")):
            raise ValueError(f"feed directory missing or empty: {feed_dir}")
    posts_seen, audio_by_post, video_by_post, items = parse_feed_files(args.feeds)
    scraped, scraped_new = parse_page_tsv(args.page_tsv, audio_by_post, video_by_post)

    print(f"sitemap posts        : {len(posts)}")
    print(f"feed items scanned   : {items}")
    print(f"posts with feed data : {len(posts_seen)}")
    print(f"page-scraped rows    : {scraped} ({scraped_new} posts beyond feed)")

    # roll up per year
    by_year_posts = collections.defaultdict(list)
    by_year_audio = collections.defaultdict(list)
    by_year_video = collections.defaultdict(list)
    for p in posts:
        y = year_of(p)
        if y:
            by_year_posts[y].append(p)
    all_audio, all_video = set(), set()
    for src, dst, alls in ((audio_by_post, by_year_audio, all_audio),
                           (video_by_post, by_year_video, all_video)):
        for p, urls in src.items():
            y = year_of(p)
            if not y:
                continue
            for u in urls:
                dst[y].append((u, p))
                alls.add(u)

    with_media = {p for p, urls in audio_by_post.items() if urls}
    with_media.update(p for p, urls in video_by_post.items() if urls)
    # every post the archive has no media URL for — including posts the feed
    # served but that yielded nothing
    no_media = [p for p in posts if p not in with_media]

    out = Path(os.path.abspath(args.out))
    protected = [Path.cwd(), Path(__file__), Path(args.posts),
                 *(Path(d) for d in args.feeds)]
    if args.page_tsv:
        protected.append(Path(args.page_tsv))
    target = out.resolve()
    if out.is_symlink() or any(p.resolve().is_relative_to(target) for p in protected):
        ap.error("OUT must be a dedicated dump directory, separate from the checkout and inputs")
    if out.exists() and (not out.is_dir() or
                         (any(out.iterdir()) and not (out / "csv/catalog.csv").is_file())):
        ap.error("refusing to replace an existing directory that is not a dump")
    if out.exists() and (out / "csv/catalog.csv").is_file() and not (all_audio or all_video):
        ap.error("refusing to replace an existing catalog with an empty one")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{out.name}.build-", dir=out.parent) as work:
        staging = Path(work) / "new"
        for sub in ("years", "index", "csv"):
            os.makedirs(os.path.join(staging, sub), exist_ok=True)

        years = sorted(set(by_year_posts) | set(by_year_audio) | set(by_year_video))
        stats = []
        for y in years:
            d = os.path.join(staging, "years", str(y))
            os.makedirs(d, exist_ok=True)
            srcs = sorted(by_year_posts.get(y, []))
            with open(os.path.join(d, "sources.txt"), "w") as f:
                f.write("\n".join(srcs) + ("\n" if srcs else ""))
            for kind, roll in (("audio", by_year_audio), ("video", by_year_video)):
                urls = sorted({u for u, _ in roll.get(y, [])})
                with open(os.path.join(d, f"{kind}_urls.txt"), "w") as f:
                    f.write("\n".join(urls) + ("\n" if urls else ""))
                with open(os.path.join(d, f"{kind}_with_source.tsv"), "w") as f:
                    for u, p in sorted(roll.get(y, [])):
                        f.write(f"{u}\t{p}\n")
            stats.append((y, len(srcs),
                          len({u for u, _ in by_year_audio.get(y, [])}),
                          len({u for u, _ in by_year_video.get(y, [])})))

        # index
        idx = os.path.join(staging, "index")
        for name, data in (("all_posts.txt", posts),
                           ("all_audio_urls.txt", sorted(all_audio)),
                           ("all_video_urls.txt", sorted(all_video)),
                           ("posts_without_media.txt", no_media)):
            with open(os.path.join(idx, name), "w") as f:
                f.write("\n".join(data) + ("\n" if data else ""))
        for name, urls in (("hosts.txt", all_audio), ("video_hosts.txt", all_video)):
            c = collections.Counter(host_of(u) for u in urls)
            with open(os.path.join(idx, name), "w") as f:
                for h, n in c.most_common():
                    f.write(f"{n}\t{h}\n")

        # catalog
        cat = os.path.join(staging, "csv", "catalog.csv")
        with open(cat, "w") as f:
            f.write("year,type,url,host,source_post\n")
            for y in years:
                for kind, roll in (("audio", by_year_audio), ("video", by_year_video)):
                    for u, p in sorted(roll.get(y, [])):
                        f.write(f'{y},"{kind}","{u}","{host_of(u)}","{p}"\n')

        # README
        with open(os.path.join(staging, "README.md"), "w") as f:
            f.write(f"# {args.site.replace('http://', '').replace('https://', '')}"
                    " media archive dump\n\n")
            f.write(f"- Source site : {args.site}\n")
            f.write(f"- Post pages (sitemap) : {len(posts)}\n")
            f.write(f"- Posts with feed content : {len(posts_seen)}\n")
            f.write(f"- Page-scraped rows : {scraped} ({scraped_new} extra posts)\n")
            f.write(f"- Posts with NO media found : {len(no_media)}"
                    " -> index/posts_without_media.txt\n")
            f.write(f"- Unique audio URLs : {len(all_audio)}\n")
            f.write(f"- Unique video URLs : {len(all_video)}\n")
            if years:
                f.write(f"- Years covered : {years[0]}-{years[-1]}\n")
            f.write("- YouTube/Google URLs : 0 (excluded, asserted at build time)\n\n")
            f.write("## Layout\n```\n<out>/\n"
                    "├── years/<YYYY>/sources.txt | audio_urls.txt | video_urls.txt\n"
                    "│                 audio_with_source.tsv | video_with_source.tsv\n"
                    "├── index/all_posts.txt | all_audio_urls.txt | all_video_urls.txt\n"
                    "│       posts_without_media.txt | hosts.txt | video_hosts.txt\n"
                    "└── csv/catalog.csv     # year,type,url,host,source_post\n```\n\n")
            f.write("## Per-year counts (posts / audio / video)\n\n")
            f.write("| year | posts | audio | video |\n|---|---:|---:|---:|\n")
            for y, np_, na, nv in stats:
                f.write(f"| {y} | {np_} | {na} | {nv} |\n")

        # console summary
        print("\nyear  posts  audio  video")
        for y, np_, na, nv in stats:
            print(f"{y}  {np_:5d}  {na:5d}  {nv:5d}")
        print(f"\ntotal audio urls : {len(all_audio)}")
        print(f"total video urls : {len(all_video)}")
        print(f"posts w/o media   : {len(no_media)}")
        print(f"catalog rows      : {len(all_audio) + len(all_video)}")

        # assertions (never by eye)
        leaked = [u for u in (all_audio | all_video) if is_bad_host(u)]
        if leaked:
            raise ValueError(f"excluded hosts leaked: {leaked[:5]}")
        broken = [u for u in (all_audio | all_video)
                  if len(re.findall(r"https?://", u, re.I)) > 1
                  and not re.search(r"[?&=#]https?://", u, re.I)]
        if broken:
            raise ValueError(f"glued urls leaked: {broken[:5]}")
        ws = [u for u in (all_audio | all_video)
              if re.search("[" + DISALLOWED + "]", u)]
        if ws:
            raise ValueError(f"malformed urls leaked: {ws[:5]}")
        print("assertions: no excluded hosts, no glued urls, no malformed urls -> OK")
        # Keep the backup outside the temporary tree: if rollback itself
        # fails, cleanup must not discard the previous catalog.
        if not out.exists():
            staging.rename(out)
            return
        backup_dir = Path(tempfile.mkdtemp(prefix=f".{out.name}.old-", dir=out.parent))
        backup = backup_dir / "previous"
        out.rename(backup)
        try:
            staging.rename(out)
        except BaseException:
            backup.rename(out)
            backup_dir.rmdir()
            raise
        shutil.rmtree(backup_dir)


if __name__ == "__main__":
    main()
