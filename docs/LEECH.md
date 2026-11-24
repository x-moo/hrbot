# Telegram leech — download a URL, upload it to Telegram

`leech.py` streams a direct URL with a **bounded Python downloader** and uploads the result to a
chat over **MTProto** (kurigram). Large files are split into parts before
uploading.

Why MTProto instead of the Bot API: the hosted Bot API caps uploads at
50 MB, while MTProto allows 2 GB per file on a normal account (4 GB with
Telegram Premium).

This is a leech-only tool — it does not mirror to Drive, rclone, GoFile or
anywhere else. It downloads locally and sends to Telegram.

## Install

```bash
pip install -r requirements.txt      # requests + kurigram + tgcrypto
# No aria2c or apt/root access is required.
```

Two ways to use it: standalone (`python3 leech.py <url>`) or through the bot archive download buttons — see `BOT.md`.

## Configure

`TELEGRAM_API` and `TELEGRAM_HASH` come from <https://my.telegram.org> and are
required — they identify the MTProto application, not the bot. `BOT_TOKEN`
uploads as the bot (2 GB ceiling, fine for most files).

```bash
cp .env.example .env && nano .env
```

| key | role |
|---|---|
| `TELEGRAM_API` / `TELEGRAM_HASH` | api_id / api_hash (required) |
| `BOT_TOKEN` | upload as the bot |
| `USER_SESSION_STRING` | upload as a user account (required above 2 GB, 4 GB with Premium) |
| `LEECH_CHAT` | default target chat |
| `DOWNLOAD_DIR` | where files are downloaded (default `downloads/`) |
| `LEECH_SPLIT_SIZE` | split threshold, `2000mb` / `4000mb` / bytes |
| `THUMB_PATH` | default thumbnail for video/audio |

Generate a user session once:

```bash
python3 generate_session.py
```

## Run

```bash
python3 leech.py <url> -c @mychannel
```

Examples:

```bash
python3 leech.py https://example.com/lecture.mp3 -c @archive
python3 leech.py https://example.com/lecture.mp3 -c -1001234567890 -n fajr.mp3
python3 leech.py https://host/file.zip -c @archive --as-doc -H "Referer: https://site/"
python3 leech.py https://host/big.mkv -c @archive -s 1900mb -t thumb.jpg
```

| flag | meaning |
|---|---|
| `-c, --chat` | target chat id (`-100...`) or `@username` (default `LEECH_CHAT`) |
| `-o, --out` | download directory |
| `-n, --name` | output file name |
| `-H, --header` | extra request header, repeatable |
| `-j, --jobs` | legacy compatibility option; Python uses one connection |
| `-s, --split-size` | split threshold (e.g. `1900mb`) |
| `--no-split` | never split; upload as one file |
| `--as-doc` | send as document even for video/audio |
| `-t, --thumb` | thumbnail image path |
| `--keep` | keep the downloaded files (default: delete after upload) |

## What appears in logs and in the chat

Console lines are timestamped, redacted and compact — bot tokens, api hashes,
session strings, URLs and local paths never appear. The chat sees only the
short summary lines from `download`, `split` and `upload`:

```
06:40:34 start: SheikhJuhanyFajr.mp3
06:40:34 python: downloading
06:40:34 downloaded 13.0 MB in 4.3s                  <- chat
06:40:34 splitting 2.4 GB into 2000 MB parts         <- chat, when above the threshold
06:40:34 2 parts ready                               <- chat
06:40:34 session: bot ready
06:40:34 uploading 1/1 · rec.mp3 · 12.7 MB · audio   <- chat
06:40:34 sent 1/1 ✓                                  <- chat
06:40:34 done: 1 file(s) in 20.4s                    <- chat
```

Raw downloader diagnostics reach neither place: the chat gets at most one
status refresh every three seconds, download errors are logged once, and
session identities (bot username, account name) are never logged at all.

## Job controls and outcome contract

The bot tracks queued, downloading, waiting-upload, uploading and terminal
states in a bounded registry. Use `/status`, `/status <id>`, `/cancel <id>`, or
the buttons on a job message. Controls are bound to the originating chat.
Cooperative cancellation is checked during download, split, upload-slot waits,
client startup and upload waits. A socket operation already in progress can
still take up to its remaining timeout.

`leech.control.TransferError` carries `sent`, `total` and `retained` paths for
operator code; Telegram summaries show counts and filenames, never local paths.
`TransferCancelled` is a typed terminal outcome. Confirmed parts are deleted
unless keep is enabled, while failed/unconfirmed files remain. The application
does not automatically resend a timed-out or otherwise ambiguous upload.

## How it behaves

- **Session lifecycle**: MTProto uploads are serialized within the bot process,
  including client startup and shutdown. Bot clients use isolated in-memory
  sessions with updates disabled; no shared bot `.session` file is opened.
  The old session file can remain on disk and is no longer used by this path.
- **Timeouts**: startup is limited to 120 seconds, shutdown to 30 seconds, and
  each file upload to `LEECH_UPLOAD_TIMEOUT` seconds (default 1800). Failed or
  timed-out files remain on disk. Check the destination before retrying an
  ambiguous upload; the application does not blindly resend it.
- **Reconnect warnings**: `Session stopped before an answer arrived` is a
  library retry warning, not final proof of failed delivery. Look for `sent`
  and `done`, or the final failure message. Network failures can still occur.

- **Download backend**: Python streams HTTP/HTTPS/FTP with one connection and bounded memory. All downloads use this backend so resource limits cannot be bypassed by an installed binary. No magnets or resume; the legacy `-j` option is accepted for compatibility but has no effect.
- **Resource limits**: `LEECH_MAX_BYTES` defaults to 536870912 (512 MiB), `LEECH_MIN_FREE_BYTES` to 134217728 (128 MiB), and `LEECH_DOWNLOAD_TIMEOUT` to 1800 seconds. The downloader checks declared and streamed size, free space, and elapsed time between reads. Socket reads time out after at most 60 seconds; a stalled read can overrun the elapsed-time limit by that interval. Partials created by failed jobs are removed. Failed upload files remain for operator inspection, so monitor disk usage.
- **Redirects**: Authorization, Cookie and Proxy-Authorization headers are removed when a redirect changes origin, including HTTPS-to-HTTP redirects.
- **Splitting**: files above `LEECH_SPLIT_SIZE` (default 2000 MB) are split
  into `.001`, `.002`, ... parts using Python, and the original is removed.
- **Upload routing**: the bot session is used for anything up to 2000 MB;
  a user session (when configured) handles the rest. A non-premium user
  account still cannot go past 2000 MB and logs a warning.
- **Media type**: by extension — audio/video/photo, everything else as
  document; `--as-doc` forces document. A media rejection retries once as a
  document instead of failing the task.
- **Errors**: download failure, flood wait (`FloodWait` → bounded wait/retry)
  and missing config all report and exit non-zero; nothing is swallowed.
- **Storage**: each confirmed file is deleted immediately unless keep is enabled;
  job directories are removed when empty. `storage freed: 420 KB` is reported
  when cleanup succeeds. Set `KEEP_FILES=1` (or `--keep`) to retain files.
- **Limits**: the bounded source and any unsent split parts occupy disk until
  confirmed or manually cleaned after failure. The Bot API message text stays
  in `bot/downloads.py` — this script only sends files.
