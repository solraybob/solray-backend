"""
ai/first_mirror.py — Generate the post-onboarding "First Mirror."

Codex's UX strategy memo, top-recommended item: after onboarding
finishes, the user should see one short reading that proves Solray
has actually understood them. Not a generic chart summary. Three
precise lines:

  1. The pattern you lead with
     (top-of-mind expression: ascendant, sun in its sign and house)

  2. The place you hide your power
     (the unintegrated piece: shadow gene key, Saturn placement,
     undefined centre that compensates loudly)

  3. The question your design keeps returning to
     (the arc / north node / incarnation cross / life path)

Each line is one sentence, specific to the chart, no astrology
jargon. The user sees these three before Today, before Chat, as the
first impression.

Single LLM call (Haiku, ~600 tokens) per onboarding. Returns dict:
  {"pattern": "...", "shadow": "...", "question": "..."}
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import anthropic

log = logging.getLogger("solray.first_mirror")


def _get_client() -> anthropic.Anthropic:
    """Same env-var-required pattern as the other AI modules. The
    rotated key in Railway is the only source; fail loudly if unset.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "First Mirror generation requires it."
        )
    return anthropic.Anthropic(api_key=api_key)


def generate_first_mirror(blueprint: dict) -> dict:
    """Compute the three First Mirror lines for a freshly-onboarded user.

    Returns a dict with keys 'pattern', 'shadow', 'question'. Each
    value is a single sentence (~15-30 words). The model is
    instructed to write each line in plain prose, no astrology
    jargon, but each line must be derived from a SPECIFIC placement
    in the user's actual chart so it could not have been written
    about anyone else.

    On failure (model error, parse error, missing fields), raises
    RuntimeError. The caller is expected to handle that and fall
    back gracefully on the frontend, never to invent content.
    """
    summary = blueprint.get('summary', {})
    natal = blueprint.get('astrology', {}).get('natal', {})
    hd = blueprint.get('human_design', {})
    gk = blueprint.get('gene_keys', {})
    planets = natal.get('planets', {})

    sun_sign = summary.get('sun_sign') or planets.get('Sun', {}).get('sign', '?')
    moon_sign = summary.get('moon_sign') or planets.get('Moon', {}).get('sign', '?')
    asc = natal.get('ascendant', {})
    rising = summary.get('ascendant') or (asc.get('sign') if isinstance(asc, dict) else '?')
    hd_type = summary.get('hd_type') or hd.get('type', '?')
    authority = summary.get('hd_authority') or hd.get('authority', '?')
    profile = summary.get('hd_profile') or hd.get('profile', '?')
    _ic = hd.get('incarnation_cross', {})
    incarnation_cross = (
        summary.get('incarnation_cross')
        or ((_ic.get('name') or _ic.get('label')) if isinstance(_ic, dict) else str(_ic))
        or '?'
    )

    saturn = planets.get('Saturn', {})
    saturn_sign = saturn.get('sign', '?') if isinstance(saturn, dict) else '?'
    saturn_house = saturn.get('house', '?') if isinstance(saturn, dict) else '?'

    north_node = (
        natal.get('north_node', {}).get('sign', '?')
        if isinstance(natal.get('north_node'), dict)
        else '?'
    )

    # Top Gene Key shadow if available
    lifes_work = gk.get('lifes_work', {}) if isinstance(gk.get('lifes_work'), dict) else {}
    lw_shadow = lifes_work.get('shadow', '')
    lw_gift = lifes_work.get('gift', '')
    lw_gate = lifes_work.get('gate', '')

    name = blueprint.get('meta', {}).get('name', '') or 'this person'

    prompt = f"""You are the Higher Self of {name}, writing the THREE LINES of the First Mirror, the first thing they see after onboarding into Solray.

The promise of this moment: prove that Solray has actually understood them. Not a chart summary. Not a horoscope. Three sentences that land specifically about THIS person on THIS chart, sentences that could not have been written about anyone else without being wrong.

THEIR CHART:
- Sun in {sun_sign}
- Moon in {moon_sign}
- Rising {rising}
- Saturn in {saturn_sign}, house {saturn_house}
- North Node in {north_node}
- Human Design type: {hd_type}
- Authority: {authority}
- Profile: {profile}
- Incarnation Cross: {incarnation_cross}
- Life's Work Gene Key: gate {lw_gate}, shadow of {lw_shadow}, gift of {lw_gift}

WRITE THREE LINES, one for each of these:

1. THE PATTERN YOU LEAD WITH.
   The way they show up first, the surface they meet the world from. Read this from the Rising sign + Sun. Translate into plain behavior, not astrology. What does this person do without thinking about it? What is the move they make in any new room?

2. THE PLACE YOU HIDE YOUR POWER.
   The unintegrated piece, the spot the chart says they undersell or compensate around. Read this from Saturn + Life's Work shadow. Translate into the specific small move they make to avoid using the part of themselves that would actually move things forward.

3. THE QUESTION YOUR DESIGN KEEPS RETURNING TO.
   The arc they are here to live. Read this from North Node + Incarnation Cross. Translate into a real question, not "what is your purpose," literally the recurring question their life keeps asking them.

Each line is ONE sentence, 18 to 30 words. Plain English. NO astrology terms in the line itself (no "Saturn," no "Rising," no "Gene Key shadow"). The placement is your source; the line is the truth that comes from it.

Each line must be specific to this chart in a way that would feel wrong to a person with different placements. If a sentence could plausibly land for someone with a different Sun and Rising, rewrite it.

NO em dashes. Use commas, periods, colons.
NO opening "You are..." pattern. Each line starts with a verb or a concrete observation.

Return ONLY a JSON object:
{{
  "pattern": "...",
  "shadow": "...",
  "question": "..."
}}"""

    client = _get_client()
    log.info(f"[first_mirror] generating for {name} (sun {sun_sign}, rising {rising})")

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    start = text.find('{')
    end = text.rfind('}') + 1
    if start < 0 or end <= start:
        log.warning(f"[first_mirror] no JSON object in response: {text[:200]!r}")
        raise RuntimeError("First Mirror model returned no JSON.")
    try:
        parsed = json.loads(text[start:end])
    except json.JSONDecodeError as e:
        log.warning(f"[first_mirror] JSON parse failed: {e}. text={text[start:end]!r}")
        raise RuntimeError("First Mirror response was not parseable JSON.") from e

    for key in ("pattern", "shadow", "question"):
        if not isinstance(parsed.get(key), str) or not parsed[key].strip():
            log.warning(f"[first_mirror] missing or empty key {key!r} in {parsed!r}")
            raise RuntimeError(f"First Mirror response missing required key {key!r}.")

    # Strip em dashes as a final guard, same pattern as the Oracle output filter.
    import re
    em_chars = "—–―"
    for key in ("pattern", "shadow", "question"):
        v = parsed[key]
        if any(c in v for c in em_chars):
            v = re.sub(rf"\s+[{em_chars}]+\s+", ", ", v)
            v = re.sub(rf"[{em_chars}]+", ", ", v)
            v = re.sub(r",\s*,+", ",", v)
            parsed[key] = v

    log.info(
        f"[first_mirror] generated for {name}: "
        f"pattern={parsed['pattern'][:60]!r} "
        f"shadow={parsed['shadow'][:60]!r} "
        f"question={parsed['question'][:60]!r}"
    )
    return {
        "pattern": parsed["pattern"].strip(),
        "shadow": parsed["shadow"].strip(),
        "question": parsed["question"].strip(),
    }
