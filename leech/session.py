"""Serialize MTProto lifecycles across worker threads and event loops."""
import asyncio
from contextlib import asynccontextmanager
import threading
from .control import check_cancelled, report_stage, track_pending_operations

_UPLOAD_LOCK = threading.Lock()


@asynccontextmanager
async def upload_slot():
    # Non-blocking acquisition keeps worker event loops responsive and avoids
    # abandoned executor acquisitions when a waiting job is cancelled.
    check_cancelled()
    report_stage('waiting_upload')
    while True:
        check_cancelled()
        if _UPLOAD_LOCK.acquire(blocking=False):
            break
        await asyncio.sleep(0.1)
    with track_pending_operations() as pending:
        try:
            check_cancelled()
            yield
        finally:
            # Client stop/disconnect runs inside the caller's yield. A library
            # coroutine may still suppress cancellation after teardown: fail
            # closed until it exits rather than overlap another transmission.
            live = {task for task in pending if not task.done()}
            if not live:
                _UPLOAD_LOCK.release()
            else:
                lock = _UPLOAD_LOCK

                def release_when_idle(task):
                    live.discard(task)
                    if not live:
                        lock.release()

                for task in live:
                    task.add_done_callback(release_when_idle)
