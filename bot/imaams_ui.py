"""IMAAMS screens, separate from the harvested blog archive UI."""
from bot.i18n import translate
from bot.imaams import RECITERS
from bot.ui import HOME, compact_text

PAGE_SIZE = 5


def _page(total, requested):
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(requested, 1), pages)
    return page, pages, (page - 1) * PAGE_SIZE


def _navigation(page, pages, prefix):
    buttons = []
    if page > 1:
        buttons.append({'text': translate('⬅️ Back'), 'callback_data': f'{prefix}:{page-1}'})
    buttons.append({'text': f'📄 {page}/{pages}', 'callback_data': 'im:x'})
    if page < pages:
        buttons.append({'text': translate('Next ➡️'), 'callback_data': f'{prefix}:{page+1}'})
    return buttons


def reciter_page(page):
    page, pages, start = _page(len(RECITERS), page)
    rows = [[{'text': compact_text(reciter['name']), 'callback_data': f'im:r:{index}:1'}]
            for index, reciter in enumerate(RECITERS[start:start+PAGE_SIZE], start)]
    rows.append(_navigation(page, pages, 'im:list'))
    rows.append([HOME])
    position = translate('Page {page}/{pages}').format(page=page, pages=pages)
    return (f"🎙 IMAAMS · {translate('Quran recitations')}\n"
            f"{len(RECITERS)} {translate('reciters')} · {position}\n\n"
            f"{translate('Choose a reciter:')}", {'inline_keyboard': rows})


def files_page(index, page, catalog, remember_downloads):
    if not 0 <= index < len(RECITERS):
        raise ValueError('unknown reciter')
    reciter = RECITERS[index]
    files = catalog.files(reciter['identifier'])
    page, pages, start = _page(len(files), page)
    rows = []
    visible = files[start:start+PAGE_SIZE]
    keys = remember_downloads([{'url': file['url']} for file in visible])
    for file, key in zip(visible, keys):
        try:
            size = max(0, int(file.get('size') or 0))
        except (ValueError, TypeError, OverflowError):
            size = 0
        size_text = f' · {size / 1024 / 1024:.1f} MB' if size else ''
        label = compact_text(file.get('title') or file.get('name'),
                             limit=60 - len(size_text), fallback=translate('Recording'))
        buttons = [{'text': label + size_text, 'url': file['url']}]
        if key:
            buttons.append({'text': '⬇️ Download', 'callback_data': 'get:' + key})
        rows.append(buttons)
    rows.append(_navigation(page, pages, f'im:r:{index}'))
    rows.append([{'text': '⬅️ Reciters', 'callback_data': f'im:rlist:{index//PAGE_SIZE+1}'}, HOME])
    position = translate('Page {page}/{pages}').format(page=page, pages=pages)
    instruction = translate('Choose a recording:' if files else 'No audio is available for this reciter yet.')
    return (f"🎙 {reciter['name']}\n{len(files)} {translate('audio tracks')} · "
            f"{position}\n\n{instruction}", {'inline_keyboard': rows})


def render(data, catalog, remember_downloads):
    if data == 'imaams':
        return reciter_page(1)
    parts = data.split(':')
    if len(parts) == 3 and parts[:2] in (['im', 'list'], ['im', 'rlist']):
        return reciter_page(int(parts[2]))
    if len(parts) == 4 and parts[:2] == ['im', 'r']:
        return files_page(int(parts[2]), int(parts[3]), catalog, remember_downloads)
    return None


def unavailable(data=None, *, busy=False):
    """Offer a retry and return to the selected reciter's original list page."""
    retry, origin = 'im:list:1', 1
    parts = (data or '').split(':')
    if len(parts) == 4 and parts[:2] == ['im', 'r']:
        try:
            index, page = int(parts[2]), int(parts[3])
            if 0 <= index < len(RECITERS) and page > 0:
                retry, origin = f'im:r:{index}:{page}', index // PAGE_SIZE + 1
        except ValueError:
            pass
    message = ('The reciter catalog is busy. Please try again shortly.' if busy else
               'Reciter catalog is temporarily unavailable. Try again later.')
    return (translate(message), {'inline_keyboard': [
        [{'text': translate('🔄 Try again'), 'callback_data': retry}],
        [{'text': '⬅️ Reciters', 'callback_data': f'im:rlist:{origin}'}, HOME],
    ]})
