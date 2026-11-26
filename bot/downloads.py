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
from leech.utils import redact


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


AUDIO_SUFFIXES = {'mp3', 'wma', 'm4a', 'ogg', 'oga', 'aac', 'flac', 'wav', 'ra', 'ram'}
VIDEO_SUFFIXES = {'wmv', 'asf', 'rm', 'mp4', '3gp', 'mkv', 'avi', 'swf'}
_SIZE_RE = re.compile(r'^downloaded (\d[\d.]* [KMGT]?B) in ')


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
            {'text': 'Cancel', 'callback_data': f'cancel:{job.id}'},
            {'text': 'Status', 'callback_data': f'status:{job.id}'}]]}

    def text(self, job):
        state = job.state.value
        if job.cancel_event.is_set() and not job.state.terminal:
            state += ' · cancellation requested'
        body = '\n'.join(job.lines[-10:])
        return (f'⬇️ #{job.id} · {state} · {mask_url(job.url)}\n{body}'
                .encode('utf-16-le')[:7800].decode('utf-16-le', errors='ignore'))

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
        suffix = Path(name).suffix.lstrip('.').lower()
        if suffix in AUDIO_SUFFIXES:
            return '🎙️ audio'
        if suffix in VIDEO_SUFFIXES:
            return '🎬 video'
        return '📦 file'

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
        details = [self._kind(names[0]) if names else '📦 file']
        size = self._size(job)
        if size:
            details.append(size)
        host = urlsplit(job.url).hostname
        if host:
            details.append(host)
        lines = [
            f'✅ #{job.id} · {translate("done").capitalize()} — '
            f'{len(names)} {translate("file(s)")}',
            '',
            f'📄 {escape_markdown(shown or "recording")}',
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
                if not job.finalized and job not in self._notices:
                    self._notices.append(job)
            return job

    def control(self, action, chat, job_id=None):
        """Chat-bound response; safe for callback answers and commands."""
        if job_id is None and action == 'status':
            with self.registry.lock:
                jobs = [job for job in self.registry.jobs.values() if job.chat == chat]
                if not jobs:
                    return 'No queued or active downloads.'
                return '\n'.join(self.text(job).split('\n', 1)[0] for job in jobs)
        if not str(job_id).isascii() or not str(job_id).isdigit() or len(str(job_id)) > 18:
            return f'Usage: /{action} <id>'
        job = (self.cancel(int(job_id), chat=chat) if action == 'cancel'
               else self.registry.get(int(job_id), chat=chat))
        if job is None:
            return 'Download not found in this chat.'
        return self.text(job).split('\n', 1)[0][:190]

    def command(self, token, chat, action, args):
        if len(args) > 1 or (action == 'cancel' and not args):
            text = f'Usage: /{action} <id>'
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
            return str(error), False
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
            return f'cannot post status: {job.error}', False
        with self._dispatch_lock:
            if job.state.terminal:
                if self._workers == 0:
                    self._workers += 1
                    try:
                        self.executor().submit(self._drain)
                    except Exception:
                        self._workers -= 1
                return f'#{job.id} {job.state.value}', False
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
                    return job.error, False
        return f'⬇️ #{job.id} queued · {mask_url(url)}', True

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
                self.finish(notice)
                continue
            job, context, runner = entry
            if not self.registry.begin(job):
                self.finish(job)
                continue
            try:
                context.run(runner or self.run, job, job.url, job.chat, job.name,
                            job.split_size, job.thumb, job.as_doc, job.to_self)
            except (Exception, SystemExit) as error:
                job.error = error_summary(error)
                self.registry.complete(job, JobState.FAILED)
                self.finish(job)

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
        if job.state is JobState.CANCELLED:
            tail = 'Cancelled; unconfirmed files are retained.'
        elif job.state is JobState.FAILED:
            tail = f'Failed: {job.note or job.error}'
        else:
            names = ', '.join(Path(path).name for path in job.files)
            tail = f'Done — {len(job.files)} file(s)' + (f': {names}' if names else '')
        if job.total:
            tail += f' · uploaded {job.sent}/{job.total}'
        if job.retained:
            tail += ' · retained: ' + ', '.join(Path(path).name for path in job.retained)
        try:
            if self.card is not None and job.state is JobState.SUCCEEDED:
                try:
                    self.card(job.token, job.chat, self.card_text(job))
                except Exception as error:
                    # The transfer succeeded; never lose the receipt over formatting.
                    print(f'download #{job.id}: card {error_summary(error)}', flush=True)
                    self.notify(job.token, job.chat, [redact(line) for line in
                                [f'#{job.id} {job.state.value}', *job.lines, '', tail]])
            else:
                self.notify(job.token, job.chat, [redact(line) for line in
                            [f'#{job.id} {job.state.value}', *job.lines, '', tail]])
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
