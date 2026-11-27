"""Per-chat language preferences and localization of bot UI strings."""
from contextvars import ContextVar
from pathlib import Path
import re
import sqlite3

from config import ROOT

language = ContextVar('language', default='id')


class Preferences:
    """Store preferences separately from the replaceable archive database."""

    def __init__(self, path=ROOT / 'preferences.db'):
        self.path = Path(path)

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.execute('CREATE TABLE IF NOT EXISTS languages (chat INTEGER PRIMARY KEY, code TEXT NOT NULL)')
        return db

    def get(self, chat):
        with self._connect() as db:
            row = db.execute('SELECT code FROM languages WHERE chat=?', (chat,)).fetchone()
        db.close()
        return row[0] if row else 'id'

    def set(self, chat, code):
        if code not in ('en', 'id'):
            raise ValueError('unsupported language')
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO languages VALUES (?, ?)', (chat, code))
        db.close()


# English, Indonesian. Original mixed-language screens are normalized first.
PHRASES = [
    ('Haramain media archive', 'Arsip media Haramain'),
    ('More options are available in Telegram\'s command menu.',
     'Perintah lainnya tersedia di menu perintah Telegram.'),
    ('Choose your language.', 'Pilih bahasa yang ingin digunakan.'),
    ('Language', 'Bahasa'),
    ('For a full month:', 'Untuk satu bulan:'),
    ('Use /start for the main menu. Other commands are available in Telegram\'s command menu.',
     'Gunakan /start untuk menu utama. Perintah lainnya tersedia di menu perintah Telegram.'),
    ('Browse the archive', 'Telusuri arsip'),
    ('Quran recitations', 'Tilawah Al-Qur\'an'),
    ('Downloads', 'Unduhan'),
    ('Preferences', 'Pengaturan'),
    ('search recordings', 'cari rekaman'),
    ('recordings for a date', 'rekaman pada tanggal tertentu'),
    ('browse years and months', 'telusuri tahun dan bulan'),
    ('browse reciters and audio', 'telusuri imam dan rekaman audio'),
    ('view downloads in this chat', 'lihat unduhan di chat ini'),
    ('cancel a download', 'batalkan unduhan'),
    ('choose language', 'pilih bahasa'),
    ('open the main menu', 'buka menu utama'),
    ('Select a recording, then tap Download to receive it in this chat.',
     'Pilih rekaman, lalu tekan Unduh untuk menerima berkas di chat ini.'),
    ('The archive is empty. Please try again later.',
     'Arsip belum tersedia. Silakan coba lagi nanti.'),
    ('No recordings found.', 'Tidak ada rekaman yang ditemukan.'),
    ('Please enter a valid date.', 'Masukkan tanggal yang valid.'),
    ('Search again', 'Cari lagi'),
    ('All YouTube', 'Semua video YouTube'),
    ('Open a video on YouTube.', 'Pilih video untuk membukanya di YouTube.'),
    ('updated:', 'diperbarui:'),
    ('Page {page}/{pages}', 'Halaman {page}/{pages}'),
    ('result', 'hasil'), ('results', 'hasil'),
    ('year', 'tahun'),
    ('reciters', 'imam'),
    ('Try again', 'Coba lagi'),
    ('The reciter catalog is busy. Please try again shortly.',
     'Katalog imam sedang sibuk. Silakan coba lagi sebentar.'),
    ('Queued', 'Dalam antrean'),
    ('Downloading', 'Mengunduh'),
    ('Splitting files', 'Membagi berkas'),
    ('Waiting to upload', 'Menunggu giliran unggah'),
    ('Uploading', 'Mengunggah'),
    ('Completed', 'Selesai'),
    ('Failed', 'Gagal'),
    ('Cancelled', 'Dibatalkan'),
    ('Cancellation requested', 'Permintaan pembatalan diterima'),
    ('Cancel', 'Batalkan'),
    ('Status', 'Lihat status'),
    ('No queued or active downloads.', 'Tidak ada unduhan dalam antrean atau sedang berjalan.'),
    ('Download not found in this chat.', 'Unduhan tidak ditemukan di chat ini.'),
    ('Usage: /{action} <id>', 'Gunakan: /{action} <id>'),
    ('This chat already has 2 queued or active downloads.',
     'Chat ini sudah memiliki 2 unduhan dalam antrean atau sedang berjalan.'),
    ('Download failed. Please try again later.', 'Unduhan gagal. Coba lagi nanti.'),
    ('Could not post download status. Please try again later.',
     'Status unduhan tidak dapat dikirim. Coba lagi nanti.'),
    ('Downloader unavailable. Please try again later.',
     'Pengunduh tidak tersedia. Coba lagi nanti.'),
    ('Downloaded {size} in {seconds}s', 'Terunduh {size} dalam {seconds} dtk'),
    ('Downloaded {size}', 'Terunduh {size}'),
    ('Splitting {size} into {part_size} parts', 'Membagi {size} menjadi bagian berukuran {part_size}'),
    ('{count} parts ready', '{count} bagian siap'),
    ('Sent', 'Terkirim'),
    ('Temporary storage cleared', 'Penyimpanan sementara dibersihkan'),
    ('Files sent: {count}', 'Berkas terkirim: {count}'),
    ('Confirmed sent: {sent}/{total}', 'Terkonfirmasi terkirim: {sent}/{total}'),
    ('Files kept on server: {files}', 'Berkas disimpan di server: {files}'),
    ('Audio', 'Audio'), ('Video', 'Video'), ('Image', 'Gambar'), ('File', 'Berkas'),
    ('Recording', 'Rekaman'),
    ('Quran reciters (command menu)', 'daftar imam Quran (menu perintah)'),
    ('Choose a reciter:', 'Pilih imam:'),
    ('No audio is available for this reciter yet.', 'Belum ada audio untuk imam ini.'),
    ('Reciter catalog is temporarily unavailable. Try again later.',
     'Katalog imam sementara tidak tersedia. Coba lagi nanti.'),
    ('audio tracks', 'rekaman audio'),
    ('Reciters', 'Daftar imam'),
    ('This recording is already queued.', 'Rekaman ini sudah dalam antrean.'),
    ('Download queue is full. Try again later.', 'Antrean unduhan penuh. Coba lagi nanti.'),
    ('Download queue is unavailable.', 'Antrean unduhan tidak tersedia.'),
    ('waiting for upload slot', 'menunggu giliran unggah'),
    ('Choose a recording:', 'Pilih rekaman:'),
    ('Source', 'Sumber'),
    ('Click Download on an archive result to receive the file.',
     'Klik tombol Unduh pada hasil arsip untuk menerima berkas.'),
    ('downloads already running', 'unduhan sedang berjalan'),
    ('try again shortly.', 'coba lagi sebentar.'),
    ('cannot post status:', 'tidak dapat mengirim status:'),
    ('leech unavailable:', 'pengunduh tidak tersedia:'),
    ('starting', 'memulai'), ('started', 'dimulai'),
    ('failed:', 'gagal:'), ('done', 'selesai'), ('file(s)', 'berkas'),
    ('python: downloading', 'python: mengunduh'),
    ('downloading...', 'mengunduh...'), ('downloaded', 'terunduh'),
    ('uploading', 'mengunggah'), ('sent', 'terkirim'),
    ('storage freed:', 'penyimpanan dibebaskan:'),
    ('Open direct link', 'Buka link langsung'),
    ('No YouTube links for this recording.',
     'Belum ada link YouTube untuk rekaman ini.'),
    ('🌐 Language', '🌐 Bahasa'),
    ('Choose a menu below, or use commands directly', 'Pilih menu di bawah, atau pakai perintah langsung'),
    ('Search results show 5 per page — use ⬅️ Back / Next ➡️.', 'Hasil pencarian tampil 5 per halaman — gunakan ⬅️ Kembali / Lanjut ➡️.'),
    ('Assalamu\'alaikum 🕌 *haramain.info* media archive bot.', 'Assalamu\'alaikum 🕌 bot arsip media *haramain.info*.'),
    ('Assalamu\'alaikum 🕌 *haramain.info* media archive', 'Assalamu\'alaikum 🕌 arsip media *haramain.info*'),
    ('Press /start for the button menu — all commands below', 'Tekan /start untuk menu tombol — semua perintah di bawah ini'),
    ('can also be run using buttons.', 'juga bisa dijalankan tanpa mengetik.'),
    ('Search file names (e.g.', 'cari nama file (mis.'),
    ('latest recordings', 'rekaman terbaru'),
    ('media for one date', 'media satu tanggal'),
    ('browse by year → month', 'telusuri per tahun → bulan'),
    ('archive statistics', 'statistik arsip'),
    ('download a file', 'unduh file'),
    ('Long results show 5 per page with ⬅️ Back / Next ➡️ buttons.', 'Hasil panjang tampil 5 per halaman dengan tombol ⬅️ Back / Next ➡️.'),
    ('5 results per page; use Back/Next to browse.', 'Hasil 5 per halaman, geser pakai tombol Back/Next.'),
    ('Or browse by month using 🗓 Year.', 'Atau telusuri per bulan lewat tombol 🗓 Tahun.'),
    ('Variants:', 'Varian:'),
    ('(no preview)', '(tanpa preview)'),
    ('(post to the leech channel).', '(post ke channel leech).'),
    ('Send:', 'Kirim:'),
    ('e.g.', 'mis.'),
    ('Choose a year:', 'Pilih tahun:'),
    ('Choose a month:', 'Pilih bulan:'),
    ('Results have expired — run the command again.', 'Hasil sudah kadaluarsa — jalankan perintah lagi.'),
    ('Archive is empty — import first:', 'Arsip kosong — impor terlebih dahulu:'),
    ('No results. Try other keywords.', 'Tidak ada hasil. Coba kata kunci lain.'),
    ('Unknown command. Use /help for the list.', 'Perintah tidak dikenal. Lihat daftar di /help.'),
    ('No media found for ', 'Tidak ada media untuk '),
    ('No data for ', 'Tidak ada data untuk '),
    ('(no media on this page)', '(tidak ada media di halaman ini)'),
    ('Archive statistics', 'Statistik arsip'),
    ('unique URLs', 'URL unik'),
    ('total rows', 'total baris'),
    ('period:', 'periode:'),
    ('URLs across', 'URL dalam'),
    (' years ', ' tahun '),
    ('result(s)', 'hasil'),
    ('page ', 'halaman '),
    ('source', 'sumber'),
    ('Latest', 'Terbaru'),
    ('Search', 'Cari'),
    ('By date', 'Berdasarkan tanggal'),
    ('Years', 'Tahun'),
    ('Year', 'Tahun'),
    ('Months', 'Bulan'),
    ('Date', 'Tanggal'),
    ('Stat', 'Statistik'),
    ('Help', 'Bantuan'),
    ('Download', 'Unduh'),
    ('Back', 'Kembali'),
    ('Next', 'Lanjut'),
    ('Jan', 'Jan'), ('Feb', 'Feb'), ('Mar', 'Mar'), ('Apr', 'Apr'),
    ('May', 'Mei'), ('Jun', 'Jun'), ('Jul', 'Jul'), ('Aug', 'Agu'),
    ('Sep', 'Sep'), ('Oct', 'Okt'), ('Nov', 'Nov'), ('Dec', 'Des'),
]


def translate(text):
    """Translate UI only; protect URLs and literal command/code spans."""
    if not isinstance(text, str):
        return text
    target = language.get()
    pairs = {}
    for en, id_ in PHRASES:
        # Match words, leaving surrounding spaces in the source untouched.
        # A trailing space followed by a digit is not a word boundary.
        en, id_ = en.strip(), id_.strip()
        pairs[en] = en if target == 'en' else id_
        pairs[id_] = en if target == 'en' else id_
    pattern = re.compile('|'.join(r'(?<!\w)' + re.escape(key) + r'(?!\w)'
                                  for key in sorted(pairs, key=len, reverse=True)))
    protected = re.split(r'(\[[^\]]*\]\([^)]*\)|`[^`]*`|https?://[^\s)]+)', text)
    return ''.join(part if index % 2 else pattern.sub(lambda match: pairs[match[0]], part)
                   for index, part in enumerate(protected))


def localize_markup(markup):
    if not markup:
        return markup

    def label(button):
        data = button.get('callback_data', '')
        # URL labels and record/reciter names are content, not UI copy. Static
        # URL actions (such as Source) are localized when they are constructed.
        if button.get('url') or data.startswith(('item:', 'im:r:')):
            return button['text']
        if data.startswith('get:') and ' · ' in button['text']:
            return translate('⬇️ Download') + ' · ' + button['text'].split(' · ', 1)[1]
        return translate(button['text'])

    return {**markup, 'inline_keyboard': [
        [{**button, 'text': label(button)} for button in row]
        for row in markup['inline_keyboard']]}
