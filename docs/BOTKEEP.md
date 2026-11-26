# Deploy di Botkeep

## Startup tunggal

Gunakan direktori checkout sebagai working directory dan startup command:

```bash
bash start.sh
```

Jangan jalankan di background/nohup dan jangan tambahkan `run`, `init`, atau
`start` sebagai argumen. Panel hosting mengelola proses foreground tersebut.
Script memvalidasi konfigurasi, memeriksa dependency, bootstrap database yang
belum ada, lalu `exec` bot. Output Python tidak dibuffer.

## Environment

Simpan token melalui Environment/Secrets panel, bukan di Git:

```dotenv
BOT_TOKEN=<token bot milikmu>
DB_UPDATE_TIME=03:00
DB_UPDATE_MAX_AGE_HOURS=24
DB_UPDATE_RETRY_SEC=300
DB_UPDATE_TIMEOUT_SEC=7200
DB_UPDATE_PRUNE=0
```

`DB_UPDATE_TIME` memakai **UTC**. Jika variabel tidak ada, default kode adalah
`03:00`. Jika panel lama masih menyimpan nilai kosong, auto-update **dimatikan**;
ubah nilainya menjadi `03:00` lalu restart proses. Environment panel menang atas
file `.env`, termasuk nilai kosong. Jangan membagikan token lewat Console/chat.

Browsing arsip tidak membutuhkan MTProto. Tombol unduh/unggah membutuhkan
`TELEGRAM_API` (integer positif), `TELEGRAM_HASH` (32 karakter hexadecimal), dan
`BOT_TOKEN` atau `USER_SESSION_STRING` sesuai kebutuhan akun.

## Alur update yang seharusnya terlihat

1. Startup: `daily database update scheduled at ... UTC`.
2. DB yang ada tetapi skemanya rusak/tidak lengkap diperbaiki sinkron sebelum
   polling. Jika refresh gagal, startup berhenti dengan pesan jelas.
3. Jika import terakhir sebelum jadwal yang terlewat atau lebih tua dari max age,
   worker segera melakukan catch-up tanpa menghalangi polling.
4. Console menampilkan `[update] sitemap/feed/build/import: started/complete`.
5. Sukses: `[update] complete: database import committed`.
6. Gagal: status tahap tercatat, database lama tidak dikosongkan, worker retry
   setelah 300 detik; kegagalan berikutnya memakai backoff hingga satu jam.
7. Jadwal harian dilanjutkan setelah sukses. Tidak diperlukan cron/ping eksternal.

Perintah diagnosis lokal (tidak memanggil Telegram atau scrape):

```bash
python3 -m bot.update --status
```

Hasil JSON berisi `state`, `stage`, `started_at`, `finished_at`, dan `last_success`
jika pernah sukses. `never_run` berarti updater belum menulis status pada checkout
itu. `running` yang tertinggal setelah crash bukan bukti proses masih hidup;
lock OS menentukan apakah refresh lain sedang aktif.

Refresh manual tetap tersedia:

```bash
python3 -m bot.update
```

## File dan kapasitas

- Database SQLite adalah file lokal (`archive.db`), bukan managed database lain.
- Checkout/runtime harus writable. Path relatif diselesaikan terhadap root repo,
  sehingga startup, jadwal dan refresh manual selalu menuju file yang sama.
- Jangan simpan `WORK`, `OUT`/`DUMP`, atau `DB_PATH` dalam direktori yang saling
  menimpa. `OUT` harus direktori dump khusus.
- Jangan hapus `archive.db`, `work/`, atau `haramain/` saat proses update berjalan.
- `DB_UPDATE_PRUNE=1` menghapus `work/` dan `haramain/` otomatis **setelah** import
  ter-commit dan terverifikasi. Default mati. Ini hemat ~200 MB untuk storage 1 GB,
  dengan konsekuensi refresh berikutnya mengambil ulang semuanya dari jaringan
  (bukan dari cache) dan `run.sh build/check` tidak bisa dijalankan tanpa fetch
  ulang. Kegagalan prune tidak pernah menggagalkan update, dan database serta
  `downloads/` tidak pernah tersentuh.
- `SWEEP` default mati; aktifkan hanya jika memang membutuhkan scrape lambat.
- Cache feed, staging dump, SQLite WAL dan file unduhan butuh ruang bersamaan.
  Monitor storage; backup data penting sebelum mengganti source/reinstall.
- Mengganti source saja belum mengubah proses Python yang sedang berjalan;
  pastikan file baru sudah di server lalu restart lewat panel.

## Batas verifikasi

Test repo menjalankan server HTTP lokal dan seluruh pipeline subprocess sampai
commit SQLite, termasuk failure/retry/rollback/timeout. Smoke test sumber publik
memastikan feed bisa diparse pada saat pengujian. Keduanya tidak memberikan
akses ke panel Botkeep: status deployment, alokasi, izin file dan jaringan
server hosting tetap harus dilihat dari Console serta hasil diagnosis di sana.
