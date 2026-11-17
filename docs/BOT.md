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
python3 import_dump.py --dump haramain --db archive.db

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

## Peta tombol

Bot juga mengaktifkan tombol **Menu** bawaan Telegram di sebelah kolom pesan
pada chat privat. Tombol ini membuka daftar `/start`, `/latest`, `/search`,
`/date`, `/year`, `/stat`, `/language`, dan `/help`. Perintah yang
memerlukan argumen tetap harus dilengkapi pengguna sebelum dikirim.
Registrasi dilakukan saat startup dan `/start`; perubahan bahasa juga
memperbarui deskripsi perintah untuk chat tersebut. Nama dan posisi tombol
bawaan mengikuti aplikasi Telegram. Grup memakai daftar slash commands;
pengaturan Menu Button khusus hanya didukung pada chat privat.

Implementasi mengikuti [setMyCommands](https://core.telegram.org/bots/api#setmycommands)
dan [setChatMenuButton](https://core.telegram.org/bots/api#setchatmenubutton),
disertai pembacaan ulang konfigurasi. Kegagalan konfigurasi dicatat ke log
dan tidak menghentikan polling.

Menu menyediakan tombol 🌐 Bahasa / Language untuk memilih 🇬🇧 English atau
🇮🇩 Indonesia. Bisa juga lewat `/language` atau `/lang`. Default Indonesia;
pilihan berlaku per chat (bersama untuk anggota grup) dan disimpan di
`preferences.db` pada root repository, terpisah dari database arsip agar
bertahan setelah restart dan impor ulang. Direktori repository harus writable.

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

## Download dari hasil arsip

Hasil terbaru, pencarian, tanggal, dan bulan menampilkan tombol nama rekaman,
tanpa daftar teks tautan yang berulang. Klik nama rekaman untuk membuka
detail: **🔗 Sumber** membuka halaman asal, **⬇️ Unduh** mengirim file ke chat
yang sama, dan **⬅️ Kembali** memulihkan daftar serta halaman sebelumnya.
Tidak perlu menyalin URL. Perintah `/dl` untuk URL bebas sudah dinonaktifkan
dan dihapus dari menu serta daftar perintah Telegram.

Tombol hanya merujuk URL arsip yang ditampilkan, terikat pada chat asal,
dan berlaku 15 menit. Setelah restart atau kedaluwarsa, buka hasil arsip
lagi untuk mendapatkan tombol baru. Mengimpor ulang arsip tidak mengubah
URL tujuan tombol yang masih berlaku. Maksimal dua unduhan berjalan per chat.
Backend tetap memakai aria2c jika tersedia, atau fallback Python.
Upload membutuhkan TELEGRAM_API, TELEGRAM_HASH dan BOT_TOKEN/USER_SESSION_STRING.
Standalone `python3 leech.py <url>` tetap tersedia untuk operator server.

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

or use systemd (see `deploy/haramain-bot.service`).

## Updating the archive

Re-run the dump pipeline (`bash pipeline/run.sh all`), then
`python3 import_dump.py --dump haramain` again. The bot needs no
restart — the DB is opened read-only on every query.
