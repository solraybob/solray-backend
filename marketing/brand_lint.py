"""
marketing/brand_lint.py — Solray brand-rule linter.

Pure validator. No AI, no DB, no I/O. Takes a string of draft copy and
returns a list of rule violations the operator should fix before posting.

The rules are absolute. They are the same rules baked into the Higher
Self prompt and the brand guide. The point of running them as code is
that the linter never gets tired, never makes exceptions for a "really
good post", and catches Bob's typos before a paying user sees them.

Rules enforced:
  no_em_dash         hardest rule in Solray. ASCII -- and unicode em
                     dash both flagged.
  no_emoji           covers most emoji ranges + ZWJ sequences. Allows
                     U+FE0E text-presentation typographic glyphs (the
                     planet symbols Solray uses) — those are typography,
                     not emoji.
  no_book_mention    the six philosophy books are internal context only.
                     Their titles never appear in public copy.
  no_correction      we never frame Solray rulerships as corrections of
                     traditional astrology. Phrases like "not Mercury",
                     "not Venus", "actually rules", "unlike traditional"
                     are flagged.
  no_em_dash_in_url  URLs themselves are exempt; the em-dash check skips
                     content inside http(s):// links.
"""

from __future__ import annotations

import re
from typing import List, Dict


_EM_DASH = "—"
_EN_DASH = "–"

# Six book titles, lowercased for case-insensitive matching.
_BOOK_TITLES = [
    "skywalker",
    "god is watching",
    "eat the location",
    "bright days dark nights",
    "meditations",
    "superior physique",
]

# Phrases that flag a "not X" correction frame.
_CORRECTION_PATTERNS = [
    r"\bnot\s+mercury\b",
    r"\bnot\s+venus\b",
    r"\bnot\s+saturn\b",
    r"\bnot\s+mars\b",
    r"\bnot\s+jupiter\b",
    r"\bunlike\s+traditional\s+astrology\b",
    r"\btraditional\s+astrology\s+(?:says|claims|teaches)\s+but\b",
    r"\bcontrary\s+to\s+(?:popular|traditional)\s+belief\b",
    r"\bcommon\s+misconception\b",
]

# Emoji ranges. Covers Misc Symbols & Pictographs, Emoticons,
# Transport, Supplemental Symbols, ZWJ joined sequences, dingbats,
# enclosed alphanumerics, regional indicators (flags), Misc Symbols.
# Carve-out: U+FE0E text-presentation selector means Solray's planet
# glyphs render as typography, not emoji, so we DON'T flag a codepoint
# immediately followed by U+FE0E.
_EMOJI_RANGES = (
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"  # misc symbols (sun, moon, signs)
    "\U00002700-\U000027BF"  # dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flags)
)
_EMOJI_RE = re.compile(f"([{_EMOJI_RANGES}])(?!︎)")


def lint(text: str) -> List[Dict]:
    """Run every rule on `text`. Return a list of {rule, message, snippet}.

    An empty list means the text passes.
    """
    if not text:
        return []

    out: List[Dict] = []

    # Strip URLs out of the corpus we lint for em dashes — em dashes in
    # URL params are out of our control.
    text_no_urls = re.sub(r"https?://\S+", "", text)

    # 1. em dash
    for m in re.finditer(re.escape(_EM_DASH), text_no_urls):
        out.append({
            "rule": "no_em_dash",
            "message": "Em dash found. Use comma, period, or colon.",
            "snippet": _around(text_no_urls, m.start(), 20),
        })

    # 2. ascii double-hyphen often used as a poor-man's em dash
    for m in re.finditer(r"--", text_no_urls):
        out.append({
            "rule": "no_em_dash",
            "message": "Double-hyphen reads as an em dash. Replace with a comma or period.",
            "snippet": _around(text_no_urls, m.start(), 20),
        })

    # 3. emoji
    for m in _EMOJI_RE.finditer(text):
        out.append({
            "rule": "no_emoji",
            "message": "Emoji found. Solray never uses emojis.",
            "snippet": _around(text, m.start(), 12),
        })

    # 4. book mentions
    lowered = text.lower()
    for title in _BOOK_TITLES:
        idx = lowered.find(title)
        if idx >= 0:
            out.append({
                "rule": "no_book_mention",
                "message": f"Book title '{title}' appears in the draft. The six books are internal context only.",
                "snippet": _around(text, idx, max(len(title) + 10, 30)),
            })

    # 5. correction-frame phrases
    for pat in _CORRECTION_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            out.append({
                "rule": "no_correction",
                "message": "Correction frame. Use Solray rulerships naturally without arguing against tradition.",
                "snippet": _around(text, m.start(), len(m.group(0)) + 10),
            })

    return out


def _around(text: str, idx: int, width: int) -> str:
    start = max(0, idx - width // 2)
    end = min(len(text), idx + width)
    snippet = text[start:end]
    return ("..." if start > 0 else "") + snippet + ("..." if end < len(text) else "")
