"""The gate: call ``before`` ahead of every post and ``after`` once it's up."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Iterable

from .config import Config
from .state import StateFile
from .text import normalize, similar, text_key
from .verifiers import PostInfo, Verifier, VerifierUnavailable, load_verifier


class Code(IntEnum):
    """Exit codes. 0 = go, 2 = wait and re-run, anything else = stop."""

    OK = 0
    NOT_FOUND = 1          # lookup: no matching post
    WAIT = 2               # before: too soon, sleep WAIT_SECONDS and re-run
    DUPLICATE = 3          # near-identical text already posted / found by dupcheck
    VERIFY_FAILED = 4      # after: post missing or not what was intended
    UNAVAILABLE = 5        # after: verifier could not reach the platform
    HALTED = 6             # halt flag is set; nothing may post until `resume`
    MULTIPLE_COPIES = 7    # after: more than one copy of the post exists
    RETRY_BLOCKED = 8      # before: unresolved earlier attempt / retry limit hit
    QUEUE_EMPTY = 10       # queue next: nothing left
    USAGE = 64             # bad input (empty text, missing file, bad config)


@dataclass
class Result:
    code: Code
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.code == Code.OK

    @property
    def wait_seconds(self) -> int:
        return int(self.data.get("wait_seconds", 0))

    def to_dict(self) -> dict[str, Any]:
        return {"code": int(self.code), "status": self.code.name,
                "message": self.message, **self.data}


def _default_clock() -> float:
    env = os.environ.get("POSTGATE_NOW")  # handy for tests and simulations
    return float(env) if env else time.time()


class Gate:
    def __init__(self, config: Config | None = None, verifier: Verifier | str | None = None,
                 clock: Callable[[], float] | None = None):
        self.config = config or Config()
        self.verifier = load_verifier(verifier if verifier is not None else self.config.verifier,
                                      None if verifier is not None else self.config.verifier_options)
        self.clock = clock or _default_clock

    # ------------------------------------------------------------------ helpers
    def now(self) -> float:
        return self.clock()

    def _state(self) -> StateFile:
        return StateFile(self.config.state_path)

    def _similar(self, a: str | None, b: str | None) -> bool:
        return similar(a, b, self.config.similarity, self.config.prefix_chars)

    def _key(self, text: str) -> str:
        return text_key(text, self.config.prefix_chars)

    def _prune(self, s: StateFile) -> None:
        now = self.now()
        keep_posts = now - self.config.retention_days * 86400
        keep_attempts = now - self.config.dup_window_hours * 3600
        s.data["posts"] = [p for p in s.data["posts"] if p.get("ts", 0) > keep_posts]
        s.data["attempts"] = [a for a in s.data["attempts"]
                              if a.get("first_ts", a.get("ts", 0)) > keep_attempts]

    def _find_attempt(self, s: StateFile, text: str) -> dict[str, Any] | None:
        for a in reversed(s.data["attempts"]):
            if self._similar(a.get("text"), text):
                return a
        return None

    def _set_halt(self, s: StateFile, reason: str, source: str) -> None:
        if self.config.halt_on_failure and not s.data.get("halt"):
            s.data["halt"] = {"reason": reason, "source": source, "ts": self.now()}

    def _halt_note(self) -> str:
        return (" Halt flag SET: no further posts until a human checks and runs `postgate resume`."
                if self.config.halt_on_failure else "")

    def _ref_id(self, ref: str | None) -> str | None:
        return self.verifier.parse_ref(ref) if ref else None

    # ------------------------------------------------------------------- before
    def before(self, text: str, parent: str | None = None, record: bool = True) -> Result:
        """May this text be posted now?

        OK records an *attempt*: the gate now assumes the post may go out, so
        the same text can't be sent again until ``after`` records it or
        ``lookup`` confirms it never arrived.
        """
        cfg = self.config
        if not normalize(text):
            return Result(Code.USAGE, "STOP: empty text. Nothing was checked or recorded.")
        with self._state() as s:
            halt = s.data.get("halt")
            if halt:
                return Result(Code.HALTED, f"HALTED: {halt.get('reason')} "
                              "(clear with `postgate resume` after checking by hand).", {"halt": halt})
            now = self.now()
            for p in s.data["posts"]:
                if p.get("ts", 0) > now - cfg.dup_window_hours * 3600 and self._similar(p.get("text"), text):
                    mins = (now - p["ts"]) / 60
                    return Result(Code.DUPLICATE,
                                  f"STOP: duplicate - near-identical text was posted {mins:.0f} min ago: "
                                  f"{p.get('url') or p['id']}", {"post": p})
            att = self._find_attempt(s, text)
            retry = False
            if att is not None:
                if not att.get("absent_confirmed"):
                    return Result(Code.RETRY_BLOCKED,
                                  "STOP: an earlier attempt at this text is unresolved. It may have gone "
                                  "out. Run `postgate lookup` (and `after` if found). Never retry blind.",
                                  {"attempt": att})
                if att.get("tries", 1) > cfg.max_retries:
                    if record:
                        self._set_halt(s, f"retry limit reached for: {normalize(text)[:60]!r}", "before")
                        s.save()
                    return Result(Code.RETRY_BLOCKED,
                                  f"STOP: retry limit ({cfg.max_retries}) reached for this text."
                                  + (self._halt_note() if record else ""), {"attempt": att})
                retry = True
            pending = [a.get("ts", 0) for a in s.data["attempts"] if not a.get("absent_confirmed")]
            posted = [p.get("ts", 0) for p in s.data["posts"]]
            last = max(pending + posted, default=0)
            wait = cfg.min_gap_seconds - (now - last)
            if wait > 0:
                secs = int(wait) + 1
                return Result(Code.WAIT, f"WAIT: only {now - last:.0f}s since the last post "
                              f"(need {cfg.min_gap_seconds:g}s). WAIT_SECONDS={secs}",
                              {"wait_seconds": secs})
            if record:
                if retry:
                    att.update(ts=now, tries=att.get("tries", 1) + 1, absent_confirmed=False)
                else:
                    s.data["attempts"].append({"key": self._key(text), "text": text,
                                               "parent": self._ref_id(parent), "ts": now,
                                               "first_ts": now, "tries": 1,
                                               "absent_confirmed": False})
                self._prune(s)
                s.save()
        what = "RETRY (absence confirmed)" if retry else "OK"
        return Result(Code.OK, f"{what}: clear to send this post ONCE.", {"retry": retry})

    # -------------------------------------------------------------------- after
    def after(self, ref: str | None, text: str | None = None, parent: str | None = None) -> Result:
        """Verify a post exists exactly once, then record it.

        ``text`` may be omitted (e.g. a temp file holding it vanished): the
        gate then uses the text stored by ``before``. That avoids reporting a
        post that *did* go out as a failure - the classic trigger for a blind
        retry and a double post.
        """
        cfg = self.config
        pid = self._ref_id(ref)
        if not pid:
            return Result(Code.USAGE, "STOP: no post reference given. If the poster reported a failure, "
                          "run `postgate lookup` before doing anything else. Do not retry.")
        notes: list[str] = []
        with self._state() as s:
            for p in s.data["posts"]:
                if str(p["id"]) == pid:
                    return Result(Code.OK, f"OK: {p.get('url') or pid} already recorded.", {"post": p})
            att = None
            if text and normalize(text):
                att = self._find_attempt(s, text)
            else:
                pending = [a for a in s.data["attempts"] if not a.get("absent_confirmed")]
                if len(pending) == 1:
                    att = pending[0]
                    text = att["text"]
                    notes.append("text not supplied/readable; used the text recorded by `before`")
                elif not pending:
                    return Result(Code.USAGE, "STOP: no text given and no pending attempt to take it from.")
                else:
                    return Result(Code.USAGE, f"STOP: no text given and {len(pending)} pending attempts; "
                                  "pass --text so the right one is matched.")
            if parent is None and att is not None:
                parent = att.get("parent")
        assert text is not None

        try:
            info = self.verifier.fetch(pid)
        except VerifierUnavailable as e:
            with self._state() as s:
                self._set_halt(s, f"could not verify post {pid}: {e}", "after")
                s.save()
            return Result(Code.UNAVAILABLE, f"STOP: could not verify {pid} ({e}). Do not retry."
                          + self._halt_note())
        if info is None:
            with self._state() as s:
                self._set_halt(s, f"post {pid} not found on the platform", "after")
                s.save()
            return Result(Code.VERIFY_FAILED, f"STOP: post {pid} not found. Do not retry; run `lookup`."
                          + self._halt_note())

        errs: list[str] = []
        checked_text = info.text is not None
        if checked_text and normalize(info.text) != normalize(text):
            errs.append(f"text mismatch: intended {normalize(text)[:120]!r}, posted {normalize(info.text)[:120]!r}")
        if not checked_text:
            notes.append(f"verifier '{self.verifier.name}' can't see text; content not verified")
        want_parent = self._ref_id(parent)
        if getattr(self.verifier, "checks_parent", True) and checked_text:
            got_parent = self._ref_id(info.parent)
            if want_parent != got_parent:
                errs.append(f"replies to {got_parent!r}, expected {want_parent!r}")
        if cfg.author and info.author is not None and info.author.lower() != cfg.author.lower():
            errs.append(f"author is {info.author!r}, expected {cfg.author!r}")
        if errs:
            with self._state() as s:
                self._set_halt(s, f"verification failed for {pid}: {errs[0]}", "after")
                s.save()
            return Result(Code.VERIFY_FAILED, f"STOP: verification failed for {info.url or pid}:\n- "
                          + "\n- ".join(errs) + "\n" + self._halt_note().strip(), {"errors": errs})

        # Exactly-one-copy check.
        now = self.now()
        copies: dict[str, PostInfo] = {pid: info}
        listed = True
        try:
            recent = self.verifier.recent(now - cfg.lookup_minutes * 60)
        except VerifierUnavailable as e:
            recent, listed = None, False
            notes.append(f"copy count from local records only (listing failed: {e})")
        if recent is None:
            if listed:
                notes.append("copy count from local records only (verifier can't list posts)")
        else:
            for p in recent:
                if p.text is not None and self._similar(p.text, text):
                    copies.setdefault(str(p.id), p)
        with self._state() as s:
            for p in s.data["posts"]:
                if p.get("ts", 0) > now - cfg.dup_window_hours * 3600 and self._similar(p.get("text"), text):
                    copies.setdefault(str(p["id"]), PostInfo(id=str(p["id"]), url=p.get("url"), text=p.get("text")))
            s.data["posts"].append({"id": pid, "url": info.url, "key": self._key(text), "text": text,
                                    "parent": want_parent, "created_ts": info.created_ts, "ts": now,
                                    "verified": checked_text})
            if att is not None:
                s.data["attempts"] = [a for a in s.data["attempts"] if a != att]
            if len(copies) > 1:
                ids = sorted(copies)
                self._set_halt(s, f"{len(copies)} copies of one post: {', '.join(ids)}", "after")
            self._prune(s)
            s.save()
        data = {"post_id": pid, "copies": sorted(copies), "notes": notes, "text_verified": checked_text}
        if len(copies) > 1:
            lst = "\n".join(f"  - {c.url or c.id}" for c in copies.values())
            return Result(Code.MULTIPLE_COPIES, f"STOP: {len(copies)} copies of this post exist:\n{lst}\n"
                          "Recorded. Decide by hand which to keep; never auto-delete." + self._halt_note(), data)
        msg = f"OK: verified one copy of {info.url or pid} and recorded it."
        if notes:
            msg += "\n  note: " + "\n  note: ".join(notes)
        return Result(Code.OK, msg, data)

    # ------------------------------------------------------- lookup / dupcheck
    def _candidates(self, since: float) -> tuple[list[PostInfo], bool, str]:
        items: list[PostInfo] = []
        listed, src = True, "platform+local"
        try:
            got = self.verifier.recent(since)
        except VerifierUnavailable as e:
            got, listed, src = None, False, f"local only (listing failed: {e})"
        if got is None:
            if listed:
                src = "local only (verifier can't list posts)"
            listed = False
        else:
            items.extend(got)
        seen = {str(i.id) for i in items}
        with self._state() as s:
            for p in s.data["posts"]:
                ts = p.get("created_ts") or p.get("ts", 0)
                if str(p["id"]) not in seen and ts >= since:
                    items.append(PostInfo(id=str(p["id"]), text=p.get("text"), url=p.get("url"),
                                          parent=p.get("parent"), created_ts=ts))
        return items, listed, src

    def lookup(self, text: str, minutes: float | None = None, confirm_absent: bool = False) -> Result:
        """Has this text already gone out? Run it whenever a poster reports failure."""
        if not normalize(text):
            return Result(Code.USAGE, "STOP: empty text; nothing looked up.")
        minutes = self.config.lookup_minutes if minutes is None else minutes
        items, listed, src = self._candidates(self.now() - minutes * 60)
        hits = [i for i in items if self._similar(i.text, text)]
        if hits and not confirm_absent:
            lines = "\n".join(f"FOUND {h.url or h.id}" for h in hits)
            return Result(Code.OK, f"source: {src}\n{lines}\nIt went out. Do NOT retry; run `after` with this id.",
                          {"found": [h.to_dict() for h in hits]})
        confirmed = listed or confirm_absent
        with self._state() as s:
            att = self._find_attempt(s, text)
            if att is not None and confirmed:
                att["absent_confirmed"] = True
                s.save()
        if confirm_absent:
            msg = "NOT_FOUND (confirmed by hand). "
        elif listed:
            msg = f"NOT_FOUND (source: {src}). Absence confirmed. "
        else:
            return Result(Code.NOT_FOUND,
                          f"NOT_FOUND in {src} - absence is NOT confirmed. Check the platform by hand; if it "
                          "really isn't there, run `postgate lookup --confirm-absent`.",
                          {"absence_confirmed": False})
        if att is not None:
            left = max(self.config.max_retries - (att.get("tries", 1) - 1), 0)
            msg += f"Retries left for this text: {left}." if left else "No retries left."
        return Result(Code.NOT_FOUND, msg, {"absence_confirmed": True})

    def dupcheck(self, hours: float | None = None, ignore: Iterable[str] = ()) -> Result:
        """Look for near-identical posts in recent history (run at start/end of a round)."""
        hours = self.config.dupcheck_hours if hours is None else hours
        skip = {str(i) for i in list(self.config.ignore_ids) + list(ignore)}
        items, _, src = self._candidates(self.now() - hours * 3600)
        items = [i for i in items if normalize(i.text) and str(i.id) not in skip]
        pairs = [(a, b) for n, a in enumerate(items) for b in items[n + 1:] if self._similar(a.text, b.text)]
        head = f"source: {src}; {len(items)} posts in the last {hours:g}h"
        if not pairs:
            return Result(Code.OK, head + "\nOK: no near-identical posts.", {"count": len(items)})
        lines = [f"DUPLICATE: {a.url or a.id} ~= {b.url or b.id}  {normalize(a.text)[:60]!r}" for a, b in pairs]
        with self._state() as s:
            self._set_halt(s, f"dupcheck found {len(pairs)} near-identical pair(s)", "dupcheck")
            s.save()
        return Result(Code.DUPLICATE, head + "\n" + "\n".join(lines) +
                      "\nSTOP: report these; never delete without a human's confirmation." + self._halt_note(),
                      {"pairs": [[a.id, b.id] for a, b in pairs]})

    # --------------------------------------------------------------------- halt
    def halt(self, reason: str = "halted by operator") -> Result:
        with self._state() as s:
            s.data["halt"] = {"reason": reason, "source": "manual", "ts": self.now()}
            s.save()
        return Result(Code.OK, f"halt flag set: {reason}")

    def resume(self) -> Result:
        with self._state() as s:
            prev = s.data.get("halt")
            s.data["halt"] = None
            s.save()
        return Result(Code.OK, f"halt cleared (was: {prev['reason']})" if prev else "not halted", {"was": prev})

    def status(self) -> Result:
        with self._state() as s:
            d = s.data
            halt = d.get("halt")
            last = max((p.get("ts", 0) for p in d["posts"]), default=None)
            pend = [a for a in d["attempts"] if not a.get("absent_confirmed")]
        wait = 0
        if last:
            wait = max(0, int(self.config.min_gap_seconds - (self.now() - last)) + 1)
        msg = (f"halted: {'YES - ' + halt['reason'] if halt else 'no'}\n"
               f"recorded posts: {len(d['posts'])}\npending attempts: {len(pend)}")
        return Result(Code.HALTED if halt else Code.OK, msg,
                      {"halt": halt, "posts": len(d["posts"]), "pending_attempts": len(pend),
                       "wait_seconds": wait})
