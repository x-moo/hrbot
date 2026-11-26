# Haramain tools

Harvest media links from haramain.info, import the catalog into SQLite, and serve it through a Telegram bot. The optional leech tool streams bounded downloads in Python and uploads them over MTProto. YouTube links found in a post are kept as reference-only buttons in the detail view — they are never downloaded or proxied.

## Repository layout

```text
archive/       SQLite importer and read-only query repository
bot/           Telegram application and Bot API transport
leech/         CLI orchestration, downloader, formatting and session helper
pipeline/      Sitemap, RSS, page scraping, dump building and link checks
deploy/        systemd service template
docs/          Bot, leech and pipeline guides
tests/         Offline integration and unit tests
scripts/       Compatibility wrappers for the old pipeline commands
config.py      Shared .env loader
start.sh       Single-command startup: deps, bootstrap, foreground bot
```

Root Python scripts remain as compatibility entry points. Implementation belongs in the directories above.

## Setup and run

Run these commands from the repository root:

```bash
python3 -m venv .venv
mkdir -p .cache/tmp
TMPDIR="$PWD/.cache/tmp" .venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env with your bot configuration, then start (foreground):
PYTHON="$PWD/.venv/bin/python" bash start.sh
```

`start.sh` validates configuration, checks/installs dependencies, harvests the
archive if the database is missing, then runs the bot in the foreground with
unbuffered logs. Set `SWEEP=1` to
include the slower page scrape. Logs go to stdout (redirect them if you need a
file). Set `INSTALL_DEPS=0` to skip the dependency check, `BOOTSTRAP=0` to skip
the initial harvest.

The original entry points still work:

```bash
python3 bot.py
python3 import_dump.py --dump haramain --db archive.db
python3 leech.py https://example.com/lecture.mp3 -c @archive
python3 generate_session.py
bash scripts/run.sh build
```

Canonical module commands are `python3 -m bot.app`, `python3 -m archive.import_dump`, `python3 -m leech.cli`, and `python3 -m leech.generate_session`. Run the pipeline with `bash pipeline/run.sh <stage>`.

## Configuration and runtime files

`.env` is read from the repository root, with the current directory as fallback; existing process environment values win. See [.env.example](.env.example).

Default runtime paths remain compatible: `archive.db`, `downloads/`, `work/`, and `haramain/` live beneath the repository root. They are ignored by Git. Page-fetch scratch files are created in the working directory and cleaned up; label-export scratch files stay beside the pipeline scripts. Neither pipeline uses `/tmp`.

`WORK` and `OUT` override pipeline paths; use absolute paths for overrides. `DB_PATH` and `DOWNLOAD_DIR` configure the other runtime locations. The dump builder validates inputs and stages a replacement before publishing it. `OUT` must be a dedicated dump directory, not an input directory or the checkout. Failed builds keep the previous dump.

Automatic refresh defaults to **03:00 UTC** when `DB_UPDATE_TIME` is absent.
Set `DB_UPDATE_TIME=` explicitly to disable it. On startup the background worker
catches up if the last successful import predates the latest scheduled run **or**
is older than `DB_UPDATE_MAX_AGE_HOURS` (default 24; `0` disables startup catch-up).
Failures retry after `DB_UPDATE_RETRY_SEC` (default 300 seconds) with exponential
backoff capped at one hour. A shared `DB_UPDATE_TIMEOUT_SEC` (default 7200 seconds)
bounds all refresh stages; timeout kills the process group and frees the lock.
`SWEEP=1` includes the slower page scrape. On storage-limited hosts,
`DB_UPDATE_PRUNE=1` deletes the rebuildable `work/` and `haramain/` output after
the import is committed and verified (failed refreshes keep everything).

Every refresh logs stage start/completion and writes `.update-status.json`
atomically. Inspect it without network access:

```bash
python3 -m bot.update --status
```

The database importer commits rows, UTC `meta.built` and precomputed aggregates
(`meta.stats`, so `/stat` and the year menu never rescan the table) together; a
failed import rolls back, and existing read-only bot connections see the next
successful commit.
Manual refresh uses `python3 -m bot.update` with the same cross-process lock.
See [Botkeep deployment](docs/BOTKEEP.md) and [architecture](docs/ARCHITECTURE.md).

## Quran reciters

Use `/imaams` to browse the dedicated Quran recitation catalog: imam → paged
audio tracks. Open the original on Archive.org or tap **Download** to receive
the selected track in the same Telegram chat. `/imaams` is in Telegram's
command menu, not the `/start` screen. Metadata is cached separately in
`imaams-cache.db`; no Archive.org API key or new environment variable is needed.
See [the bot guide](docs/BOT.md#quran-recitations-imaams) for cache and navigation details.

## Development

```bash
mkdir -p .cache/tmp
python3 -m unittest discover -s tests -v
python3 -m compileall -q archive bot leech pipeline config.py
bash -n start.sh pipeline/*.sh scripts/*.sh
```

Tests use local fixtures and keep temporary directories in `.cache/` beneath this checkout. No Telegram credentials or live archive are required. Install `requirements.txt` first.

## Guides

- [Bot commands and operation](docs/BOT.md)
- [Leech configuration and CLI](docs/LEECH.md)
- [Harvest pipeline](docs/pipeline.md)
- [systemd template](deploy/haramain-bot.service)

The service template must be edited for your checkout and Python interpreter. Live scraping, Telegram polling, and uploads require network access and appropriate configuration; they are not part of the offline test suite.
