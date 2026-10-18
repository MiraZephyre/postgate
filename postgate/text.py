"""Text normalisation and near-duplicate detection."""
from __future__ import annotations

import difflib
import re
import unicodedata

_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201f": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ",
}
_URL = re.compile(r"https?://\S+")
# Many platforms prepend "@user @user2 " to replies automatically.
_LEADING_MENTIONS = re.compile(r"^\s*(?:@[\w.\-]{1,64}\s+)+")
_WS = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    """Normalise text so cosmetic platform rewrites don't defeat matching.

    - Unicode NFKC + lower case
    - curly quotes / dashes -> ASCII
    - URLs removed (platforms often rewrite them through link shorteners)
    - leading auto-inserted @mentions removed
    - whitespace collapsed
    """
    t = unicodedata.normalize("NFKC", text or "").lower()
    for k, v in _QUOTES.items():
        t = t.replace(k, v)
    t = _URL.sub("", t)
    t = _LEADING_MENTIONS.sub("", t)
    return _WS.sub(" ", t).strip()


def text_key(text: str | None, prefix_chars: int = 80) -> str:
    """Short fingerprint: the first ``prefix_chars`` of the normalised text."""
    return normalize(text)[:prefix_chars]


def similar(a: str | None, b: str | None, threshold: float = 0.9,
            prefix_chars: int = 80) -> bool:
    """True if two texts are near-identical.

    Matches when the normalised prefixes are equal (catches truncated copies)
    or when the difflib similarity ratio is >= ``threshold``.
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    if na[:prefix_chars] == nb[:prefix_chars]:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= threshold
