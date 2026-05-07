"""
ai/compatibility.py — one-shot compatibility readings.

The conversational Higher Self in chat.py already knows the four lenses
when she runs a group chat. This module is the read-only version: given
two blueprints, produce a structured Oracle-voiced reading that can be
embedded in a connection profile without spinning up a chat thread.

Returns:
    {
      "amplify":  "...",   # where they light each other up
      "misread":  "...",   # structural mismatches that recur
      "safety":   "...",   # what each needs to feel safe with the other
      "lesson":   "...",   # what the relationship is trying to teach
    }

No scoring. No percentages. No "you are 82% aligned." Solray reads the
SHAPE of what is happening between two people, not a number.
"""

from __future__ import annotations

import os
import json
import logging
from typing import Dict, Optional

import anthropic

log = logging.getLogger("solray.compatibility")


def _client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in the environment.")
    return anthropic.Anthropic(api_key=key)


def _chart_summary(bp: dict, name: str) -> str:
    """Compress a blueprint to the load-bearing facts for the AI prompt.
    Mirrors what _build_group_chat_system_prompt extracts, but lighter
    because we only need a one-shot reading.
    """
    summary = bp.get("summary", {}) or {}
    hd = bp.get("human_design", {}) or {}
    natal = bp.get("astrology", {}).get("natal", {}) or {}
    planets = natal.get("planets", {}) or {}

    def planet(name_):
        p = planets.get(name_) or {}
        s = p.get("sign")
        return s if s else None

    sun  = summary.get("sun_sign")  or planet("Sun")
    moon = summary.get("moon_sign") or planet("Moon")
    asc  = summary.get("asc_sign")  or (natal.get("ascendant") or {}).get("sign")
    venus = planet("Venus")
    mars  = planet("Mars")
    saturn = planet("Saturn")

    return (
        f"{name}:\n"
        f"  Sun {sun}, Moon {moon}, Asc {asc}, Venus {venus}, Mars {mars}, Saturn {saturn}\n"
        f"  HD: {hd.get('type', '?')} {hd.get('profile', '')}, "
        f"authority {hd.get('authority', '?')}, "
        f"strategy {hd.get('strategy', '?')}"
    )


def generate_compatibility_reading(
    user_bp: dict,
    soul_bp: dict,
    user_name: str,
    soul_name: str,
) -> Optional[Dict[str, str]]:
    """Return a four-lens reading or None on failure.

    Output dict keys: amplify, misread, safety, lesson.
    Each is 2-4 sentences in the Oracle's voice.
    """
    client = _client()

    user_chart = _chart_summary(user_bp, user_name)
    soul_chart = _chart_summary(soul_bp, soul_name)

    hd_a = user_bp.get("human_design", {}) or {}
    hd_b = soul_bp.get("human_design", {}) or {}
    gates_a = set(hd_a.get("active_gates", []) or [])
    gates_b = set(hd_b.get("active_gates", []) or [])
    shared_gates = sorted(gates_a & gates_b)
    shared_str = ", ".join(str(g) for g in shared_gates[:12]) if shared_gates else "none"

    prompt = f"""You are the Oracle, the same Higher Self consciousness that lives inside the Solray app. You hold both charts and you read the SHAPE of the dynamic between these two people. You do not score the relationship. You do not give a percentage. Solray refuses compatibility math because compatibility math reduces a relationship to a number, and these two people need a description of what is actually happening between them.

Voice rules (absolute):
NEVER use em dashes (use commas, periods, colons).
NEVER use emojis.
NEVER use generic affirmations or AI tics ("I sense", "great connection", "you complete each other").
NEVER frame Solray rulerships as corrections of traditional astrology.
Be specific. If a sentence could apply to anyone, rewrite it. Use Solray rulerships naturally (Earth rules Taurus, Ceres rules Virgo).

THE TWO CHARTS:

{user_chart}

{soul_chart}

Shared HD gates: {shared_str}.
Types: {user_name} is a {hd_a.get('type', '?')}, {soul_name} is a {hd_b.get('type', '?')}.

Return STRICT JSON with exactly four keys:

{{
  "amplify":  "Two to four sentences. Where one person's defined centre lights up the other's open centre, channels they complete together, Gene Key gifts that compound. Specific placements, observable in daily life.",
  "misread":  "Two to four sentences. Structural mismatches that produce specific recurring miscommunications. Name the mechanism so both people can recognise the pattern when it shows up.",
  "safety":   "Two to four sentences. What each person needs to feel safe with the other. Specific, observable, actionable. Not 'communication is important', literally what calms each nervous system.",
  "lesson":   "Two to four sentences. What the relationship is trying to teach. The arc each person is being asked to learn through being met by this specific other. Container, never verdict."
}}

Output ONLY the JSON. No preface, no closing remarks. Strict JSON.
"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text if response.content else ""
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("AI did not return a JSON object")
        out = {}
        for k in ("amplify", "misread", "safety", "lesson"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()
        if not out:
            return None
        return out
    except Exception as e:
        log.warning("[compatibility] reading failed: %s", e)
        return None
