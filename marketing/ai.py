"""
marketing/ai.py — AI helpers for the marketing tool.

Two surfaces for now:
  generate_angles_for_signal(signal) → list of 5 ranked Solray-shaped
                                       angles with platform + copy + why
  generate_platform_variants(raw_note, channels) → per-channel drafts of
                                                   the same idea

Both are admin-triggered (one click per call) so token spend is bounded.
Both run through brand_lint.lint() before returning, so violations are
surfaced inline with the draft. Drafts that violate hard rules still
come back; we trust Bob to make the final edit, but we name the issues.

Model strategy:
  Haiku for fast, cheap drafting (signals are surfaced in volume).
  Sonnet only when we explicitly want depth — currently unused here,
  reserved for Founder Voice Studio second-pass refinement if Bob
  asks for it.
"""

from __future__ import annotations

import os
import json
import logging
from typing import List, Dict, Optional

import anthropic

from .brand_lint import lint as brand_lint_text

log = logging.getLogger("solray.marketing.ai")


def _client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in the environment.")
    return anthropic.Anthropic(api_key=key)


# ----------------------------------------------------------------------------
# Solray voice context shared by every AI call in this module.
# ----------------------------------------------------------------------------

_VOICE_CONTEXT = """You are drafting marketing copy in Solray's voice. Solray is a contemplative astrology / Human Design / Gene Keys app run by a solo founder. The voice rules are absolute:

NEVER use em dashes (—) or double hyphens (--). Use commas, periods, or colons.
NEVER use emojis. Anywhere. Zero exceptions.
NEVER reference Solray's internal philosophy books by name.
NEVER frame Solray rulerships as corrections of traditional astrology. Do not write "not Mercury" or "unlike traditional astrology". Use Solray rulerships naturally (Earth rules Taurus, Ceres rules Virgo) the way a native speaker uses their language.
NEVER write generic horoscope cliches ("the universe wants you to surrender into the mystery").
NEVER use AI tics ("I sense", "I feel", "you may be experiencing", "let me hold space for you").

Voice posture: precise, observational, contemplative. Specific over universal — if a sentence could apply to anyone, rewrite it. Living By Design. Function and beauty are the only two reasons anything exists.

Voice anchors (what Solray sounds like):
  "You don't trust easy. That's the cost of the standards you carry."
  "The thing you're calling failure is probably timing."
  "Your body is telling you the truth your sentences haven't caught up to."
  "Slow down. There's a question under the question."
  "Taurus people are slow because Earth moves slow. The body knows what year it is."
"""


# ----------------------------------------------------------------------------
# Signal Radar: turn a signal into 5 ranked angles
# ----------------------------------------------------------------------------

def generate_angles_for_signal(
    signal_title: str,
    signal_body: Optional[str] = None,
    signal_source: str = "manual",
) -> List[Dict]:
    """Take a single signal (title + optional body) and return up to 5
    ranked angles Solray could publish in response. Each angle is:

        {
          "platform":   "x" | "instagram" | "tiktok" | "linkedin" | "blog",
          "copy":       full draft, ready to post (no em dashes, no emojis),
          "why":        one-line reason this angle works for Solray,
          "lint":       list of brand-rule violations (empty if clean),
        }
    """
    client = _client()

    prompt = f"""{_VOICE_CONTEXT}

A signal is something happening in the world (a transit, a viral conversation, a cultural moment, a public event) that Solray could plausibly respond to. Your job: read this signal and propose 5 Solray angles, each on a different platform if that helps reach.

Signal source: {signal_source}
Signal title: {signal_title}
Signal body: {signal_body or '(no additional context)'}

Return strictly a JSON array of 5 objects, each with these keys:
  platform: one of x, instagram, tiktok, linkedin, blog
  copy: the actual draft post, ready to publish
  why: one sentence on why this angle lands for Solray's contemplative astrology audience

Length per platform:
  x: under 280 characters, ideally one sentence with snap
  instagram: 1-3 short paragraphs, can pair with an image
  tiktok: 4-6 short lines that work as voiceover
  linkedin: 2-4 paragraphs, opening line carries the weight
  blog: a single tight opening paragraph (we'll expand later)

If you cannot find 5 distinct angles, return fewer rather than padding. Quality over count.

Output ONLY the JSON array, no preface, no closing remarks. Strict JSON.
"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text if response.content else ""
        text = text.strip()
        # Tolerate code-fenced JSON.
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        angles = json.loads(text)
        if not isinstance(angles, list):
            raise ValueError("AI did not return a JSON array")
    except Exception as e:
        log.warning("[marketing.ai] angle generation failed: %s", e)
        return []

    # Lint every draft so violations surface alongside the copy.
    out: List[Dict] = []
    for a in angles[:5]:
        if not isinstance(a, dict):
            continue
        copy = str(a.get("copy") or "").strip()
        out.append({
            "platform": str(a.get("platform") or "").strip().lower() or "x",
            "copy": copy,
            "why": str(a.get("why") or "").strip(),
            "lint": brand_lint_text(copy),
        })
    return out


# ----------------------------------------------------------------------------
# Founder Voice Studio: raw note → per-platform variants
# ----------------------------------------------------------------------------

_DEFAULT_CHANNELS = ["x", "instagram", "linkedin"]


def generate_platform_variants(
    raw_note: str,
    channels: Optional[List[str]] = None,
) -> List[Dict]:
    """Take Bob's raw observation and return one draft per requested
    channel. Each result is the same shape as generate_angles_for_signal:

        { platform, copy, why, lint }
    """
    if not raw_note or not raw_note.strip():
        return []

    channels = channels or _DEFAULT_CHANNELS

    client = _client()

    prompt = f"""{_VOICE_CONTEXT}

Bob just wrote down a raw observation. Your job: turn this into one polished draft per requested platform, in Solray's voice. Do not sand off the contemplative tone. Do not summarize into bullet points. Keep the same idea; adapt the form to the platform.

Raw note from Bob:
---
{raw_note.strip()}
---

Platforms requested: {', '.join(channels)}

Length per platform:
  x: under 280 characters, ideally one sentence with snap
  instagram: 1-3 short paragraphs, can pair with an image
  tiktok: 4-6 short lines that work as voiceover
  linkedin: 2-4 paragraphs, opening line carries the weight
  blog: a single tight opening paragraph (we'll expand later)

Return strictly a JSON array of objects, one per requested platform, each with:
  platform: the platform string
  copy: the draft, ready to publish
  why: one sentence on what about Bob's note this draft preserves

Output ONLY the JSON array. Strict JSON.
"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text if response.content else ""
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        variants = json.loads(text)
        if not isinstance(variants, list):
            raise ValueError("AI did not return a JSON array")
    except Exception as e:
        log.warning("[marketing.ai] voice studio failed: %s", e)
        return []

    out: List[Dict] = []
    for v in variants[: len(channels)]:
        if not isinstance(v, dict):
            continue
        copy = str(v.get("copy") or "").strip()
        out.append({
            "platform": str(v.get("platform") or "").strip().lower() or channels[0],
            "copy": copy,
            "why": str(v.get("why") or "").strip(),
            "lint": brand_lint_text(copy),
        })
    return out
