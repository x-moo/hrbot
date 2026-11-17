"""Serialize MTProto lifecycles across worker threads and event loops."""
import asyncio
from contextlib import asynccontextmanager
import threading

_UPLOAD_LOCK = threading.Lock()


@asynccontextmanager
async def upload_slot():
    # Non-blocking acquisition keeps worker event loops responsive and avoids
    # abandoned executor acquisitions when a waiting job is cancelled.
    while not _UPLOAD_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.1)
    try:
        yield
    finally:
        _UPLOAD_LOCK.release()
