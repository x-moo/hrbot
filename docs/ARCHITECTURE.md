# Arsitektur dan kontrak runtime

## Lapisan

- `config.py`: dotenv sebagai data, schema `Settings` immutable, validasi finite
  number/jam UTC/kredensial opsional, path relatif terhadap checkout.
- `start.sh`: satu entrypoint foreground; validasi → dependency → bootstrap DB
  bila hilang → `exec` bot dengan log tidak dibuffer.
- `bot/app.py`: routing command/callback dan UI katalog. `main()` menyiapkan
  resource, memverifikasi skema database sebelum polling, dan `poll_updates()`
  menjalankan polling. Cache callback tetap opaque,
  chat-bound dan memiliki TTL, bukan URL mentah di `callback_data`.
- `bot/jobs.py`: registry job bertipe dan transisi state di bawah lock.
- `bot/downloads.py`: admission, eksekusi worker, status, cancel dan terminal
  notification; transport Telegram di-inject agar test tidak membutuhkan token.
- `leech/control.py`: cancellation cooperative dan stage callback lewat
  `ContextVar`, tanpa global socket monkeypatch.
- `leech/downloader.py`, `network.py`: streaming, ukuran/disk/deadline, validasi
  setiap redirect, DNS pinning untuk link arsip tidak tepercaya.
- `leech/cli.py`, `session.py`: orkestrasi split/MTProto; lifecycle upload
  diserialisasi dan timeout ambigu tidak dikirim ulang secara buta.
- `bot/imaams.py` / `imaams_ui.py`: katalog reciter dan cache metadata terpisah
  dari arsip blog, memakai layanan download yang sama.
- `bot/scheduler.py`: state machine jadwal UTC yang dapat di-step tanpa thread
  dalam test; retry terbatas backoff, wait bisa dihentikan.
- `bot/update.py`: lock lintas proses, deadline keseluruhan, subprocess group,
  stage logging dan status atomik.
- `pipeline/`: harvest sitemap/RSS/page scrape → dump tervalidasi.
- `archive/import_dump.py`: fungsi reusable `import_catalog()`; DELETE/INSERT,
  indeks dan `meta.built` UTC dalam satu transaksi WAL.
- `archive/repository.py`: query read-only; statistik di-cache berdasarkan
  `PRAGMA data_version` dan invalidasi pada commit eksternal.

## Auto-update

```text
start.sh
  ├─ validasi dan dependency
  ├─ DB hilang → python -m bot.update (bootstrap)
  └─ exec bot.py
       ├─ polling Telegram (main thread)
       └─ DailyWorker (maintenance thread)
            ├─ startup: terakhir import < jadwal terakhir ATAU umur >= max age
            ├─ daily: HH:MM UTC
            └─ failure: retry 300s → 600s → ... → maksimum 3600s
                 └─ run_update()
                      ├─ flock(.update.lock)
                      ├─ sitemap → feed → [sweep] → build → import
                      ├─ batas waktu bersama; bunuh process group saat timeout
                      └─ .update-status.json: succeeded/failed + tahap
```

`DB_UPDATE_TIME` tidak ada berarti `03:00`; nilai kosong eksplisit berarti mati.
`DB_UPDATE_MAX_AGE_HOURS=0` hanya mematikan startup catch-up. Callback maintenance
synchronous pada satu thread, sehingga retry tidak menumpuk. Update manual juga
mengambil lock yang sama. Lock fd diwariskan ke anak agar refresh lain tidak
menimpa file yang masih dipakai anak jika updater induk mati.

Importer tidak mengganti inode file database: pembaca bot tetap terhubung ke file
yang sama. Kegagalan INSERT mengembalikan katalog lama. Timestamp keberhasilan
dan isi media tidak dapat ter-commit terpisah. Pilihan ini berbeda dari dump
folder yang dipublikasikan lewat staging/rollback.

## Kontrak keamanan dan resource

- Dua worker transfer tetap, kapasitas global mencakup aktif dan menunggu,
  maks dua job per chat dan duplikat chat/URL ditolak.
- Pembatalan terikat chat; tidak pernah memulai unduhan dari command `/dl` bebas.
- Partial download/split yang gagal dibersihkan; file upload yang tidak mendapat
  konfirmasi Telegram tetap disimpan. Status final harus membedakan cancelled,
  gagal, dan partial success.
- Tidak ada retry generic untuk upload yang hasilnya ambigu. Hanya FloodWait
  eksplisit/reject tipe media yang mendapat retry/fallback terkontrol.
- Memisahkan config, UI, job, transfer dan maintenance tidak mengharuskan semua
  kode menjadi asyncio. Polling Bot API tetap sederhana, operasi berat pada
  bounded workers, sedangkan lifecycle MTProto mempunyai event loop per transfer.
- Tidak menambahkan dependency berat milik bot mirror umum. Pola ditulis ulang;
  kode substantif repo referensi tidak disalin.

## Verifikasi

`python3 -m unittest discover -s tests -v` meliputi regresi lama dan test baru:
missed schedule <24 jam, retry/backoff, stop worker, process-group timeout,
lock surviving child, rollback impor dengan pembaca tetap terbuka, serta HTTP
lokal → pipeline subprocess sebenarnya → commit SQLite → failure → retry.
Test transfer memakai sumber HTTP lokal dan transport MTProto terkontrol;
bukan pengiriman ke akun Telegram nyata.

Panduan operasional hosting: [BOTKEEP.md](BOTKEEP.md).
