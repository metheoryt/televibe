"""One marker file per in-flight turn (section 11)."""

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("televibe")


class Markers:
    def __init__(self, state_dir: Path) -> None:
        self.dir = state_dir / "turns"

    def open(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, turn_id: str, data: dict[str, Any]) -> None:
        path = self.dir / f"{turn_id}.json"
        tmp = self.dir / f".{turn_id}.json.tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def remove(self, turn_id: str) -> None:
        (self.dir / f"{turn_id}.json").unlink(missing_ok=True)

    def all(self) -> list[dict[str, Any]]:
        found = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                log.warning("televibe: skipping unreadable marker %s", path)
                continue
            if isinstance(data, dict):
                found.append(data)
        return found
