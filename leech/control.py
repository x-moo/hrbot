"""Cooperative transfer cancellation, scoped to the current worker/task."""
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio

_CANCEL_EVENTS = ContextVar('transfer_cancel_events', default=())
_ON_STAGE = ContextVar('transfer_on_stage', default=None)
_PENDING_OPERATIONS = ContextVar('transfer_pending_operations', default=None)


class TransferError(RuntimeError):
    """Failed transfer: sent counts acknowledgements; retained lists local paths."""
    def __init__(self, message, *, sent=0, total=0, retained=()):
        super().__init__(message)
        self.sent = sent
        self.total = total
        self.retained = list(retained)


class TransferCancelled(TransferError):
    """The caller requested cancellation; unconfirmed files remain on disk."""


@contextmanager
def transfer_control(cancel_event=None, on_stage=None):
    """Scope a worker's controls; nested scopes cannot mask cancellation.

    cancel_event implements is_set() (e.g. threading.Event); on_stage receives
    downloading/splitting/waiting_upload/uploading. Omitted hooks inherit.
    Context propagates into asyncio tasks/to_thread, not raw new threads.
    """
    events = _CANCEL_EVENTS.get()
    token = _CANCEL_EVENTS.set(events + (cancel_event,) if cancel_event is not None else events)
    stage_token = _ON_STAGE.set(on_stage if on_stage is not None else _ON_STAGE.get())
    try:
        yield
    finally:
        _ON_STAGE.reset(stage_token)
        _CANCEL_EVENTS.reset(token)


def check_cancelled():
    if any(event.is_set() for event in _CANCEL_EVENTS.get()):
        raise TransferCancelled('Transfer cancelled; unconfirmed files retained')


def report_stage(state):
    """Publish one machine-readable stage to the active progress hook."""
    callback = _ON_STAGE.get()
    if callback is not None:
        callback(state.lower())


@contextmanager
def track_pending_operations():
    """Give the upload slot ownership of operations slow to cancel."""
    pending = set()
    token = _PENDING_OPERATIONS.set(pending)
    try:
        yield pending
    finally:
        _PENDING_OPERATIONS.reset(token)


async def await_controlled(awaitable, timeout):
    """Wait once, polling worker cancellation without resending the operation."""
    task = asyncio.ensure_future(awaitable)
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while not task.done():
            check_cancelled()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.wait({task}, timeout=min(0.1, remaining))
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=1)
            pending = _PENDING_OPERATIONS.get()
            if not task.done() and pending is not None:
                pending.add(task)
        # Retrieve failures even when cancellation won the race.
        def consume(future):
            if not future.cancelled():
                future.exception()
        task.add_done_callback(consume)
