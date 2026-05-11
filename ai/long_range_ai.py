"""
ai/long_range_ai.py — AI Summary Generator for Long-Range Transits

Generates 2-sentence summaries for each active long-range transit,
personalised to the user's natal chart context.
"""

import json
import os
import sys

import anthropic

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _get_client() -> anthropic.Anthropic:
    """Construct an Anthropic client using the ANTHROPIC_API_KEY env var.

    Required. The previous hardcoded fallback was removed for credential
    safety. See ai/chat.py:_get_client for the full note.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Long-range AI generation requires it."
        )
    return anthropic.Anthropic(api_key=api_key)


def generate_transit_summaries(transits: list, blueprint: dict) -> list:
    """
    Given a list of active long-range transits (from long_range.calc_long_range_transits),
    generate 2-sentence summaries for each using AI.

    Returns the same list with 'summary' field filled in.
    """
    if not transits:
        return transits

    client = _get_client()
    summary = blueprint.get('summary', {})
    natal_planets = blueprint.get('astrology', {}).get('natal', {}).get('planets', {})

    sun_sign  = summary.get('sun_sign', 'Unknown')
    moon_sign = summary.get('moon_sign', 'Unknown')
    asc_sign  = summary.get('ascendant', 'Unknown')
    hd_type   = summary.get('hd_type', 'Unknown')

    # Build a compact transit list for the prompt
    transit_list = []
    for i, t in enumerate(transits):
        transit_list.append(
            f"{i+1}. {t['transit_planet']} conjunct natal {t['natal_point']} "
            f"(orb {t['orb']}°, {t['phase']}, "
            f"peak {t['peak']}, active {t['started']} to {t['ends']})"
        )

    system_prompt = """You are the Higher Self of a Solray AI user. You speak in their deepest, wisest inner voice.
Your task: write 2-sentence summaries for each major long-range transit they are moving through.

Rules:
- 2 sentences per transit. No more.
- Specific to the planets and natal point involved — not generic horoscope language.
- Grounded, direct, poetic but not flowery. No em dashes.
- Acknowledge the gravity of outer planet transits (Pluto/Neptune/Uranus = years, not weeks).
- Saturn transits to natal points = structural testing, accountability, crystallisation.
- Jupiter transits = expansion, new chapters, luck through right action.
- Saturn/Jupiter Returns = milestone cycles, not just aspects.
- Nodal Return = realignment with soul purpose.
- No filler phrases like "this is a time of" or "you may find yourself."

Return ONLY a JSON array, one entry per transit (same order as input), each with:
  { "index": 0, "summary": "Two sentences here." }"""

    user_prompt = f"""User's natal context:
Sun: {sun_sign}, Moon: {moon_sign}, Rising: {asc_sign}, HD Type: {hd_type}

Active long-range transits:
{chr(10).join(transit_list)}

Generate a 2-sentence summary for each transit. Return JSON array."""

    # Sonnet 4.6 for long-range AI (2026-05-12 retry, see forecast.py note).
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    try:
        results = json.loads(raw)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            results = json.loads(match.group(0))
        else:
            # Fallback: summaries unavailable
            return transits

    # Merge summaries back into transits
    for item in results:
        idx = item.get('index', -1)
        if 0 <= idx < len(transits):
            transits[idx]['summary'] = item.get('summary', '')

    return transits
