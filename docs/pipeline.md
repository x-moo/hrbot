# pipeline/ — haramain.info media-archive pipeline

Reusable, stage-by-stage tools for harvesting every media URL from a
Blogger archive (built for haramain.info, works for any Blogger site).

## Stages

| # | script | stage | runs |
|---|--------|-------|------|
| 1 | `sitemap_posts.py` | ground truth: every post URL | `python3 sitemap_posts.py -o work/posts.txt` |
| 2 | `feed_dump.py` | structured bulk route (RSS) | `feed_dump.py global -d work/feeds` then `feed_dump.py fill -d work/feeds --posts work/posts.txt` |
| 3 | `scrape_pages.py` | page-level fallback for posts no feed serves | `probe` → gate on sample → `sweep` → `residue` |
| 4 | `build_dump.py` | assemble the tidy dump tree | `build_dump.py --posts ... --feeds ... --page-tsv ... --out haramain/` |
| 5 | `check_links.py` | verify: link health + histograms | `check_links.py haramain --hist` / `-n 120` |
| 6 | `by_label.py` | sidebar menu categories -> per-label folders | `menu` -> `show` -> `dump "<group>"` -> `summary` |
| — | `dump_all_groups.sh` | dump every group from menu.json | `./dump_all_groups.sh` |
| — | `run.sh` | runs every stage | `./run.sh [sitemap|feed|scrape|sweep|build|check|all]` |
| — | `common.py` | shared: media regexes, host exclusions, `classify()`, `unglue()` | imported by all |

## The order matters

```
sitemap  →  feed  →  reconcile  →  page-sweep  →  build  →  verify
 (ground     (cheap   (sitemap −     (residue     (tree +    (link
  truth)     bulk)     feed = gap)    only)        asserts)    health)
```

1. **Ground truth first.** The feed caps depth and silently omits old
   posts. The sitemap list is the denominator for every coverage claim.
2. **Cheapest structured route.** Global feed pagination, then month-window
   fills (`published-min/max`) for only the months that still miss posts.
3. **Reconcile before scaling.** `set(sitemap) − set(feed)` grouped by
   year-month is the map of what to fetch next.
4. **Page sweep only for the residue** (~9% here), and **gate on a sample**
   (`probe -n 30`) first: a broken regex, an undecoded body and a page with
   genuinely no media all look identical — zero rows.
5. **Build with assertions, never hand-filtered afterwards.** Excluded
   hosts (youtube/google), glued two-href URLs, and malformed URLs are all
   asserted at build time — the script refuses to finish if any leak.

## Conventions

- Output of stage 3: TSV rows `media_url <TAB> post_url`, appended and
  resumable (a page already listed in the TSV is skipped, so re-running a
  sweep only retries the pages that yielded nothing).
- Every fetch uses `curl --compressed` (undecoded gzip extracts zero
  matches, which reads exactly like "no media on this page").
- Parallel fetches use per-task temp files (a shared path races silently).
- Page-fetch scratch files stay in the current working directory and are removed after each fetch. Label-export scratch files stay in `pipeline/` and are removed on exit.
- URL hygiene lives in `common.py`: `unglue()` splits jammed hrefs,
  nested players (`?theFile=<url>`) are unwrapped, literal spaces in
  broken `src=` attrs become `%20`.

## Reuse on another site

```bash
SITE=https://example.blogspot.com ./run.sh all
```

Only `sitemap_posts.py` and `feed_dump.py` know about Blogger; `scrape_pages.py`
(`probe|sweep|residue|one`), `build_dump.py` and `check_links.py` are
site-agnostic — pass `--posts`, `--feeds`, `--page-tsv`, `--site`.
