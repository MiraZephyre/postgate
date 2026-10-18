"""Locked JSON state file. Safe for several processes on one machine."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


def _empty() -> dict[str, Any]:
    return {"version": 1, "posts": [], "attempts": [], "halt": None}


class StateFile:
    """Context manager: exclusive lock, load, mutate ``.data``, ``save()``.

    >>> with StateFile("state.json") as s:
    ...     s.data["posts"].append({...})
    ...     s.save()
    """

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.data: dict[str, Any] = _empty()
        self._lock = None

    def __enter__(self) -> "StateFile":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = open(str(self.path) + ".lock", "a+")
        if fcntl is not None:
            fcntl.flock(self._lock, fcntl.LOCK_EX)
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.data = _empty()
        except ValueError as e:
            # Never silently discard history (that would re-enable duplicates).
            raise RuntimeError(f"state file {self.path} is corrupt: {e}") from e
        for k, v in _empty().items():
            self.data.setdefault(k, v)
        return self

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def __exit__(self, *exc: Any) -> None:
        if self._lock is not None:
            if fcntl is not None:
                fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None
