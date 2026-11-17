# haramain-tools — media-archive dump pipeline + Telegram bot

Two parts in one repo:

```
├── bot.py               # Telegram bot (Bot API, repo root)
├── bot.sh               # service control: init / run / start / stop / restart
├── leech.py             # download a URL (aria2c) + upload via MTProto
├── generate_session.py  # one-time helper for USER_SESSION_STRING
├── import_dump.py       # dump tree -> SQLite
├── .env.example         # BOT_TOKEN / DB_PATH / POLL_SEC + leech keys
├── haramain-bot.service # systemd unit
├── BOT.md               # bot usage
├── LEECH.md             # leech usage (MTProto, >50 MB files)
├── requirements.txt
└── scripts/             # harvest pipeline: haramain.info -> dump tree
```

Full flow: **pipeline builds the dump -> import into SQLite -> bot serves users.**

```
bot.sh init  ->  haramain/  ->  import_dump.py  ->  archive.db  ->  bot.sh run
 (harvest)      (dump tree)     (import)          (sqlite)       (telegram)
```

## Quick start

```bash
# 1. build the archive: harvest (4-8 h on a 500 MB server; resumable,
#    safe to stop) + import into sqlite (~20 MB db)
./bot.sh init

# 2. token from @BotFather -> .env
cp .env.example .env && nano .env
#    (dependencies: ./bot.sh run|start installs requirements.txt itself)

# 3. run (foreground; use `./bot.sh start` for background)
./bot.sh run
```

For files the hosted Bot API refuses (anything above 50 MB) use the MTProto
leech instead — see `LEECH.md`:

```bash
python3 leech.py https://example.com/lecture.mp3 -c @archive
```

Service management: `./bot.sh start|stop|restart` (pidfile `bot.pid`,
log `bot.log`). For production you can instead use
`haramain-bot.service` (systemd, auto-restart, MemoryMax=150M).
Bot commands are documented in `BOT.md`.

## Directory map

### scripts/ — pipeline (details: scripts/README.md)

| # | script | role |
|---|--------|------|
| 1 | `sitemap_posts.py` | ground truth: every post URL from the sitemap |
| 2 | `feed_dump.py` | bulk via RSS (`global` + `fill` for months with gaps) |
| 3 | `scrape_pages.py` | per-page fallback for posts no feed serves (`probe` -> `sweep` -> `residue`) |
| 4 | `build_dump.py` | builds the dump tree + assertions |
| 5 | `check_links.py` | live-link percentage + histograms |
| 6 | `by_label.py` + `dump_all_groups.sh` | per-category (sidebar menu) |
| - | `common.py` | media regexes, host exclusions, `classify()`, `unglue()` |
| - | `run.sh` | runs every pipeline stage (called by `bot.sh init`) |

The root `bot.sh` wraps init/bootstrap plus background service control:
`init | run | start | stop | restart`.

### bot (repo root)

| file | role |
|------|------|
| `bot.py` | polling loop, reads `.env`, answers commands (`/dl` included) |
| `import_dump.py` | dump tree -> SQLite |
| `.env.example` | `BOT_TOKEN` / `DB_PATH` / `POLL_SEC` template |
| `haramain-bot.service` | systemd unit |
| `requirements.txt` | `requests` + `kurigram`/`tgcrypto` (leech) |

### leech (repo root)

| file | role |
|------|------|
| `leech.py` | aria2c download -> MTProto upload (2 GB / 4 GB Premium) |
| `generate_session.py` | mints `USER_SESSION_STRING` for the user session |
| `LEECH.md` | flags, limits, behaviour |

The Bot API cannot upload past 50 MB; `leech.py` bypasses that by talking
MTProto directly. It only leeches — no mirroring to Drive/rclone/cloud hosts.

## Figure targets (500 MB RAM / 1 GB disk server)

| item | value |
|---|---|
| posts (sitemap) | ~49,580 |
| temporary feed XML | ~109 MB -> **delete after build** |
| final dump (text) | ~44 MB < 100 MB |
| archive.db (100K rows) | ~20 MB |
| bot RAM | ~17 MB |
| full sweep | 3.6-8 h, resumable |

## Notes

- A live link is not the same as an extracted one: old hosts (4shared,
  veoh) are mostly dead; `mirrors.quranicaudio.com` is the active one.
  Measure with `scripts/check_links.py haramain -n 120`.
- Updating the archive = re-run the pipeline, then re-import. The bot
  **does not need a restart** (SQLite is opened read-only per query).
