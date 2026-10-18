"""Configuration loading (TOML or JSON) with environment overrides."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib as _toml  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - older Pythons
    try:
        import tomli as _toml  # type: ignore[import-not-found,no-redef]
    except ModuleNotFoundError:
        _toml = None


class ConfigError(Exception):
    pass


@dataclass
class Config:
    #: Where gate state (posts, attempts, halt flag) lives.
    state_path: str = ".postgate/state.json"
    #: Where the queue lives. Defaults to ``queue.json`` next to the state file.
    queue_path: str | None = None
    #: Minimum seconds between two posts (counted from the last post/attempt).
    min_gap_seconds: float = 90
    #: ``before`` blocks text near-identical to anything posted in this window.
    dup_window_hours: float = 24
    #: Default window for ``dupcheck``.
    dupcheck_hours: float = 6
    #: Default window for ``lookup``.
    lookup_minutes: float = 120
    #: difflib ratio at or above which two texts count as the same post.
    similarity: float = 0.9
    #: Equal normalised prefixes of this length also count as the same post.
    prefix_chars: int = 80
    #: Retries allowed per text, and only after absence has been confirmed.
    max_retries: int = 1
    #: Drop recorded posts older than this many days.
    retention_days: float = 7
    #: Set the persistent halt flag on verification failures / duplicates.
    halt_on_failure: bool = True
    #: Expected author/account name; checked by ``after`` when the verifier
    #: reports one. Empty = don't check.
    author: str = ""
    #: Verifier: "local", "trust", or "package.module:ClassName".
    verifier: str = "trust"
    #: Keyword arguments passed to the verifier constructor.
    verifier_options: dict = field(default_factory=dict)
    #: Post ids that ``dupcheck`` should ignore (known, accepted duplicates).
    ignore_ids: list = field(default_factory=list)

    def resolved_queue_path(self) -> str:
        if self.queue_path:
            return self.queue_path
        return str(Path(self.state_path).with_name("queue.json"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        known = {f.name for f in fields(cls)}
        # Allow a [postgate] table or top-level keys.
        if "postgate" in data and isinstance(data["postgate"], dict):
            merged = dict(data["postgate"])
            for k, v in data.items():
                if k != "postgate":
                    merged.setdefault(k, v)
            data = merged
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"unknown config keys: {', '.join(sorted(unknown))}")
        return cls(**data)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config from ``path`` / $POSTGATE_CONFIG / ./postgate.toml / ./postgate.json.

    Environment overrides: POSTGATE_STATE (state_path), POSTGATE_QUEUE (queue_path).
    """
    path = path or os.environ.get("POSTGATE_CONFIG")
    if not path:
        for cand in ("postgate.toml", "postgate.json"):
            if os.path.exists(cand):
                path = cand
                break
    data: dict[str, Any] = {}
    if path:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        if p.suffix.lower() == ".json":
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except ValueError as e:
                raise ConfigError(f"bad JSON in {p}: {e}") from e
        else:
            if _toml is None:
                raise ConfigError("TOML config needs Python 3.11+ (or `pip install tomli`); use JSON instead")
            try:
                data = _toml.loads(p.read_text(encoding="utf-8"))
            except Exception as e:  # tomllib.TOMLDecodeError
                raise ConfigError(f"bad TOML in {p}: {e}") from e
    cfg = Config.from_dict(data)
    if os.environ.get("POSTGATE_STATE"):
        cfg.state_path = os.environ["POSTGATE_STATE"]
    if os.environ.get("POSTGATE_QUEUE"):
        cfg.queue_path = os.environ["POSTGATE_QUEUE"]
    return cfg
