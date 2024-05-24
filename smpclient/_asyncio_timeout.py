"""This module provides a backport of the `asyncio.timeout` context manager for
Python 3.10 and below.
"""

import asyncio
import contextlib
from typing import AsyncGenerator


@contextlib.asynccontextmanager
async def timeout(seconds: float) -> AsyncGenerator[None, None]:
    async def cancel(task: asyncio.Task[None], seconds: float) -> None:
        await asyncio.sleep(seconds)
        if not task.done():
            task.cancel()

    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("Must be used inside a running task")

    cancel_task = asyncio.create_task(cancel(task, seconds))
    try:
        yield
    except asyncio.CancelledError:
        raise asyncio.TimeoutError
    finally:
        cancel_task.cancel()
