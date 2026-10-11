"""Request-local delivery of *public* model output, never private reasoning.

The API installs a callback inside its worker. Observability must not turn a
successful scientific call into a failed one, so a disconnected observer is
isolated from the provider call. ContextVars prevent concurrent jobs from
accidentally publishing into another project's timeline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

ProgressCallback = Callable[[dict[str, Any]], None]
_callback: ContextVar[ProgressCallback | None] = ContextVar("model_progress_callback", default=None)
_logger = logging.getLogger(__name__)


@contextmanager
def model_progress(callback: ProgressCallback | None) -> Iterator[None]:
    """Install a callback for this job/thread; always restore its predecessor."""
    token = _callback.set(callback)
    try:
        yield
    finally:
        _callback.reset(token)


capture_model_progress = model_progress


def current_progress_callback() -> ProgressCallback | None:
    return _callback.get()


def emit_model_progress(event: dict[str, Any]) -> None:
    callback = current_progress_callback()
    if callback is not None:
        try:
            callback(dict(event))
        except Exception:
            # Exception text may contain request data; do not log it.
            _logger.warning("Model progress observer failed; model call continues.")
