# Haramain tools

Harvest media links from haramain.info, import the catalog into SQLite, and serve it through a Telegram bot. The optional leech tool downloads files with aria2c and uploads them over MTProto.

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
bot.sh         Bootstrap and service control
```

Root Python scripts remain as compatibility entry points. Implementation belongs in the directories above.

## Setup and run

Run these commands from the repository root:

```bash
python3 -m venv .venv
mkdir -p .cache/tmp
TMPDIR="$PWD/.cache/tmp" .venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env with your bot configuration.
PYTHON="$PWD/.venv/bin/python" bash bot.sh init
PYTHON="$PWD/.venv/bin/python" bash bot.sh run
```

`init` harvests the archive if the database is missing. Set `SWEEP=1` to include the slower page scrape. `start`, `stop`, and `restart` manage a background process; logs and its pidfile remain in the repository root.

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

Default runtime paths remain compatible: `archive.db`, `downloads/`, `work/`, `haramain/`, `bot.log`, and `bot.pid` live beneath the repository root. They are ignored by Git. Page-fetch scratch files are created in the working directory and cleaned up; label-export scratch files stay beside the pipeline scripts. Neither pipeline uses `/tmp`.

`WORK` and `OUT` override pipeline paths; use absolute paths for overrides. `DB_PATH`, `DOWNLOAD_DIR`, `PIDFILE`, and `LOGFILE` configure the other runtime locations. The dump builder validates inputs and stages a replacement before publishing it. `OUT` must be a dedicated dump directory, not an input directory or the checkout. Failed builds keep the previous dump.

To enable a daily refresh, set `DB_UPDATE_TIME=03:00` in `.env` (UTC). It runs while the bot process is running and needs no crontab or extra terminal. Updates do not run when the bot is stopped; `SWEEP=1` includes the slower page scrape.

## Development

```bash
mkdir -p .cache/tmp
python3 -m unittest discover -s tests -v
python3 -m compileall -q archive bot leech pipeline config.py
bash -n bot.sh pipeline/*.sh scripts/*.sh
```

Tests use local fixtures and keep temporary directories in `.cache/` beneath this checkout. No Telegram credentials or live archive are required. Install `requirements.txt` first.

## Guides

- [Bot commands and operation](docs/BOT.md)
- [Leech configuration and CLI](docs/LEECH.md)
- [Harvest pipeline](docs/pipeline.md)
- [systemd template](deploy/haramain-bot.service)

The service template must be edited for your checkout and Python interpreter. Live scraping, Telegram polling, and uploads require network access and appropriate configuration; they are not part of the offline test suite.
