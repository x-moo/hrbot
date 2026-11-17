# Telegram bot — Haramain media archive

Lightweight bot (stdlib + requests) to search the media dump of
haramain.info. Data is read from SQLite, so results are instant and
the memory footprint is tiny.

```
 RAM ~25-40 MB   CPU ~0% (idle)   Disk: archive.db ~10-20 MB
```

## Setup

```bash
# 0. dependencies (bot.sh does this for you on ./bot.sh run|start)
pip install -r requirements.txt

# 1. import the dump (built by build_dump.py) into sqlite — or simply
#    run `./bot.sh init` from the repo root, which does it for you
python3 import_dump.py --dump ../haramain --db archive.db

# 2. get a token from @BotFather -> put it in .env
cp .env.example .env
nano .env          # BOT_TOKEN=...

# 3. run — foreground
./bot.sh run        # (or: python3 bot.py directly)
#    background:  ./bot.sh start | stop | restart
```
`.env` is loaded from next to the script (or cwd). Real env vars win
when a key is set in both places.

## Commands

## Commands — semuanya juga tombol

`/start` mengirim satu menu tombol; setiap perintah bisa ditekan tanpa
mengetik. Setiap layar punya baris `[ ⬅️ … ] [ 🔝 Menu ]` untuk naik lagi.

| command | contoh | tombol | role |
|---|---|---|---|
| /start | /start | 🆕 🔎 📅 🗓 📊 ❓ | menu tombol + statistik singkat |
| /latest [n] | /latest 3 | 🔝 Menu | n rekaman terbaru |
| /search <words...> | /search sudais fajr | ⬅️ Back / 📄 / Next ➡️ | cari nama file |
| /date YYYY-MM-DD | /date 2026-10-28 | ⬅️ Back / 📄 / Next ➡️ | media satu tanggal |
| /year [yyyy] | /year 2024 | 🗓 tahun → 📅 bulan → halaman | telusuri per tahun/bulan |
| /stat | /stat | 🔝 Menu | statistik arsip |
| /dl \<url\> | /dl https://host/rec.mp3 | — | unduh + kirim lewat MTProto |

## Peta tombol

```
/start
 ├─ 🆕 Latest        → 5 terbaru
 ├─ 🔎 Search        → minta `/search <kata>`; hasil 5 per halaman
 ├─ 📅 By date       → minta `/date YYYY-MM-DD`
 ├─ 🗓 Year          → daftar tahun (jumlah URL per tahun)
 │     └─ 2026 → daftar bulan (jumlah per bulan)
 │           └─ Oct → item berhalaman 5-an + ⬅️ Months
 ├─ 📊 Stat          → total baris/URL, audio vs video, periode
 └─ ❓ Help
```

- Paging: `PAGE_SIZE` = 5 per halaman. `⬅️ Back` / `Next ➡️` mengedit
  pesan yang sama di tempat (tidak spam). Tombol tengah `📄 1/3` hanya
  penanda (ditekan = no-op). Di ujung, tombol yang tak berlaku hilang.
- Layar tanpa halaman (menu/help/stat/latest/daftar tahun & bulan) hanya
  menampilkan tombol navigasi; karena itu selalu ada tombol yang bisa
  ditekan di setiap pesan.
- Tombol paging membawa `(tahun, bulan, hari)` di `callback_data`, jadi
  pindah **bulan ↔ halaman** tidak perlu state apa pun di server.
- Hanya hasil `/search` yang disimpan (per `(chat_id, message_id)`,
  `VIEW_TTL` 15 menit) karena isinya tak bisa dipulihkan dari tombol.
  Kalau kadaluarsa: "Hasil sudah kadaluarsa — jalankan perintah lagi."
- Halaman di luar rentang (mis. setelah re-import) diklem ke halaman
  terakhir, bukan error.
- Polling pakai `allowed_updates: ["message", "callback_query"]`, dan tiap
  tap dibalas `answerCallbackQuery` supaya spinner klien berhenti.

## /dl — leech into Telegram

`/dl <url>` downloads the URL with aria2c and uploads it with the
MTProto session, so files the Bot API would refuse (anything above 50 MB)
still get through. A status message is edited while it works, then the
result is posted and the status removed. Status text stays deliberately
plain: `⬇️ #1 · host.tld/…/file.mp3` plus short progress lines — the URL's
query string (often a token) and any session identity stay out.

```
/dl https://host/rec.mp3                 # send to this chat
/dl https://host/rec.mp3 as doc          # force document (no video/audio preview)
/dl https://host/big.mkv to channel      # keep the file in LEECH_CHAT instead
```

Behaviour notes:

- **Types as media** (audio/video/photo) by extension; `as doc` forces a
  document. Split parts (`.001`, …) always go as documents.
- **Target chat** is the chat the command came from. From a *private* chat
  the file lands in your Saved Messages when `USER_SESSION_STRING` is set;
  otherwise it is sent to that private chat.
- **Concurrency**: two downloads per chat at a time; further `/dl` calls
  answer `2 downloads already running`.
- **Requirements**: `TELEGRAM_API`, `TELEGRAM_HASH` (my.telegram.org), aria2c
  (auto-installed when root) and either `BOT_TOKEN` or `USER_SESSION_STRING`
  — from `.env`, same file as the archive bot. Without them `/dl` reports the
  missing key instead of failing silently.
- **Split size** comes from `LEECH_SPLIT_SIZE` (default 2000 MB).
- **Disk usage**: each file is deleted as soon as it is uploaded and the
  `downloads/dl<timestamp>/` folder is removed once empty, so storage stays
  near zero between downloads. The bot also sweeps empty folders at startup.
  Set `KEEP_FILES=1` to keep the files instead.

## 500 MB RAM server

```bash
./bot.sh start     # background; log in bot.log, pid in bot.pid
./bot.sh restart   # after an archive update
./bot.sh stop
```

`run`, `start` and `restart` first check that `requirements.txt` is
satisfied and pip-install whatever is missing (set `INSTALL_DEPS=0` to skip
that, e.g. on a machine with no package index). `init` and `stop` never
touch pip.

or use systemd (see `haramain-bot.service`).

## Updating the archive

Re-run the dump pipeline (`../scripts/run.sh all`), then
`python3 import_dump.py --dump ../haramain` again. The bot needs no
restart — the DB is opened read-only on every query.