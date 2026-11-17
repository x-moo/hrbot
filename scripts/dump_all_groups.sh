#!/usr/bin/env bash
# Dump every label group from menu.json into <out>/<slug>/
set -euo pipefail
cd "$(dirname "$0")"
MENU=${MENU:-../work/menu.json}
OUT=${OUT:-../haramain/labels}
FEED1=${FEED1:-../work/feeds}
FEED2=${FEED2:-../work/feeds2}
PAGE_TSV=${PAGE_TSV:-../work/page_media.tsv}

python3 - <<'PY' > /tmp/groups.txt
import json, sys
menu = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "../work/menu.json",
                      encoding="utf-8"))
for g in menu:
    print(g)
PY

n=0
while IFS= read -r g; do
  n=$((n+1))
  echo "=== [$n] $g"
  python3 by_label.py dump "$g" --menu "$MENU" \
      --feeds "$FEED1" --feeds "$FEED2" --page-tsv "$PAGE_TSV" \
      --out "$OUT" || echo "!! FAILED: $g"
done < /tmp/groups.txt

echo "ALL_GROUPS_DONE"
