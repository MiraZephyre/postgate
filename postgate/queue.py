"""A tiny posting queue: load a plan, take the next item, mark it done.

Plan formats
------------
Text (one post per line)::

    # comments and blank lines are ignored
    A standalone post.
    parent: https://example.com/posts/123
    A reply to post 123 (``parent:`` applies to the next line only).
    thread: First part of a thread.
    thread: Second part - automatically replies to the first.
    Use \\n inside a line for a line break.

JSON: ``[{"text": "...", "parent": "...", "thread": false}, ...]`` or
``{"items": [...]}``.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any

from .gate import Code, Gate, Result

THREAD = "@thread"


def parse_plan(path: str | os.PathLike) -> list[dict[str, Any]]:
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        data = json.loads(raw)
        items = data["items"] if isinstance(data, dict) else data
        out = []
        for it in items:
            if not str(it.get("text", "")).strip():
                raise ValueError("plan item without text")
            out.append({"text": it["text"], "parent": it.get("parent"), "thread": bool(it.get("thread"))})
        return _chain_threads(out)
    out, parent = [], None
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        low = s.lower()
        if low.startswith("parent:"):
            parent = s[7:].strip() or None
            continue
        thread = low.startswith("thread:")
        text = (s[7:].strip() if thread else s).replace("\\n", "\n")
        out.append({"text": text, "parent": parent, "thread": thread})
        parent = None
    return _chain_threads(out)


def _chain_threads(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every thread item after the first replies to the previous thread item."""
    first = True
    for it in items:
        if it["thread"]:
            if not first and not it.get("parent"):
                it["parent"] = THREAD
            first = False
    return items


class Queue:
    def __init__(self, gate: Gate, path: str | None = None):
        self.gate = gate
        self.path = Path(path or gate.config.resolved_queue_path())

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"i": 0, "items": [], "last_thread_id": None, "log": []}

    def _save(self, q: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(q, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def load(self, plan: str | os.PathLike, force: bool = False) -> Result:
        q = self._load()
        if not force and q["i"] < len(q["items"]):
            return Result(Code.USAGE, f"STOP: queue still has {len(q['items']) - q['i']} item(s); "
                          "finish it or use --force to replace it.")
        try:
            items = parse_plan(plan)
        except (OSError, ValueError, KeyError) as e:
            return Result(Code.USAGE, f"STOP: can't read plan: {e}")
        self._save({"i": 0, "items": items, "last_thread_id": None, "log": []})
        return Result(Code.OK, f"loaded {len(items)} item(s)", {"count": len(items)})

    def _current(self, q: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, Result | None]:
        if q["i"] >= len(q["items"]):
            return None, None, Result(Code.QUEUE_EMPTY, "QUEUE_DONE: nothing left to post.")
        it = q["items"][q["i"]]
        parent = it.get("parent")
        if parent == THREAD:
            parent = q.get("last_thread_id")
            if not parent:
                return None, None, Result(Code.USAGE, "STOP: thread item has no previous thread post recorded.")
        return it, parent, None

    def next(self, peek: bool = False) -> Result:
        """Run ``before`` for the next item. ``peek`` checks without recording an attempt."""
        q = self._load()
        it, parent, err = self._current(q)
        if err:
            return err
        r = self.gate.before(it["text"], parent, record=not peek)
        not_before = _dt.datetime.fromtimestamp(self.gate.now() + r.wait_seconds).isoformat(timespec="seconds")
        r.data.update(idx=q["i"] + 1, of=len(q["items"]), text=it["text"], parent=parent, not_before=not_before)
        return r

    def done(self, ref: str) -> Result:
        """Run ``after`` for the current item and advance on success."""
        q = self._load()
        it, parent, err = self._current(q)
        if err:
            return err
        r = self.gate.after(ref, it["text"], parent)
        if r.code == Code.OK:
            pid = self.gate.verifier.parse_ref(ref)
            if it.get("thread"):
                q["last_thread_id"] = pid
            q["log"].append({"idx": q["i"] + 1, "post_id": pid, "status": "posted"})
            q["i"] += 1
            self._save(q)
            r.message += f"\nadvanced to {q['i']}/{len(q['items'])}"
        return r

    def skip(self, reason: str = "skipped") -> Result:
        q = self._load()
        it, _, err = self._current(q)
        if err:
            return err
        if it.get("thread"):
            return Result(Code.USAGE, "STOP: refusing to skip a thread item (later parts would reply to the wrong post).")
        q["log"].append({"idx": q["i"] + 1, "status": "skipped", "reason": reason})
        q["i"] += 1
        self._save(q)
        return Result(Code.OK, f"skipped item {q['i']}: {reason}")

    def status(self) -> Result:
        q = self._load()
        left = len(q["items"]) - q["i"]
        return Result(Code.OK, f"{q['i']}/{len(q['items'])} done, {left} left",
                      {"done": q["i"], "total": len(q["items"]), "left": left})
