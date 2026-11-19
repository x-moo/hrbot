"""IMAAMS screens, separate from the harvested blog archive UI."""
from bot.i18n import translate
from bot.imaams import RECITERS

PAGE_SIZE = 5


def _page(total, requested):
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(requested, 1), pages)
    return page, pages, (page - 1) * PAGE_SIZE


def _navigation(page, pages, prefix):
    buttons = []
    if page > 1:
        buttons.append({'text': '⬅️ Back', 'callback_data': f'{prefix}:{page-1}'})
    buttons.append({'text': f'{page}/{pages}', 'callback_data': 'im:x'})
    if page < pages:
        buttons.append({'text': 'Next ➡️', 'callback_data': f'{prefix}:{page+1}'})
    return buttons


def reciter_page(page):
    page, pages, start = _page(len(RECITERS), page)
    rows = [[{'text': reciter['name'], 'callback_data': f'im:r:{index}:1'}]
            for index, reciter in enumerate(RECITERS[start:start+PAGE_SIZE], start)]
    rows.append(_navigation(page, pages, 'im:list'))
    return (f"🎙 IMAAMS Quran Recitations\n{translate('Choose a reciter:')} {page}/{pages}",
            {'inline_keyboard': rows})


def files_page(index, page, catalog, remember_downloads):
    if not 0 <= index < len(RECITERS):
        raise ValueError('unknown reciter')
    reciter = RECITERS[index]
    files = catalog.files(reciter['identifier'])
    page, pages, start = _page(len(files), page)
    rows, lines = [], []
    visible = files[start:start+PAGE_SIZE]
    keys = remember_downloads([{'url': file['url']} for file in visible])
    for file, key in zip(visible, keys):
        label = str(file.get('title') or file['name']).replace('\n', ' ')[:180]
        try:
            size = max(0, int(file.get('size') or 0))
        except (ValueError, TypeError, OverflowError):
            size = 0
        size_text = f' · {size / 1024 / 1024:.1f} MB' if size else ''
        buttons = [{'text': (label + size_text)[:64], 'url': file['url']}]
        if key:
            buttons.append({'text': '⬇️ Download', 'callback_data': 'get:' + key})
        rows.append(buttons)
        lines.append('• ' + label)
    if not files:
        lines.append(translate('No audio is available for this reciter yet.'))
    rows.append(_navigation(page, pages, f'im:r:{index}'))
    rows.append([{'text': '⬅️ Reciters', 'callback_data': f'im:rlist:{index//PAGE_SIZE+1}'}])
    return (f"🎙 {reciter['name']}\n{len(files)} {translate('audio tracks')} · "
            f"{translate('page ')}{page}/{pages}\n" + '\n'.join(lines), {'inline_keyboard': rows})


def render(data, catalog, remember_downloads):
    if data == 'imaams':
        return reciter_page(1)
    parts = data.split(':')
    if len(parts) == 3 and parts[:2] in (['im', 'list'], ['im', 'rlist']):
        return reciter_page(int(parts[2]))
    if len(parts) == 4 and parts[:2] == ['im', 'r']:
        return files_page(int(parts[2]), int(parts[3]), catalog, remember_downloads)
    return None


def unavailable():
    return (translate('Reciter catalog is temporarily unavailable. Try again later.'),
            {'inline_keyboard': [[{'text': '⬅️ Reciters', 'callback_data': 'im:rlist:1'}]]})
