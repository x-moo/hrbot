"""Thread-safe admission and lifecycle for bounded background transfers."""
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from itertools import count
from threading import Event, RLock
from typing import Any
import os


class JobState(str, Enum):
    QUEUED = 'queued'
    DOWNLOADING = 'downloading'
    WAITING_UPLOAD = 'waiting_upload'
    UPLOADING = 'uploading'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    CANCELLED = 'cancelled'

    @property
    def terminal(self):
        return self in (self.SUCCEEDED, self.FAILED, self.CANCELLED)


@dataclass
class Job:
    id: int
    token: str = field(repr=False)
    chat: int
    url: str = field(repr=False)
    state: JobState = JobState.QUEUED
    cancel_event: Event = field(default_factory=Event)
    lines: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    error: str = ''
    note: str = ''
    message_id: int | None = None
    stage: str = 'queued'
    sent: int = 0
    total: int = 0
    retained: list[str] = field(default_factory=list)
    name: str | None = None
    split_size: str | None = None
    thumb: str | None = None
    as_doc: bool = False
    to_self: bool = False
    future: Any = field(default=None, repr=False)
    last_edit: float = float('-inf')
    last_text: str | None = None
    finalized: bool = False
    status_disabled: bool = False
    ui_lock: Any = field(default_factory=RLock, repr=False)


class JobRejected(ValueError):
    """Admission failed without allocating a slot or starting work."""


class JobRegistry:
    """Only queued and active jobs occupy admission slots."""

    def __init__(self, *, capacity=None, jobs=None, lock=None):
        self._capacity = capacity
        self.jobs = jobs if jobs is not None else {}
        self.lock = lock if lock is not None else RLock()
        self.history = OrderedDict()
        self._ids = count(1)

    @property
    def capacity(self):
        try:
            configured = int(self._capacity if self._capacity is not None else
                             os.environ.get('LEECH_QUEUE_SIZE', '10'))
        except (ValueError, TypeError):
            configured = 10
        return max(1, configured)

    def add(self, *, token, chat, url, **options):
        with self.lock:
            if any(job.chat == chat and job.url == url for job in self.jobs.values()):
                raise JobRejected('This recording is already queued.')
            if len(self.jobs) >= self.capacity:
                raise JobRejected('Download queue is full. Try again later.')
            if sum(job.chat == chat for job in self.jobs.values()) >= 2:
                raise JobRejected('This chat already has 2 queued or active downloads.')
            job = Job(next(self._ids), token, chat, url, **options)
            self.jobs[job.id] = job
            return job

    def begin(self, job):
        with self.lock:
            if job.state is not JobState.QUEUED:
                return False
            job.state = JobState.DOWNLOADING
            job.stage = job.state.value
            return True

    def counts(self):
        with self.lock:
            return dict(Counter(job.state.value for job in self.jobs.values()))

    def get(self, job_id, *, chat):
        with self.lock:
            job = self.jobs.get(job_id) or self.history.get(job_id)
            return job if job is not None and job.chat == chat else None

    def stage(self, job, stage):
        state = JobState.DOWNLOADING if stage == 'splitting' else JobState(stage)
        if state not in (JobState.DOWNLOADING, JobState.WAITING_UPLOAD, JobState.UPLOADING):
            raise ValueError('not a transfer stage')
        with self.lock:
            if not job.state.terminal:
                job.state, job.stage = state, stage

    def complete(self, job, state):
        if not state.terminal:
            raise ValueError('completion requires a terminal state')
        with self.lock:
            if job.state.terminal:
                return False
            job.state, job.stage = state, state.value
            self.jobs.pop(job.id, None)
            self.history[job.id] = job
            while len(self.history) > 100:
                self.history.popitem(last=False)
            return True

    def cancel(self, job_id, *, chat):
        with self.lock:
            job = self.get(job_id, chat=chat)
            if job is None or job.state.terminal:
                return job
            job.cancel_event.set()
            if job.state is JobState.QUEUED:
                self.complete(job, JobState.CANCELLED)
            return job

    def claim_finalization(self, job):
        with self.lock:
            if not job.state.terminal or job.finalized:
                return False
            job.finalized = True
            return True
