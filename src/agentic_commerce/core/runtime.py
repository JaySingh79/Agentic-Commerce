"""Shared async runtime: one persistent background event loop for the process.

Every synchronous entrypoint (LangChain tools, UCP facades) submits coroutines
to this single loop via :func:`run_async`, so the pooled ``httpx.AsyncClient`` and
cached OAuth token live on one loop instead of a fresh loop per call.
"""

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

_LOOP = asyncio.new_event_loop()
threading.Thread(target=_LOOP.run_forever, name="ucp-async-loop", daemon=True).start()

_T = TypeVar("_T")


def run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """Runs an async coroutine on the shared background loop and blocks for its result."""
    return asyncio.run_coroutine_threadsafe(coro, _LOOP).result()
