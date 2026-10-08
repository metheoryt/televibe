"""Run headless coding agents one turn at a time, as resumable sessions with a typed event stream."""

import sys

if sys.platform == "win32":
    raise ImportError(
        "televibe supports Linux and macOS only: it kills an agent by its process group, "
        "which Windows does not have"
    )

from televibe.errors import TelevibeError

__all__ = ["TelevibeError"]
