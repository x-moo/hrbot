#!/usr/bin/env python3
"""Stage 3 -- page-level fallback: scrape media URLs out of raw post HTML.

For posts no feed serves (or that yielded nothing), fetch each page and
regex-extract media URLs.

Subcommands:
    probe   Sample N random pages, print status + extension/host histograms.
            Run this BEFORE a full sweep (a broken regex and a site with no
            media both look like zero rows at scale).
    sweep   Fetch every page in LIST, append "url<TAB>post" rows to OUT.
            Pages already present in OUT (i.e. that yielded media) are
            skipped, so re-running only retries the empty/failed ones.
    residue Pages still yielding nothing: re-check them and write the two
            outcome files (truly empty vs unfetchable).
    one     Fetch a single post URL and print what was found.

Output TSV columns: media_url <TAB> post_url
"""
import argparse
import collections
import concurrent.futures as cf
import html
import os
import random
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.common import MEDIA_RE, classify, host_of, ext_of  # noqa: E402


def fetch_page(url, timeout=60, tries=3, ua=True):
    """(code, body) of one page via curl. code != '200' means body is junk."""
    tmp = f"{os.path.abspath('.')}/.pg_{os.getpid()}_{os.urandom(4).hex()}.html"
    cmd = ["curl", "-s", "-L", "--compressed", "--max-time", str(timeout),
           "-w", "%{http_code}", "-o", tmp, url]
    if ua:
        cmd[1:1] = ["-H", "User-Agent: Mozilla/5.0"]
    cmd.insert(-1, "--")
    code = "000"
    try:
        for i in range(tries):
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout + 30)
            code = f"ERR:curl:{r.returncode}" if r.returncode else (r.stdout.strip() or "000")[-3:]
            if code == "200":
                break
            time.sleep(3 + i * 5)
        body = ""
        if code == "200":
            with open(tmp, "rb") as f:
                body = html.unescape(f.read().decode("utf-8", "ignore"))
        return code, body
    except Exception as e:                                   # noqa: BLE001
        return f"ERR:{type(e).__name__}", ""
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def media_of(body):
    """(audio, video) lists from a decoded page body."""
    a, v = [], []
    sa, sv = set(), set()
    for m in MEDIA_RE.findall(body):
        classify(m.rstrip('.,;"'), a, v, sa, sv)
    return a, v


def list_rows(path):
    """(url, post) pairs already present in the output TSV."""
    s = set()
    try:
        with open(path) as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) == 2:
                    s.add(p[1])
    except FileNotFoundError:
        pass
    return s


def emit(rows, out_path):
    """Append (url, post) rows, flushing immediately."""
    with open(out_path, "a") as f:
        for u, p in rows:
            f.write(f"{u}\t{p}\n")
        f.flush()


# --- subcommands ----------------------------------------------------------
def cmd_probe(args):
    posts = [l.strip() for l in open(args.list) if l.strip()]
    random.seed(args.seed)
    sample = random.sample(posts, min(args.n, len(posts)))
    codes, exts, hosts, zero = (collections.Counter(), collections.Counter(),
                                collections.Counter(), 0)
    for u in sample:
        code, body = fetch_page(u, tries=1)
        codes[code] += 1
        media = sum(media_of(body), [])
        if not media and code == "200":
            zero += 1
        for m in media:
            exts[ext_of(m)] += 1
            hosts[host_of(m)] += 1
    print(f"sampled: {len(sample)}")
    print(f"status : {dict(codes)}")
    print(f"exts   : {dict(exts.most_common())}")
    print(f"hosts  : {dict(hosts.most_common(8))}")
    print(f"200 pages with zero media: {zero}")
    if not exts:
        print("!! zero media found -- do NOT scale this up until fixed")


def cmd_sweep(args):
    posts = [l.strip() for l in open(args.list) if l.strip()]
    done = list_rows(args.out)          # posts that already yielded media
    todo = [p for p in posts if p not in done]
    print(f"to fetch: {len(todo)} (done: {len(done)})", flush=True)

    def one(url):
        code, body = fetch_page(url, tries=3)
        return url, code, sum(media_of(body), [])

    codes, rows, with_media = collections.Counter(), 0, 0
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for i, (url, code, media) in enumerate(ex.map(one, todo), 1):
            codes[code] += 1
            if media:
                with_media += 1
                emit([(m, url) for m in media], args.out)
                rows += len(media)
            if i % 50 == 0:
                print(f"  {i}/{len(todo)} new_rows={rows}", flush=True)
    print(f"sweep done: codes={dict(codes)} pages_with_media={with_media} "
          f"new_rows={rows}")


def cmd_residue(args):
    posts = [l.strip() for l in open(args.list) if l.strip()]
    done = list_rows(args.out)
    todo = [p for p in posts if p not in done]
    print(f"residue to classify: {len(todo)}", flush=True)

    empty, unfetchable, found = [], [], []
    for i, u in enumerate(todo, 1):
        code, body = fetch_page(u, tries=3)
        media = sum(media_of(body), [])
        if code != "200":
            unfetchable.append(u)
        elif media:
            found.append(u)
            emit([(m, u) for m in media], args.out)
        else:
            empty.append(u)
        if i % 25 == 0:
            print(f"  {i}/{len(todo)}", flush=True)

    with open(args.empty_out, "w") as f:
        f.write("\n".join(sorted(empty)) + ("\n" if empty else ""))
    if args.unfetchable_out:
        with open(args.unfetchable_out, "w") as f:
            f.write("\n".join(sorted(unfetchable)) + ("\n" if unfetchable else ""))
    print(f"empty={len(empty)} unfetchable={len(unfetchable)} media_now={len(found)}")
    for u in found[:10]:
        print(f"  media now: {u}")


def cmd_one(args):
    code, body = fetch_page(args.url, tries=1)
    a, v = media_of(body)
    print(f"code: {code}")
    for m in a:
        print(f"  [audio] {m}")
    for m in v:
        print(f"  [video] {m}")
    if not a and not v:
        print("  no media found")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="sample pages, print histograms")
    p.add_argument("list")
    p.add_argument("-n", type=int, default=30)
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(func=cmd_probe)

    s = sub.add_parser("sweep", help="fetch pages, append media rows")
    s.add_argument("list")
    s.add_argument("-o", "--out", required=True, help="output TSV (append)")
    s.add_argument("-j", "--jobs", type=int, default=6)
    s.set_defaults(func=cmd_sweep)

    r = sub.add_parser("residue", help="classify posts that yielded no media")
    r.add_argument("list")
    r.add_argument("-o", "--out", required=True, help="output TSV (append)")
    r.add_argument("--empty-out", required=True, help="posts that are text-only")
    r.add_argument("--unfetchable-out", help="posts that could not be fetched")
    r.set_defaults(func=cmd_residue)

    o = sub.add_parser("one", help="fetch one post URL")
    o.add_argument("url")
    o.set_defaults(func=cmd_one)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
