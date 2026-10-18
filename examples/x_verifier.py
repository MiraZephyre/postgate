"""Example verifier for X (Twitter). A STUB: fill in the two TODOs.

Point postgate at it with::

    verifier = "x_verifier:XVerifier"     # module must be importable (PYTHONPATH)
    [verifier_options]
    username = "your_account"

Keep credentials out of config files and code: read them from the environment
(the variable name below is only a suggestion).

What postgate needs from you:

* ``fetch(post_id)`` -> PostInfo(id, text, url, parent, author, created_ts)
  or None when the post doesn't exist. ``parent`` is the id of the post it
  replies to (None for a standalone post).
* ``recent(since_ts)`` -> every post by the account since ``since_ts``,
  *including replies*, or None if you can't list them. If your listing can't
  see replies, return None for reply-heavy bots - otherwise `lookup` would
  report "absence confirmed" for a reply it simply can't see.

Raise VerifierUnavailable on timeouts, 429s and 5xx so postgate halts instead
of guessing.
"""
from __future__ import annotations

import os
import re

from postgate import PostInfo, Verifier, VerifierUnavailable  # noqa: F401


class XVerifier(Verifier):
    name = "x"

    def __init__(self, username: str, token_env: str = "X_API_TOKEN", timeout: float = 20):
        self.username = username
        self.token = os.environ.get(token_env, "")
        self.timeout = timeout

    def parse_ref(self, ref: str) -> str:
        # Accepts "https://x.com/<user>/status/<id>" or a bare numeric id.
        m = re.search(r"/status(?:es)?/(\d+)", ref or "") or re.fullmatch(r"\s*(\d+)\s*", ref or "")
        return m.group(1) if m else ""

    def fetch(self, post_id: str) -> PostInfo | None:
        # TODO: call your API client for one post, e.g. GET a post by id with
        # the author and "replied to" reference expanded. Map the response:
        #   return PostInfo(id=..., text=..., url=f"https://x.com/{self.username}/status/{id}",
        #                   parent=<replied-to id or None>, author=<username>,
        #                   created_ts=<unix seconds>)
        # Return None on a clean "not found"; raise VerifierUnavailable on errors.
        raise NotImplementedError("fill in XVerifier.fetch for your API access")

    def recent(self, since_ts: float) -> list[PostInfo] | None:
        # TODO: list the account's own posts AND replies newer than since_ts.
        # Return None if your access level can't list them.
        return None
