"""Configure Telegram's native command menu beside the message composer.

Reference: https://core.telegram.org/bots/api#setchatmenubutton
"""
COMMANDS = (
    ('start', 'Open menu', 'Buka menu'),
    ('latest', 'Latest recordings', 'Rekaman terbaru'),
    ('search', 'Search recordings', 'Cari rekaman'),
    ('date', 'Browse recordings by date', 'Cari rekaman berdasarkan tanggal'),
    ('year', 'Browse years and months', 'Telusuri tahun dan bulan'),
    ('stat', 'Archive statistics', 'Statistik arsip'),
    ('imaams', 'Browse Quran reciters and audio', 'Cari imam dan rekaman Quran'),
    ('language', 'Choose English or Indonesian', 'Pilih bahasa Inggris atau Indonesia'),
    ('help', 'Command help', 'Bantuan perintah'),
)


def command_list(code):
    if code not in ('en', 'id'):
        raise ValueError('unsupported language')
    return [{'command': name, 'description': en if code == 'en' else id_}
            for name, en, id_ in COMMANDS]


def configure_menu(token, api, *, chat=None, code='id', private=False):
    """Publish commands and read back the selected scope and button."""
    scope = {'type': 'default'} if chat is None else {'type': 'chat', 'chat_id': chat}
    # Override both language variants for a chat: explicit app preference wins
    # over the Telegram client's own language setting.
    for locale in ('', 'en', 'id'):
        selected = code if chat is not None or not locale else locale
        payload = {'scope': scope, 'language_code': locale,
                   'commands': command_list(selected)}
        api(token, 'setMyCommands', payload)
    actual = api(token, 'getMyCommands', {'scope': scope, 'language_code': ''})
    if actual != command_list(code):
        raise RuntimeError('Telegram command menu verification failed')
    if chat is None or private:
        target = {} if chat is None else {'chat_id': chat}
        api(token, 'setChatMenuButton', {**target, 'menu_button': {'type': 'commands'}})
        actual = api(token, 'getChatMenuButton', target)
        if actual.get('type') != 'commands':
            raise RuntimeError('Telegram menu button verification failed')
