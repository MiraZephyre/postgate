"""Pluggable verifiers: how postgate checks a post really exists.

A verifier answers two questions about the platform:

* ``fetch(post_id)``  -> the post as the platform sees it, or ``None`` if it
  does not exist.
* ``recent(since_ts)`` -> posts by the account since a unix timestamp, or
  ``None`` if the verifier can't list posts (then postgate falls back to its
  own records and tells you so).

Raise :class:`VerifierUnavailable` for network/API errors. postgate treats
that as "unknown" and halts rather than guessing - a blind retry after an
unknown result is exactly how double posts happen.

Write your own by subclassing :class:`Verifier` and pointing the config at it:
``verifier = "mybot.verify:MyVerifier"``.
"""
from __future__ import annotations

import importlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class VerifierUnavailable(Exception):
    """The platform could not be reached / gave an unusable answer."""


@dataclass
class PostInfo:
    id: str
    text: str | None = None          # None = verifier can't see text
    url: str | None = None
    parent: str | None = None        # id of the post this replies to
    author: str | None = None
    created_ts: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Verifier:
    """Base class. Override ``fetch`` and (ideally) ``recent``."""

    name = "base"
    #: Whether ``after`` should compare the reply parent the verifier reports.
    checks_parent = True

    def parse_ref(self, ref: str) -> str:
        """Turn whatever the poster returned (URL or id) into a post id."""
        ref = (ref or "").strip()
        if "://" in ref:
            ref = ref.split("?", 1)[0].split("#", 1)[0].rstrip("/")
            ref = ref.rsplit("/", 1)[-1]
        return ref

    def fetch(self, post_id: str) -> PostInfo | None:  # pragma: no cover - abstract
        raise NotImplementedError

    def recent(self, since_ts: float) -> list[PostInfo] | None:
        return None


class TrustVerifier(Verifier):
    """No network, no checks: accepts any non-empty post id as existing.

    Useful to get rate-gap, duplicate, queue and halt protection on day one,
    before you've written a real verifier. Text and copy counts are NOT
    verified in this mode (postgate says so in its output).
    """

    name = "trust"
    checks_parent = False

    def fetch(self, post_id: str) -> PostInfo | None:
        return PostInfo(id=post_id) if post_id else None


class LocalVerifier(Verifier):
    """No network: verifies against a local JSON ledger file.

    The ledger looks like ``{"posts": [{"id": ..., "text": ..., "parent": ...,
    "author": ..., "created_ts": ...}]}``. Use it for dry runs, tests, or a
    bot whose "platform" is local (a file, a log, a mock server). Call
    :meth:`publish` to simulate posting.
    """

    name = "local"

    def __init__(self, ledger_path: str = ".postgate/ledger.json", author: str | None = None):
        self.ledger_path = Path(ledger_path)
        self.author = author

    def _load(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.ledger_path.read_text(encoding="utf-8")).get("posts", [])
        except FileNotFoundError:
            return []
        except ValueError as e:
            raise VerifierUnavailable(f"ledger {self.ledger_path} unreadable: {e}") from e

    @staticmethod
    def _info(p: dict[str, Any]) -> PostInfo:
        return PostInfo(id=str(p["id"]), text=p.get("text"), url=p.get("url"),
                        parent=p.get("parent"), author=p.get("author"),
                        created_ts=p.get("created_ts"))

    def fetch(self, post_id: str) -> PostInfo | None:
        for p in self._load():
            if str(p.get("id")) == str(post_id):
                return self._info(p)
        return None

    def recent(self, since_ts: float) -> list[PostInfo] | None:
        return [self._info(p) for p in self._load() if (p.get("created_ts") or 0) >= since_ts]

    def publish(self, text: str, parent: str | None = None, created_ts: float | None = None) -> str:
        """Append a post to the ledger (simulates a platform) and return its id."""
        posts = self._load()
        pid = uuid.uuid4().hex[:12]
        posts.append({"id": pid, "text": text, "parent": parent, "author": self.author,
                      "created_ts": time.time() if created_ts is None else created_ts})
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ledger_path.with_name(self.ledger_path.name + ".tmp")
        tmp.write_text(json.dumps({"posts": posts}, indent=1), encoding="utf-8")
        os.replace(tmp, self.ledger_path)
        return pid


BUILTIN = {"trust": TrustVerifier, "local": LocalVerifier}


def load_verifier(spec: str | Verifier | None, options: dict[str, Any] | None = None) -> Verifier:
    """Build a verifier from a name (``trust``/``local``) or ``module:Class``."""
    if isinstance(spec, Verifier):
        return spec
    spec = spec or "trust"
    options = dict(options or {})
    if spec in BUILTIN:
        return BUILTIN[spec](**options)
    if ":" not in spec:
        raise ValueError(f"unknown verifier {spec!r}; use trust, local or module:ClassName")
    mod_name, cls_name = spec.split(":", 1)
    cls = getattr(importlib.import_module(mod_name), cls_name)
    v = cls(**options)
    if not isinstance(v, Verifier):
        raise TypeError(f"{spec} is not a postgate.Verifier subclass")
    return v
