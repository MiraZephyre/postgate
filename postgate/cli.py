"""Command-line interface. Run ``postgate --help``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .gate import Code, Gate, Result
from .queue import Queue

EPILOG = """exit codes:
  0  OK                 go ahead (before) / verified (after) / found (lookup)
  1  NOT_FOUND          lookup found no matching post
  2  WAIT               too soon: sleep WAIT_SECONDS, then run `before` again
  3  DUPLICATE          near-identical text already posted
  4  VERIFY_FAILED      post missing, wrong text, wrong parent or wrong author
  5  UNAVAILABLE        verifier couldn't reach the platform (unknown != failed)
  6  HALTED             halt flag set; run `postgate resume` after checking
  7  MULTIPLE_COPIES    more than one copy of the post exists
  8  RETRY_BLOCKED      earlier attempt unresolved, or retry limit reached
  10 QUEUE_EMPTY        queue has nothing left
  64 USAGE              bad input (empty text, missing file, bad config)
Anything except 0 and 2 means: stop posting.
"""


def _add_text(p: argparse.ArgumentParser, required: bool = True) -> None:
    g = p.add_mutually_exclusive_group(required=required)
    g.add_argument("--text", help="exact post text")
    g.add_argument("--text-file", help="read the exact post text from this file")


def _read_text(a: argparse.Namespace) -> tuple[str | None, str | None]:
    """Return (text, problem)."""
    if getattr(a, "text", None) is not None:
        return a.text, None
    path = getattr(a, "text_file", None)
    if path is None:
        return None, None
    try:
        return Path(path).read_text(encoding="utf-8"), None
    except OSError as e:
        return None, f"could not read text file {path!r}: {e.strerror or e}"


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on bad usage, which would read as WAIT. Use 64."""

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(int(Code.USAGE), f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="postgate", description="Safety gate for bots that post: "
                                 "rate gaps, duplicate blocking, verify-exactly-once, halt on failure.",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"postgate {__version__}")
    ap.add_argument("--config", help="TOML or JSON config (default: $POSTGATE_CONFIG, ./postgate.toml, ./postgate.json)")
    ap.add_argument("--state", help="state file (overrides config)")
    ap.add_argument("--verifier", help="trust | local | module:ClassName (overrides config)")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    sp = ap.add_subparsers(dest="cmd", required=True)

    b = sp.add_parser("before", help="check a post may go out now; records an attempt on OK")
    _add_text(b)
    b.add_argument("--parent", help="post being replied to (URL or id)")
    b.add_argument("--check", action="store_true", help="check only; don't record an attempt")

    f = sp.add_parser("after", help="verify the post exists exactly once and record it")
    f.add_argument("--ref", "--url", "--id", dest="ref", required=True, help="URL or id the poster returned")
    _add_text(f, required=False)
    f.add_argument("--parent", help="expected parent (default: the one given to `before`)")

    d = sp.add_parser("dupcheck", help="find near-identical posts in recent history")
    d.add_argument("--hours", type=float)
    d.add_argument("--ignore", action="append", default=[], metavar="ID", help="known duplicate to ignore")

    lk = sp.add_parser("lookup", help="has this text already gone out?")
    _add_text(lk)
    lk.add_argument("--minutes", type=float)
    lk.add_argument("--confirm-absent", action="store_true",
                    help="you checked the platform by hand and the post is NOT there (unlocks one retry)")

    h = sp.add_parser("halt", help="set the halt flag (blocks all posts)")
    h.add_argument("--reason", default="halted by operator")
    sp.add_parser("resume", help="clear the halt flag")
    sp.add_parser("status", help="show halt flag and counters (exit 6 if halted)")

    q = sp.add_parser("queue", help="work through a plan of posts")
    qs = q.add_subparsers(dest="qcmd", required=True)
    ql = qs.add_parser("load", help="load a plan (text or JSON)")
    ql.add_argument("plan")
    ql.add_argument("--force", action="store_true", help="replace an unfinished queue")
    qn = qs.add_parser("next", help="run `before` for the next item and print it")
    qn.add_argument("--peek", action="store_true", help="don't record an attempt")
    qd = qs.add_parser("done", help="run `after` for the current item and advance")
    qd.add_argument("ref")
    qk = qs.add_parser("skip", help="skip the current item")
    qk.add_argument("--reason", default="skipped")
    qs.add_parser("status")
    return ap


def _emit(r: Result, as_json: bool) -> int:
    if as_json:
        print(json.dumps(r.to_dict(), default=str, ensure_ascii=False))
    else:
        print(r.message)
        if r.code == Code.WAIT and "WAIT_SECONDS=" not in r.message:
            print(f"WAIT_SECONDS={r.wait_seconds}")
    return int(r.code)


def run(argv: list[str] | None = None) -> Result:
    return _run(build_parser().parse_args(argv))


def _run(a: argparse.Namespace) -> Result:
    try:
        cfg = load_config(a.config)
    except ConfigError as e:
        return Result(Code.USAGE, f"STOP: config error: {e}")
    if a.state:
        cfg.state_path = a.state
    try:
        gate = Gate(cfg, verifier=a.verifier) if a.verifier else Gate(cfg)
    except Exception as e:  # bad verifier spec / import error
        return Result(Code.USAGE, f"STOP: could not load verifier: {e}")

    if a.cmd in ("before", "lookup"):
        text, problem = _read_text(a)
        if problem:
            return Result(Code.USAGE, f"STOP: {problem}. Nothing was checked; this is NOT a post failure.")
        if a.cmd == "before":
            return gate.before(text or "", a.parent, record=not a.check)
        return gate.lookup(text or "", a.minutes, a.confirm_absent)
    if a.cmd == "after":
        text, problem = _read_text(a)
        r = gate.after(a.ref, text, a.parent)
        if problem:
            r.message = f"warning: {problem}\n" + r.message
        return r
    if a.cmd == "dupcheck":
        return gate.dupcheck(a.hours, a.ignore)
    if a.cmd == "halt":
        return gate.halt(a.reason)
    if a.cmd == "resume":
        return gate.resume()
    if a.cmd == "status":
        return gate.status()
    if a.cmd == "queue":
        qu = Queue(gate)
        if a.qcmd == "load":
            return qu.load(a.plan, a.force)
        if a.qcmd == "next":
            r = qu.next(a.peek)
            if not a.json and "idx" in r.data:
                item = {k: r.data[k] for k in ("idx", "of", "parent", "text", "not_before")}
                r.message += "\n" + json.dumps(item, ensure_ascii=False)
            return r
        if a.qcmd == "done":
            return qu.done(a.ref)
        if a.qcmd == "skip":
            return qu.skip(a.reason)
        return qu.status()
    return Result(Code.USAGE, f"unknown command {a.cmd}")  # pragma: no cover


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        r = _run(a)
    except RuntimeError as e:  # e.g. corrupt state file
        r = Result(Code.USAGE, f"STOP: {e}")
    return _emit(r, a.json)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
