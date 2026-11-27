# Telegram bot — Haramain media archive

Lightweight bot (stdlib + requests) to search the media dump of
haramain.info. Data is read from SQLite, so results are instant and
the memory footprint is tiny.

```
 RAM ~25-40 MB   CPU ~0% (idle)   Disk: archive.db ~10-20 MB
```

## Setup

```bash
# 0. dependencies (start.sh installs anything missing)
pip install -r requirements.txt

# 1. start: harvests the archive if the DB is missing, then runs in foreground
cp .env.example .env
nano .env          # BOT_TOKEN=... (daily refresh defaults to 03:00 UTC)
bash start.sh      # (or: python3 bot.py directly, once archive.db exists)
```
`.env` is loaded from next to the script (or cwd). Real env vars win
when a key is set in both places.

## Commands

`/start` membuka empat tombol dalam dua baris: **🆕 Terbaru**, **🔎 Cari**,
**❓ Bantuan**, dan **🌐 Bahasa**. Perintah lainnya tersedia lewat menu perintah
bawaan Telegram. Layar arsip dan katalog menyediakan **🏠 Menu**; layar detail
juga punya **⬅️ Kembali** menuju daftar asal. Label mengikuti bahasa chat.

| command | contoh | fungsi |
|---|---|---|
| /start | /start | menu utama + statistik singkat |
| /latest [n] | /latest 3 | n rekaman terbaru |
| /search <words...> | /search sudais fajr | cari nama file, lima hasil per halaman |
| /date YYYY-MM-DD | /date 2026-10-28 | rekaman satu tanggal; YYYY-MM untuk satu bulan |
| /year [yyyy] | /year 2024 | telusuri per tahun/bulan |
| /stat | /stat | statistik arsip |
| /imaams | /imaams | katalog imam dan audio IMAAMS |
| /language | /language | pilih Indonesia atau English |
| /status [id] | /status | lihat unduhan pada chat ini |
| /cancel <id> | /cancel 12 | batalkan unduhan |
| /help | /help | panduan perintah |

## Peta tombol

Bot juga mengaktifkan tombol **Menu** bawaan Telegram di sebelah kolom pesan
pada chat privat. Tombol ini membuka daftar `/start`, `/latest`, `/search`,
`/date`, `/year`, `/stat`, `/imaams`, `/language`, `/status`, `/cancel`, dan `/help`. Perintah yang
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
 ├─ 🆕 Terbaru  ── 🔎 Cari
 └─ ❓ Bantuan  ── 🌐 Bahasa

Menu perintah Telegram
 ├─ /date     → tanggal atau bulan tertentu
 ├─ /year     → tahun → bulan → rekaman berhalaman
 ├─ /stat     → jumlah, jenis media, periode, dan waktu pembaruan
 ├─ /imaams   → imam → audio berhalaman
 └─ /status, /cancel, /help, /language, /latest, /search, /start
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

## Quran recitations — `/imaams`

`/imaams` membuka katalog **IMAAMS QU'RAN RECITATIONS** dari homepage
[Haramain](https://www.haramain.info/). Perintah ini tersedia di daftar
perintah bawaan Telegram, **bukan tombol tambahan di layar `/start`**.
Pilih imam → daftar audio, lima entri per halaman. Tombol judul membuka
file asli di Archive.org. Tombol **⬇️ Unduh** di sampingnya mengunduh satu
rekaman dan mengunggahnya ke chat Telegram yang sama. Judul ditampilkan satu
kali pada tombol; label panjang diringkas tanpa mengubah judul atau URL aslinya.
Tombol Daftar imam memulihkan halaman daftar imam sebelumnya, dan **🏠 Menu**
tersedia pada setiap layar katalog. Jika katalog gagal dimuat, **🔄 Coba lagi**
mencoba halaman audio yang sama. Respons metadata yang terlambat diabaikan
setelah pengguna kembali ke daftar imam atau menu utama.

Tombol unduh terikat ke chat asal dan berlaku 15 menit. Unduhan memakai
antrean, batas, dan konfigurasi MTProto yang sama dengan unduhan dari hasil
arsip blog (lihat bagian berikut). Membuka katalog atau tautan asli tidak
memerlukan konfigurasi MTProto.

Katalog ini terpisah dari posting blog dan `archive.db`: daftar imam
tersimpan di `bot/imaams.py`, layar di `bot/imaams_ui.py`, dan metadata
file di `imaams-cache.db` pada root repository. Tidak perlu API key.
Metadata diperbarui sesuai kebutuhan melalui public Metadata API Archive.org;
cache segar berlaku enam jam. Saat jaringan bermasalah, metadata lama yang
sudah tersimpan tetap dapat dipakai. Worker metadata terpisah dari polling
dan antrean unduhan.
Refresh ini tidak bergantung pada `DB_UPDATE_TIME`. Direktori repository
harus writable; tidak ada env baru atau perubahan startup yang diperlukan.

## Download dari hasil arsip

Hasil terbaru, pencarian, tanggal, dan bulan menampilkan tombol nama rekaman,
tanpa daftar teks tautan yang berulang. Klik nama rekaman untuk membuka
detail: **🔗 Sumber** membuka halaman asal, **⬇️ Unduh** mengirim file ke chat
yang sama, dan **⬅️ Kembali** memulihkan daftar serta halaman sebelumnya.
Unduh ditempatkan pada baris utama; tautan sumber/YouTube maksimal dua per
baris. Daftar semua video YouTube dibuka pada pesan yang sama, dengan tombol
Kembali ke detail sebelum kembali ke daftar rekaman. Tidak perlu menyalin URL.
Perintah `/dl` untuk URL bebas sudah dinonaktifkan dan dihapus dari menu serta
daftar perintah Telegram.

Tombol hanya merujuk URL arsip yang ditampilkan, terikat pada chat asal,
dan berlaku 15 menit. Setelah restart atau kedaluwarsa, buka hasil arsip
lagi untuk mendapatkan tombol baru. Mengimpor ulang arsip tidak mengubah
URL tujuan tombol yang masih berlaku. Maksimal dua unduhan berjalan per chat.
Backend download memakai Python dengan batas ukuran dan ruang disk.
Bot memakai dua worker tetap dan membatasi seluruh job aktif/menunggu melalui
`LEECH_QUEUE_SIZE` (default 10). Maksimal dua job per chat; permintaan rekaman
yang sama dalam satu chat ditolak selama masih aktif atau menunggu. Fase
upload tetap satu per satu. Folder setiap job unik, dan slot dibersihkan
walaupun pesan notifikasi terakhir gagal. Antrean tidak persisten setelah restart.
Upload membutuhkan TELEGRAM_API, TELEGRAM_HASH dan BOT_TOKEN/USER_SESSION_STRING.
Standalone `python3 leech.py <url>` tetap tersedia untuk operator server.

Setiap job memiliki state queued/downloading/waiting_upload/uploading dan status
terminal succeeded/failed/cancelled. Di UI, status ditulis sesuai bahasa chat,
misalnya **Dalam antrean**, **Mengunduh**, **Membagi berkas**, **Menunggu giliran
unggah**, dan **Mengunggah**. Gunakan `/status` untuk antrean di chat ini,
`/status <id>` untuk satu job, dan `/cancel <id>` atau tombol Batalkan untuk
membatalkan. ID hanya berlaku dalam chat pemilik job. Pembatalan aktif bersifat
cooperative: koneksi/download/split/upload memeriksa cancel; operasi socket yang
sedang menunggu tetap tunduk pada deadline. File yang pengirimannya belum
terkonfirmasi tidak dihapus. Status diedit paling sering tiga detik sekali dan
teks identik tidak dikirim ulang.

Pesan status (yang bertombol Batalkan/Lihat status) dihapus setelah job selesai, jadi
tidak ada lagi teks "queued" duplikat. Yang tertinggal hanya satu kartu hasil:
nama file, jenis, ukuran, host, dan label **🔗 Buka link langsung** yang membuka
URL mp3/video penuh. URL lengkap tidak pernah dicetak sebagai teks biasa; ia
hanya menjadi target tautan. Kartu hanya dikirim untuk job yang berhasil — job
gagal atau dibatalkan memakai ringkasan singkat. Kegagalan menampilkan petunjuk
untuk mencoba lagi; hasil parsial menyebut jumlah berkas yang terkonfirmasi
terkirim dan nama berkas yang masih disimpan di server.

## Hosted / low-memory servers

```bash
bash start.sh      # foreground: logs go to stdout; redirect if you need a file
```

`start.sh` checks `requirements.txt` and pip-installs whatever is missing
(set `INSTALL_DEPS=0` to skip, e.g. on a machine with no package index),
bootstraps the database if it is missing (`BOOTSTRAP=0` to skip), then execs
the bot. There is no background mode, pidfile or logfile — let the hosting
panel manage the process.

Or use systemd (see `deploy/haramain-bot.service`). That system-wide template
requires an existing unprivileged `haramain` user/group; edit `User`, `Group`,
checkout path, and Python interpreter for your server, and grant that user write
access to the repository/runtime files. Do not run the service as root.

## Updating the archive

Daily refresh defaults to `03:00` UTC when `DB_UPDATE_TIME` is not present.
An **explicitly blank** `DB_UPDATE_TIME=` disables automatic refresh; a blank
value already saved in the hosting panel overrides `.env` and the code default.
No crontab, second terminal or keep-alive ping is required.

Before polling, the bot verifies that the SQLite file exposes every column it
queries. An existing but unusable file is refreshed synchronously when automatic
refresh is enabled; otherwise startup exits with a repair command instead of
failing later on the first query. A repair that still produces an unusable
schema is a startup error.

At startup, catch-up runs in the maintenance worker when `meta.built` is before
the most recent daily schedule, or when the import is at least
`DB_UPDATE_MAX_AGE_HOURS` old (default 24). Setting max age to `0` disables
startup catch-up only. New timestamps are UTC with an offset; legacy naive
import timestamps are interpreted as UTC.

A failed refresh retries after `DB_UPDATE_RETRY_SEC` (default 300 seconds),
then exponential backoff up to one hour. It does not kill polling or erase the
previous SQLite catalog. `DB_UPDATE_TIMEOUT_SEC` (default 7200 seconds) bounds
the full pipeline; timed-out shells and their children are terminated together.

`bash start.sh` is the single foreground startup command. Manual and scheduled
refreshes use the same `.update.lock`, interpreter and normalized paths.
Logs include `[update] sitemap/feed/build/import: started/complete` and a final
success/failure line. `.update-status.json` records state, stage, timestamps and
last success without tokens or raw network output.

```bash
python3 -m bot.update --status  # read-only local diagnostics
python3 -m bot.update           # manual refresh
```

A refresh is successful only after the import transaction commits. Existing
SQLite readers see the committed rows without restarting the bot. `SWEEP=1`
adds the optional slow page scrape. Runtime files, work cache and database
must be writable and fit within the hosting allocation.
