"""Bounded transfer scheduling and plain-text Telegram download UI."""
from collections import deque
from contextvars import copy_context
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit
import tempfile
import time
import re
import os

from bot.i18n import translate
from bot.jobs import Job, JobRejected, JobState
from bot.telegram import NO_PREVIEW, TgError
from leech.utils import media_kind, redact


WORKERS = 2


def escape_markdown(text):
    """Escape untrusted progress for the existing Markdown notification seam."""
    return re.sub(r'([\\`*_\[\]])', r'\\\1', str(text))


def error_summary(error):
    """Do not copy arbitrary exception text (URLs, secrets, paths) to chat."""
    return redact(type(error).__name__)


def download_root(root):
    configured = Path(os.environ.get('DOWNLOAD_DIR') or 'downloads')
    return configured if configured.is_absolute() else Path(root) / configured


def mask_url(url):
    """Display host and filename only; never credentials, query, or fragment."""
    parsed = urlsplit(url)
    host = parsed.hostname or 'recording'
    name = parsed.path.rsplit('/', 1)[-1]
    return (f'{host}/…/{name}' if name else host)[:200]


_SIZE_RE = re.compile(r'^downloaded (\d[\d.]* [KMGT]?B) in ')
_PROGRESS_SIZE = r'(\d+(?:\.\d+)? [KMGT]?B)'
_STATE_LABELS = {
    'queued': 'Queued',
    'downloading': 'Downloading',
    'splitting': 'Splitting files',
    'waiting_upload': 'Waiting to upload',
    'uploading': 'Uploading',
    'succeeded': 'Completed',
    'failed': 'Failed',
    'cancelled': 'Cancelled',
}
_KIND_LABELS = {'audio': 'Audio', 'video': 'Video', 'photo': 'Image', 'doc': 'File'}
_ADMISSION_MESSAGES = {
    'This recording is already queued.',
    'Download queue is full. Try again later.',
    'This chat already has 2 queued or active downloads.',
}


def clip_text(text, units):
    """Keep Telegram's UTF-16 budget without cutting a surrogate pair."""
    return text.encode('utf-16-le')[:units * 2].decode('utf-16-le', errors='ignore')


def progress_text(line):
    """Present known English pipeline landmarks without changing their raw data."""
    line = str(line)
    if line == 'python: downloading':
        return translate('Downloading')
    if line == 'waiting for upload slot':
        return translate('Waiting to upload')
    match = re.fullmatch(r'downloading\.\.\. (\d+)%', line)
    if match:
        return f'{translate("Downloading")}… {match[1]}%'
    match = re.fullmatch(r'downloaded ' + _PROGRESS_SIZE + r' in ([\d.]+)s', line)
    if match:
        return translate('Downloaded {size} in {seconds}s').format(
            size=match[1], seconds=match[2])
    match = re.fullmatch(r'downloaded ' + _PROGRESS_SIZE, line)
    if match:
        return translate('Downloaded {size}').format(size=match[1])
    match = re.fullmatch(r'splitting ' + _PROGRESS_SIZE + r' into ' +
                         _PROGRESS_SIZE + r' parts', line)
    if match:
        return translate('Splitting {size} into {part_size} parts').format(
            size=match[1], part_size=match[2])
    match = re.fullmatch(r'(\d+) parts ready', line)
    if match:
        return translate('{count} parts ready').format(count=match[1])
    match = re.fullmatch(r'uploading (\d+)/(\d+) · (.+) · ' +
                         _PROGRESS_SIZE + r' · (audio|video|image|file)', line)
    if match:
        kind = {'audio': 'Audio', 'video': 'Video', 'image': 'Image', 'file': 'File'}[match[5]]
        return (f'{translate("Uploading")} {match[1]}/{match[2]} · {match[3]} · '
                f'{match[4]} · {translate(kind)}')
    match = re.fullmatch(r'sent (\d+)/(\d+) ✓', line)
    if match:
        return f'{translate("Sent")} {match[1]}/{match[2]} ✓'
    match = re.fullmatch(r'storage freed: ' + _PROGRESS_SIZE, line)
    if match:
        return f'{translate("Temporary storage cleared")} · {match[1]}'
    match = re.fullmatch(r'done: (\d+) file\(s\) in ([\d.]+)s', line)
    if match:
        return (translate('Files sent: {count}').format(count=match[1]) +
                f' · {match[2]}s')
    # Preserve unrecognized data verbatim: translating it could rename a file.
    return line


def format_bytes(count):
    value = float(count)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} B' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} GB'


class DownloadService:
    def __init__(self, *, registry, executor, api, notify, root, card=None):
        self.registry = registry
        self.executor = executor
        self.api = api
        self.notify = notify
        self.card = card
        self.root = root
        self._pending = deque()
        self._notices = deque(maxlen=100)
        self._dispatch_lock = RLock()
        self._workers = 0

    def markup(self, job):
        if job.state.terminal:
            return {'inline_keyboard': []}
        return {'inline_keyboard': [[
            {'text': translate('Cancel'), 'callback_data': f'cancel:{job.id}'},
            {'text': translate('Status'), 'callback_data': f'status:{job.id}'}]]}

    @staticmethod
    def state_label(job):
        stage = job.state.value if job.state.terminal else job.stage
        return translate(_STATE_LABELS.get(stage, _STATE_LABELS[job.state.value]))

    def header(self, job):
        state = self.state_label(job)
        if job.cancel_event.is_set() and not job.state.terminal:
            state += ' · ' + translate('Cancellation requested')
        return f'⬇️ #{job.id} · {state} · {mask_url(job.url)}'

    def text(self, job):
        header = clip_text(self.header(job), 3900)
        remaining = 3900 - len(header.encode('utf-16-le')) // 2
        newest = []
        for raw in reversed(job.lines[-10:]):
            line = progress_text(raw)
            if remaining <= 1:
                break
            shown = clip_text(line, remaining - 1)
            newest.append(shown)
            remaining -= 1 + len(shown.encode('utf-16-le')) // 2
            if shown != line:
                break
        return '\n'.join([header, *reversed(newest)])

    def status(self, job):
        """Best-effort, rate-limited edits; never sleep or retry flood waits."""
        with job.ui_lock:
            text = self.text(job)
            now = time.monotonic()
            if (job.status_disabled or job.message_id is None or text == job.last_text or
                    now - job.last_edit < 3):
                return
            job.last_edit = now
            try:
                self.api(job.token, 'editMessageText', {
                    'chat_id': job.chat, 'message_id': job.message_id,
                    'text': text, **NO_PREVIEW, 'reply_markup': self.markup(job)},
                    tries=0)
                job.last_text = text
            except TgError as error:
                description = error.description.lower()
                if any(phrase in description for phrase in (
                        'message to edit not found', "message can't be edited",
                        'message_id_invalid', 'chat not found', 'bot was blocked')):
                    job.status_disabled = True
                elif 'not modified' in description:
                    job.last_text = text
                else:
                    print(f'download #{job.id}: status {error_summary(error)}', flush=True)
            except Exception as error:
                print(f'download #{job.id}: status {error_summary(error)}', flush=True)

    @staticmethod
    def _link(url, label):
        """Markdown link whose target keeps the full URL out of the visible text."""
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return ''
        # Parentheses and spaces would break the [text](url) syntax.
        target = (str(url).replace('(', '%28').replace(')', '%29')
                  .replace(' ', '%20').replace('\n', ''))
        return f'🔗 [{label}]({target})'

    @staticmethod
    def _kind(name):
        kind = media_kind(name)
        icon = {'audio': '🎙️', 'video': '🎬', 'photo': '🖼️', 'doc': '📦'}[kind]
        return f'{icon} {translate(_KIND_LABELS[kind])}'

    @staticmethod
    def _size(job):
        """Downloaded size from the pipeline's own progress line, else the file."""
        for line in reversed(job.lines):
            match = _SIZE_RE.match(str(line))
            if match:
                return match.group(1)
        total = 0
        for path in job.files:
            try:
                total += Path(path).stat().st_size
            except OSError:
                pass
        return format_bytes(total) if total else ''

    def card_text(self, job):
        """Completion card: what was saved plus a clickable direct link."""
        names = [Path(path).name for path in job.files]
        shown = ', '.join(names[:3])
        if len(names) > 3:
            shown += f' … (+{len(names) - 3})'
        details = [self._kind(names[0] if names else '')]
        size = self._size(job)
        if size:
            details.append(size)
        host = urlsplit(job.url).hostname
        if host:
            details.append(host)
        count = translate('Files sent: {count}').format(count=len(names))
        lines = [
            f'✅ #{job.id} · {translate("Completed")} — {count}',
            '',
            f'📄 {escape_markdown(shown or translate("Recording"))}',
            escape_markdown(' · '.join(details)),
        ]
        link = self._link(job.url, translate('Open direct link'))
        if link:
            lines.append(link)
        return '\n'.join(lines)

    def cancel(self, job_id, *, chat):
        """Signal only on the polling path; workers handle notifications."""
        with self._dispatch_lock:
            job = self.registry.cancel(job_id, chat=chat)
            if job is not None and job.state is JobState.CANCELLED:
                self._pending = deque(entry for entry in self._pending if entry[0] is not job)
                if not job.finalized and not any(entry[0] is job for entry in self._notices):
                    self._notices.append((job, copy_context()))
            return job

    def control(self, action, chat, job_id=None):
        """Chat-bound response; safe for callback answers and commands."""
        if job_id is None and action == 'status':
            with self.registry.lock:
                jobs = [job for job in self.registry.jobs.values() if job.chat == chat]
                if not jobs:
                    return translate('No queued or active downloads.')
                return '\n'.join(self.header(job) for job in jobs)
        if not str(job_id).isascii() or not str(job_id).isdigit() or len(str(job_id)) > 18:
            return translate('Usage: /{action} <id>').format(action=action)
        job = (self.cancel(int(job_id), chat=chat) if action == 'cancel'
               else self.registry.get(int(job_id), chat=chat))
        if job is None:
            return translate('Download not found in this chat.')
        return clip_text(self.header(job), 190)

    def command(self, token, chat, action, args):
        if len(args) > 1 or (action == 'cancel' and not args):
            text = translate('Usage: /{action} <id>').format(action=action)
        else:
            text = self.control(action, chat, args[0] if args else None)
        self.api(token, 'sendMessage', {'chat_id': chat, 'text': text, **NO_PREVIEW}, tries=0)

    def start(self, token, chat, url, name, split_size, thumb, as_doc, to_self,
              *, runner=None):
        try:
            job = self.registry.add(token=token, chat=chat, url=url, name=name,
                                    split_size=split_size, thumb=thumb,
                                    as_doc=as_doc, to_self=to_self)
        except JobRejected as error:
            message = str(error)
            if message not in _ADMISSION_MESSAGES:
                message = 'Download queue is unavailable.'
            return translate(message), False
        try:
            text = self.text(job)
            message = self.api(token, 'sendMessage', {
                'chat_id': chat, 'text': text, **NO_PREVIEW,
                'disable_notification': True, 'reply_markup': self.markup(job)},
                tries=0)
            job.message_id = message['message_id']
            job.last_text, job.last_edit = text, time.monotonic()
        except Exception as error:
            job.error = error_summary(error)
            self.registry.complete(job, JobState.FAILED)
            self.registry.claim_finalization(job)
            return translate('Could not post download status. Please try again later.'), False
        with self._dispatch_lock:
            if job.state.terminal:
                if self._workers == 0:
                    self._workers += 1
                    try:
                        self.executor().submit(self._drain)
                    except Exception:
                        self._workers -= 1
                return f'#{job.id} · {self.state_label(job)}', False
            self._pending.append((job, copy_context(), runner))
            if self._pending and self._workers < WORKERS:
                self._workers += 1
                try:
                    self.executor().submit(self._drain)
                except Exception:
                    self._workers -= 1
                    self._pending = deque(entry for entry in self._pending if entry[0] is not job)
                    job.error = 'Download queue is unavailable.'
                    self.registry.complete(job, JobState.FAILED)
                    self.finish(job)
                    return translate('Download queue is unavailable.'), False
        return f'⬇️ #{job.id} · {translate("Queued")} · {mask_url(url)}', True

    def _drain(self):
        while True:
            with self._dispatch_lock:
                if self._notices:
                    notice = self._notices.popleft()
                else:
                    notice = None
                if notice is not None:
                    entry = None
                elif self._pending:
                    entry = self._pending.popleft()
                else:
                    self._workers -= 1
                    return
            if notice is not None:
                job, context = notice
                context.run(self.finish, job)
                continue
            job, context, runner = entry
            if not self.registry.begin(job):
                context.run(self.finish, job)
                continue
            try:
                context.run(runner or self.run, job, job.url, job.chat, job.name,
                            job.split_size, job.thumb, job.as_doc, job.to_self)
            except (Exception, SystemExit) as error:
                job.error = error_summary(error)
                self.registry.complete(job, JobState.FAILED)
                context.run(self.finish, job)

    @staticmethod
    def adapt(job, *, url='', chat=0):
        """Narrow compatibility for callers predating typed lifecycle jobs."""
        if isinstance(job, Job):
            return job
        if '_typed_job' in job:
            return job['_typed_job']
        fields = ('token', 'chat', 'url', 'lines', 'files', 'error', 'note', 'message_id')
        values = {key: job[key] for key in fields if key in job}
        values.setdefault('token', '')
        values.setdefault('chat', chat)
        values.setdefault('url', url)
        typed = Job(id=job['id'], **values)
        job['_typed_job'] = typed
        return typed

    def run(self, job, url, chat, name, split_size, thumb, as_doc, to_self,
            *, finish=None):
        """Run the transfer boundary on a worker with scoped controls."""
        job = self.adapt(job, url=url, chat=chat)
        finalizer = finish or self.finish
        if job.state.terminal:
            finalizer(job)
            return
        if job.state is JobState.QUEUED:
            self.registry.begin(job)
        target = None
        cancelled_type = ()

        def note(text):
            with job.ui_lock:
                job.lines.append(redact(text)[:1000])
                del job.lines[:-10]
            self.status(job)

        def stage(value):
            self.registry.stage(job, value)
            self.status(job)

        try:
            from leech import cli as leech
            from leech.control import TransferCancelled, transfer_control, check_cancelled
            from leech.downloader import untrusted_downloads
            cancelled_type = TransferCancelled
            root = download_root(self.root())
            root.mkdir(parents=True, exist_ok=True)
            target = Path(tempfile.mkdtemp(prefix=f'dl{job.id}-', dir=root))
            with transfer_control(cancel_event=job.cancel_event, on_stage=stage), untrusted_downloads():
                check_cancelled()
                files = leech.run_leech_sync(
                    url, chat=chat, name=name, split_size=split_size, thumb=thumb,
                    as_doc=as_doc, to_self=to_self, progress=note, target_dir=str(target))
            job.files = [str(path) for path in files]
            self.registry.complete(job, JobState.SUCCEEDED)
        except (ImportError, SystemExit) as error:
            job.note = f'leech unavailable: {error_summary(error)}'
            self.registry.complete(job, JobState.FAILED)
        except Exception as error:
            job.error = error_summary(error)
            job.sent = getattr(error, 'sent', 0)
            job.total = getattr(error, 'total', 0)
            job.retained = [str(path) for path in getattr(error, 'retained', [])]
            cause, was_cancelled = error, False
            seen = set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                was_cancelled = was_cancelled or isinstance(cause, cancelled_type)
                cause = cause.__cause__ or cause.__context__
            state = JobState.CANCELLED if was_cancelled else JobState.FAILED
            self.registry.complete(job, state)
        finally:
            if target is not None:
                try:
                    target.rmdir()  # Never remove unconfirmed/retained media.
                except OSError:
                    pass
            finalizer(job)

    def receipt_lines(self, job):
        """Describe confirmed delivery without exposing internal errors or paths."""
        icon = {JobState.SUCCEEDED: '✅', JobState.CANCELLED: '🚫', JobState.FAILED: '❌'}[job.state]
        lines = [f'{icon} #{job.id} · {self.state_label(job)}']
        if job.state is JobState.FAILED:
            if job.note.startswith('leech unavailable:'):
                message = 'Downloader unavailable. Please try again later.'
            elif job.error == 'Download queue is unavailable.':
                message = 'Download queue is unavailable.'
            else:
                message = 'Download failed. Please try again later.'
            lines.append(translate(message))
        elif job.state is JobState.SUCCEEDED:
            lines.append(translate('Files sent: {count}').format(count=len(job.files)))
            names = ', '.join(Path(path).name for path in job.files)
            if names:
                lines.append(f'📄 {names}')
        if job.total:
            lines.append(translate('Confirmed sent: {sent}/{total}').format(
                sent=job.sent, total=job.total))
        if job.retained:
            names = ', '.join(Path(path).name for path in job.retained)
            lines.append(translate('Files kept on server: {files}').format(files=names))
        return [redact(line) for line in lines]

    def finish(self, job):
        """Release exactly once before best-effort final notifications."""
        legacy = job if isinstance(job, dict) else None
        job = self.adapt(job)
        if not job.state.terminal:
            state = JobState.FAILED if job.error or job.note else JobState.SUCCEEDED
            self.registry.complete(job, state)
        if legacy is not None:
            with legacy['lock']:
                legacy['pool'].pop(job.id, None)
        if not self.registry.claim_finalization(job):
            return
        try:
            if self.card is not None and job.state is JobState.SUCCEEDED:
                try:
                    self.card(job.token, job.chat, self.card_text(job))
                except Exception as error:
                    # The transfer succeeded; never lose the receipt over formatting.
                    print(f'download #{job.id}: card {error_summary(error)}', flush=True)
                    self.notify(job.token, job.chat, self.receipt_lines(job))
            else:
                self.notify(job.token, job.chat, self.receipt_lines(job))
        except Exception as error:
            print(f'download #{job.id}: notification {error_summary(error)}', flush=True)
        if job.message_id is not None:
            try:
                self.api(job.token, 'deleteMessage', {
                    'chat_id': job.chat, 'message_id': job.message_id}, tries=0)
            except Exception:
                pass


def prune_downloads(root):
    """Remove empty run directories only; never delete retained media."""
    root = download_root(root)
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir():
            try:
                child.rmdir()
            except OSError:
                pass
    try:
        root.rmdir()
    except OSError:
        pass
