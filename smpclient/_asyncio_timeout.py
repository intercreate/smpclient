"""This module provides a backport of the `asyncio.timeout` context manager for
Python 3.10 and below.
"""

import sys

if sys.version_info < (3, 11):
    import asyncio
    import contextlib

    @contextlib.asynccontextmanager
    async def timeout(seconds):
        async def cancel_task(task, seconds):
            await asyncio.sleep(seconds)
            if not task.done():
                task.cancel()

        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Must be used inside a running task")

        cancel_task = asyncio.create_task(cancel_task(task, seconds))
        try:
            yield
        except asyncio.CancelledError:
            raise asyncio.TimeoutError
        finally:
            cancel_task.cancel()

    asyncio.timeout = timeout
