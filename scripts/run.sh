#!/usr/bin/env bash
# End-to-end pipeline for the haramain.info media archive.
# Every stage is independently re-runnable; each step checks its own input.
#
#   ./run.sh            # full run into ./haramain
#   ./run.sh build      # just rebuild the dump from already-collected data
#   ./run.sh check      # just verify the finished dump
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
SITE=${SITE:-http://www.haramain.info}
OUT=${OUT:-../haramain}
WORK=${WORK:-../work}          # collected raw data lives here
FEEDS="$WORK/feeds"            # stage-2 output (global + month fills)
FEEDS_EXTRA="$WORK/feeds2"     # optional second feed dir (month-window fills)
PAGES="$WORK/page_media.tsv"   # stage-3 output
POSTS="$WORK/posts.txt"        # stage-1 output

# build: feed args from whichever dirs exist (bash array, safe splitting)
FEED_ARGS=()
for d in "$FEEDS" "$FEEDS_EXTRA"; do
  [ -d "$d" ] && FEED_ARGS+=(--feeds "$d")
done
[ ${#FEED_ARGS[@]} -eq 0 ] && FEED_ARGS=(--feeds "$FEEDS")

mkdir -p "$WORK"

case "${1:-all}" in
  sitemap)
    $PY sitemap_posts.py --site "$SITE" -o "$POSTS"
    ;;
  feed)
    # NB: `global` paginates serially (no -j flag exists there); only `fill`
    # takes -j. Passing -j to global aborts the whole stage with argparse error.
    $PY feed_dump.py global -d "$FEEDS" --site "$SITE"
    $PY feed_dump.py fill   -d "$FEEDS" --site "$SITE" --posts "$POSTS" -j 4
    ;;
  scrape)
    # gate on a sample first -- a broken regex and a site with no media
    # both look like zero rows at scale (see scripts/README.md step 3)
    $PY scrape_pages.py probe "$POSTS" -n 30
    ;;
  sweep)
    $PY scrape_pages.py sweep "$POSTS" -o "$PAGES" -j 6
    $PY scrape_pages.py residue "$POSTS" -o "$PAGES" \
        --empty-out "$WORK/posts_without_media.txt" \
        --unfetchable-out "$WORK/posts_unfetchable.txt"
    ;;
  build)
    # FEED_ARGS is a bash array of --feeds pairs; plain $FEED_ARGS keeps only
    # the first word, so expand it properly:
    $PY build_dump.py --posts "$POSTS" "${FEED_ARGS[@]}" \
        --page-tsv "$PAGES" --out "$OUT" --site "$SITE"
    ;;
  check)
    $PY check_links.py "$OUT" --hist
    $PY check_links.py "$OUT" -n 120
    ;;
  all)
    $0 sitemap
    $0 feed
    $0 sweep
    $0 build
    $0 check
    ;;
  *)
    echo "usage: $0 [sitemap|feed|scrape|sweep|build|check|all]" >&2
    exit 2
    ;;
esac
echo "OK ($1)"
