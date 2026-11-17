#!/usr/bin/env python3
"""Stage 5 -- verify: link health + sanity histograms of the finished dump.

    python3 check_links.py <dump_dir> [-n 120] [--seed 7]
    python3 check_links.py <dump_dir> --hist

--hist prints extension/host breakdowns without hitting the network.
Otherwise: HEAD-sample N audio URLs and report the alive ratio (2xx/3xx),
plus a few non-alive examples so you can see *why* (404 vs redirect farm).
"""
import argparse
import collections
import concurrent.futures as cf
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import host_of, ext_of, read_lines  # noqa: E402


def head(url, timeout=25):
    try:
        r = subprocess.run(
            ["curl", "-sI", "-L", "--max-time", str(timeout),
             "-o", "/dev/null", "-w", "%{http_code}|%{content_type}|%{url_effective}",
             url],
            capture_output=True, text=True, timeout=timeout + 15)
        return r.stdout.strip() or "000||"
    except Exception:                                          # noqa: BLE001
        return "ERR||"


def hist(dump):
    for kind in ("audio", "video"):
        urls = read_lines(os.path.join(dump, "index", f"all_{kind}_urls.txt"))
        if not urls:
            continue
        exts = collections.Counter(ext_of(u) for u in urls)
        hosts = collections.Counter(host_of(u) for u in urls)
        print(f"\n== {kind}: {len(urls)} urls")
        print("   ext :", dict(exts.most_common(8)))
        for h, n in hosts.most_common(6):
            print(f"   {n:7d}  {h}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", help="dump directory (contains index/)")
    ap.add_argument("-n", type=int, default=120, help="sample size")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--hist", action="store_true", help="histograms only, no net")
    args = ap.parse_args()

    if args.hist:
        hist(args.dump)
        return

    urls = read_lines(os.path.join(args.dump, "index", "all_audio_urls.txt"))
    if not urls:
        sys.exit("no audio urls in dump/index/all_audio_urls.txt")
    random.seed(args.seed)
    sample = random.sample(urls, min(args.n, len(urls)))

    codes = collections.Counter()
    detail = collections.defaultdict(list)
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        for i, (u, res) in enumerate(zip(sample, ex.map(head, sample)), 1):
            code = res.split("|", 1)[0]
            codes[code] += 1
            detail[code].append((u, res))
            if i % 25 == 0:
                print(f"  checked {i}/{len(sample)}", flush=True)

    print(f"sampled: {len(sample)}")
    for c, n in codes.most_common():
        print(f"  {c}: {n} ({n * 100 / len(sample):.0f}%)")
    alive = sum(n for c, n in codes.items() if c[:1] in "23")
    print(f"alive (2xx/3xx): {alive * 100 / len(sample):.0f}%")
    for c in [c for c in detail if c[:1] not in "23"][:3]:
        print(f"\nnon-alive {c}:")
        for u, res in detail[c][:4]:
            print("  ", res.split("|")[-1][:110] or u[:110])


if __name__ == "__main__":
    main()
