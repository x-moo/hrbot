#!/usr/bin/env python3
"""Filter the dump by the site's sidebar menu categories (label groups).

Subcommands:
    menu     Fetch the site's sidebar and parse every label group.
             Writes menu.json: {group: [{label, count, search_url}]}.
    show     Print groups / labels from menu.json (or live fetch).
    dump     Export a group: per-label folders with posts + media urls,
             a combined TSV, and an index pointing at the site's label pages.
    summary  One index.md across every group already dumped.

Typical use:
    python3 by_label.py menu
    python3 by_label.py show
    python3 by_label.py dump "Daily Salaah Recordings" \
        --feeds ../work/feeds --page-tsv ../work/page_media.tsv --out ../haramain/labels

Media comes from the feed items (which carry <category> labels) plus any
page-scraped TSV rows for the same posts; a post with no feed item carries
no label and therefore stays out of the per-label folders.
"""
import argparse
import collections
import glob
import html
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import MEDIA_RE, classify  # noqa: E402

ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
LINK_RE = re.compile(r"<link>\s*([^<]+?)\s*</link>", re.I)
CAT_RE = re.compile(r"<category[^>]*>\s*(.*?)\s*</category>", re.I | re.S)
WIDGET_RE = re.compile(r"<div class='widget Label'[^>]*>\s*<h2>(.*?)</h2>"
                       r"(.*?)</ul>", re.S)
LI_RE = re.compile(r"<li>\s*<a[^>]*href='([^']+)'[^>]*>(.*?)</a>"
                   r"(?:\s*<span[^>]*>\((\d+)\)</span>)?", re.S)

SITE = "http://www.haramain.info"
GROUP_PAGE = f"{SITE}/search/label/Daily%20Salaah%20Recordings"


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def norm_label(s):
    """Normalize a label for matching: entities, stray quotes, whitespace."""
    s = html.unescape(s).strip().strip('"\u201c\u201d')
    return re.sub(r"\s+", " ", s).strip()


def slug(name):
    return re.sub(r"[^\w\- ]+", "_", name, flags=re.U).strip().replace(" ", "_")


def parse_menu(page_html):
    """-> {group_title: [{label, count, search_url}]}"""
    menu = {}
    for title, body in WIDGET_RE.findall(page_html):
        labels = [{"label": strip_tags(name),
                   "count": int(count) if count else None,
                   "search_url": html.unescape(href)}
                  for href, name, count in LI_RE.findall(body)]
        labels = [l for l in labels if l["label"]]
        if labels:
            menu[strip_tags(title)] = labels
    return menu


def load_menu(args):
    if getattr(args, "menu", None) and os.path.exists(args.menu):
        with open(args.menu, encoding="utf-8") as f:
            return json.load(f)
    page = fetch(getattr(args, "page", None) or GROUP_PAGE)
    return parse_menu(page)


def find_group(menu, name):
    """Exact, then normalized, then case-insensitive, then substring match."""
    for cand in (name, norm_label(name)):
        if cand in menu:
            return cand, menu[cand]
    key = norm_label(name).lower()
    for g in menu:
        if norm_label(g).lower() == key:
            return g, menu[g]
    for g in menu:
        if key in norm_label(g).lower():
            return g, menu[g]
    sys.exit(f"group {name!r} not found. Available:\n  " + "\n  ".join(sorted(menu)))


def feed_items(feed_dirs):
    """Yield the HTML of every Blogger feed item across the given dirs."""
    for d in feed_dirs:
        for f in sorted(glob.glob(os.path.join(d, "*.xml"))):
            with open(f, encoding="utf-8", errors="ignore") as fh:
                for it in ITEM_RE.findall(fh.read()):
                    yield it


def item_url(item):
    """Post URL of a feed item, or None when it is not a post link."""
    m = LINK_RE.search(item)
    if not m:
        return None
    url = html.unescape(m.group(1).strip())
    return url if url.endswith(".html") else None


def load_post_labels(feed_dirs):
    """post_url -> [labels], from feed items' <category> tags."""
    post_labels = {}
    for it in feed_items(feed_dirs):
        url = item_url(it)
        if not url:
            continue
        for x in (html.unescape(c).strip() for c in CAT_RE.findall(it)):
            if x:
                post_labels.setdefault(url, [])
                if x not in post_labels[url]:
                    post_labels[url].append(x)
    return post_labels


def load_post_media(feed_dirs, page_tsv=None):
    """post_url -> [media urls], from feed items (+ page-scraped TSV rows)."""
    post_media = collections.defaultdict(list)
    seen = {}
    for it in feed_items(feed_dirs):
        url = item_url(it)
        if not url:
            continue
        a, v = [], []
        sa, sv = seen.setdefault(url, (set(), set()))
        for m in MEDIA_RE.findall(html.unescape(it)):
            classify(m.rstrip('.,;"'), a, v, sa, sv)
        post_media[url].extend(a + v)
    if page_tsv and os.path.exists(page_tsv):
        for line in open(page_tsv, errors="ignore"):
            c = line.rstrip("\n").split("\t")
            if len(c) == 2 and c[1].startswith("http"):
                post_media[c[1]].append(c[0])
    return post_media


def cmd_menu(args):
    menu = load_menu(args)
    if not menu:
        sys.exit("no label widgets found -- is the URL a label/search page?")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(menu, f, ensure_ascii=False, indent=1)
    print(f"menu: {len(menu)} groups, {sum(len(v) for v in menu.values())} labels "
          f"-> {args.out}")
    for g, labels in menu.items():
        print(f"  [{len(labels):2d}] {g}")


def cmd_show(args):
    menu = load_menu(args)
    if not args.group:
        for g, labels in menu.items():
            print(f"{g}  ({len(labels)} labels)")
        return
    g, labels = find_group(menu, args.group)
    print(f"{g}  ({len(labels)} labels)")
    for l in labels:
        print(f"  {l['count'] or '?':>6}  {l['label']}")
        print(f"         {l['search_url']}")


def cmd_dump(args):
    menu = load_menu(args)
    group, labels = find_group(menu, args.group)
    print(f"group: {group} ({len(labels)} labels)")

    post_labels = load_post_labels(args.feeds)
    post_media = load_post_media(args.feeds, args.page_tsv)

    # one post often carries several labels of this group -> attribute all
    member_set = {norm_label(l["label"]) for l in labels}
    label_posts = collections.defaultdict(list)
    for post, labs in post_labels.items():
        for x in {norm_label(x) for x in labs}:
            if x in member_set:
                label_posts[x].append(post)

    gdir = os.path.join(args.out, slug(group))
    os.makedirs(gdir, exist_ok=True)

    index_rows, all_media = [], []
    for meta in labels:
        name = meta["label"]
        posts = sorted(set(label_posts.get(norm_label(name), [])))
        media = sorted({u for p in posts for u in post_media.get(p, [])})
        d = os.path.join(gdir, slug(name))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "posts.txt"), "w") as f:
            f.write("\n".join(posts) + ("\n" if posts else ""))
        with open(os.path.join(d, "media_urls.txt"), "w") as f:
            f.write("\n".join(media) + ("\n" if media else ""))
        with open(os.path.join(d, "posts_with_media.tsv"), "w") as f:
            for p in posts:
                for u in sorted(set(post_media.get(p, []))):
                    f.write(f"{u}\t{p}\n")
        index_rows.append((name, len(posts), len(media), meta["search_url"]))
        all_media.extend((u, p, name) for p in posts
                         for u in post_media.get(p, []))

    # combined TSV: url <TAB> post <TAB> label
    with open(os.path.join(gdir, "all_media.tsv"), "w") as f:
        for u, p, lab in sorted(set(all_media)):
            f.write(f"{u}\t{p}\t{lab}\n")

    # index.md: the menu list with local stats + the site's own label page
    with open(os.path.join(gdir, "index.md"), "w") as f:
        f.write(f"# {group}\n\nGroup page: {GROUP_PAGE}\n\n")
        f.write("| label | posts | media urls | site search url |\n"
                "|---|---:|---:|---|\n")
        for name, np_, nm, url in index_rows:
            f.write(f"| {name} | {np_} | {nm} | {url} |\n")

    print(f"posts under group : {sum(r[1] for r in index_rows)}")
    print(f"unique media urls : {len({u for u, _, _ in all_media})}")
    print(f"-> {gdir}")
    if not any(r[1] for r in index_rows):
        print("!! zero posts matched -- check --feeds and the group name")


def cmd_summary(args):
    """Write <out>/index.md summarizing every group already dumped."""
    rows = []
    for g in sorted(os.listdir(args.out)):
        gdir = os.path.join(args.out, g)
        idx = os.path.join(gdir, "index.md")
        if not os.path.isdir(gdir) or not os.path.exists(idx):
            continue
        labels = psum = zero = 0
        for line in open(idx, encoding="utf-8"):
            if not line.startswith("| ") or line.startswith("|--") or "label |" in line:
                continue
            cols = line.split("|")
            if len(cols) >= 4:
                labels += 1
                try:
                    n = int(cols[2].strip())
                    psum += n
                    zero += (n == 0)
                except ValueError:
                    pass
        media = set()
        am = os.path.join(gdir, "all_media.tsv")
        if os.path.exists(am):
            for line in open(am, encoding="utf-8"):
                u = line.split("\t", 1)[0]
                if u:
                    media.add(u)
        rows.append((g, labels, zero, psum, len(media)))

    out = os.path.join(args.out, "index.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Label groups (from the site's sidebar menu)\n\n")
        f.write(f"Groups: {len(rows)}  |  Note: posts often carry labels in "
                "several groups, so column 'posts (sum)' overlaps.\n\n")
        f.write("| group | labels | empty labels | posts (sum) | unique media "
                "| folder |\n|---|---:|---:|---:|---:|---|\n")
        for g, nl, nz, ps, nm in rows:
            f.write(f"| {g.replace('_', ' ')} | {nl} | {nz} | {ps} | {nm} "
                    f"| `{g}/` |\n")
    print(f"summary: {len(rows)} groups -> {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("menu", help="parse the sidebar label groups")
    m.add_argument("--page", help=f"page with sidebar (default: {GROUP_PAGE})")
    m.add_argument("--out", default="menu.json")
    m.set_defaults(func=cmd_menu)

    s = sub.add_parser("show", help="list groups/labels")
    s.add_argument("group", nargs="?", help="group name (omit: all groups)")
    s.add_argument("--menu", help="menu.json from `menu`")
    s.add_argument("--page")
    s.set_defaults(func=cmd_show)

    d = sub.add_parser("dump", help="export a group's posts + media")
    d.add_argument("group")
    d.add_argument("--menu", help="menu.json from `menu`")
    d.add_argument("--page")
    d.add_argument("--feeds", action="append", default=[], required=True,
                   help="feed dir (repeatable)")
    d.add_argument("--page-tsv", help="page-scraped media TSV")
    d.add_argument("--out", default="../haramain/labels")
    d.set_defaults(func=cmd_dump)

    u = sub.add_parser("summary", help="index.md across all dumped groups")
    u.add_argument("--out", default="../haramain/labels")
    u.set_defaults(func=cmd_summary)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
