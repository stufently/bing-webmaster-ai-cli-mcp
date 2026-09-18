"""Append-only record of attempted and completed writes."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # pragma: no cover - Windows has no fcntl; O_APPEND still protects each write offset.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class AuditLog:
    def __init__(self, state_dir: Path) -> None:
        directory = Path(state_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path = directory / "audit.jsonl"

    def record(self, event: str, **fields: Any) -> None:
        entry = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
        line = (json.dumps(entry, ensure_ascii=False, default=str) + "\n").encode()
        descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            remaining = memoryview(line)
            while remaining:
                written = os.write(descriptor, remaining)
                if written == 0:
                    raise OSError("audit write returned zero bytes")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def entries(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text().splitlines() if line]
