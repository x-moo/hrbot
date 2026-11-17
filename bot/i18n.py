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
    ('🌐 Language', '🌐 Bahasa'),
    ('Choose a menu below, or use commands directly', 'Pilih menu di bawah, atau pakai perintah langsung'),
    ('Search results show 5 per page — use ⬅️ Back / Next ➡️.', 'Hasil pencarian muncul 5 per halaman — tombol ⬅️ Back / Next ➡️.'),
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
    ('Or browse by month using 🗓 Year.', 'Atau telusuri per bulan lewat tombol 🗓 Year.'),
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
    return {**markup, 'inline_keyboard': [
        [{**button, 'text': (translate('⬇️ Download') + ' · ' + button['text'].split(' · ', 1)[1]
                            if button.get('callback_data', '').startswith('get:') and ' · ' in button['text']
                            else button['text'] if button.get('callback_data', '').startswith('item:')
                            else translate(button['text']))} for button in row]
        for row in markup['inline_keyboard']]}
