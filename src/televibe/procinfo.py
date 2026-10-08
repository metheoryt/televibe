"""Process facts televibe needs: a start time to recognize a pid, and a group kill."""

import os
import signal
import subprocess
import sys
from pathlib import Path


def start_time(pid: int) -> str | None:
    """An opaque start time for `pid`, or None if there is no such process.

    Comparing it with a recorded value tells a live agent from a recycled pid (REQ-STATE-4).
    """
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        return stat.rsplit(")", 1)[1].split()[19]  # field 22, starttime
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            env={"PATH": "/bin:/usr/bin", "LC_ALL": "C"},
            check=False,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def kill_group(pgid: int) -> None:
    """SIGKILL the whole process group; silent if it is already gone (REQ-CANCEL-1)."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
