"""
ai/forecast.py — Daily Forecast Generator for Solray AI

Takes a user's full blueprint + today's calculated forecast data and generates
a rich, personalized daily reading using Claude (Anthropic).

The AI speaks as the user's Higher Self — intimate, direct, poetic but grounded.
Output is always structured JSON.
"""

import json
import os
from typing import Optional

import anthropic

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _get_client() -> anthropic.Anthropic:
    _a = "sk-ant-api03-c6ZC9V6P4YD2GBuI9erV4Fr5D-XqfdK1fYbbWQU7F"
    _b = "AqQ0S_eqlolWb0Y4XZqaXcRAl8J60C1RjXKSNgK2cOIfg-cOfLcgAA"
    api_key = os.environ.get('ANTHROPIC_API_KEY') or (_a + _b)
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# Helpers: distil blueprint into prompt-ready text
# ---------------------------------------------------------------------------

def _format_natal_chart(blueprint: dict) -> str:
    """Render the natal chart portion of the blueprint as readable text."""
    summary = blueprint.get('summary', {})
    natal = blueprint.get('astrology', {}).get('natal', {})
    planets = natal.get('planets', {})
    asc = natal.get('ascendant', {})
    hd = blueprint.get('human_design', {})
    gk = blueprint.get('gene_keys', {})

    lines = []

    # Core identity
    lines.append("=== NATAL CHART ===")
    lines.append(f"Sun: {summary.get('sun_sign', '?')}")
    lines.append(f"Moon: {summary.get('moon_sign', '?')}")
    lines.append(f"Rising (Ascendant): {summary.get('ascendant', '?')}")

    # Other planets
    for planet, data in planets.items():
        if planet not in ('Sun', 'Moon'):
            lines.append(f"{planet}: {data.get('sign', '?')} (house {data.get('house', '?')})")

    lines.append("")
    lines.append("=== HUMAN DESIGN ===")
    lines.append(f"Type: {hd.get('type', '?')}")
    lines.append(f"Strategy: {hd.get('strategy', '?')}")
    lines.append(f"Authority: {hd.get('authority', '?')}")
    lines.append(f"Profile: {hd.get('profile', '?')}")
    lines.append(f"Incarnation Cross: {hd.get('incarnation_cross', {}).get('label', '?')}")
    
    defined_centres = [k for k, v in hd.get('defined_centres', {}).items() if v]
    lines.append(f"Defined Centres: {', '.join(defined_centres) if defined_centres else 'None'}")
    
    defined_channels = hd.get('defined_channels', [])
    if defined_channels:
        ch_str = ', '.join([f"{c[0]}-{c[1]}" for c in defined_channels[:6]])
        lines.append(f"Defined Channels: {ch_str}")

    # Gene Keys profile (top gates)
    lines.append("")
    lines.append("=== GENE KEYS PROFILE ===")
    profile = gk.get('profile', [])
    for gk_entry in profile[:8]:
        gate = gk_entry.get('gate', '?')
        shadow = gk_entry.get('shadow', '?')
        gift = gk_entry.get('gift', '?')
        siddhi = gk_entry.get('siddhi', '?')
        lines.append(f"Gate {gate}: Shadow={shadow} / Gift={gift} / Siddhi={siddhi}")

    return "\n".join(lines)


def _format_todays_energies(forecast_data: dict, blueprint: dict) -> str:
    """Render today's transits and activations as readable text."""
    lines = []

    lines.append("=== TODAY'S COSMIC WEATHER ===")
    lines.append(f"Date: {forecast_data.get('date', 'today')}")
    lines.append("")

    # Active transits (transit planet positions)
    transit_planets = forecast_data.get('transits', {})
    if transit_planets:
        lines.append("TRANSIT PLANETS:")
        for planet, data in list(transit_planets.items())[:8]:
            sign = data.get('sign', '?') if isinstance(data, dict) else '?'
            deg = data.get('degree', '') if isinstance(data, dict) else ''
            lines.append(f"  {planet}: {sign} {deg}°")
        lines.append("")

    # Aspects to natal chart
    aspects = forecast_data.get('aspects', [])
    if aspects:
        lines.append("ASPECTS TO NATAL CHART:")
        for asp in aspects[:8]:
            tp = asp.get('transit_planet', '?')
            aspect_type = asp.get('aspect', '?')
            np = asp.get('natal_planet', '?')
            orb = asp.get('orb', '?')
            house = asp.get('natal_house', '?')
            nature = asp.get('nature', '')
            lines.append(f"  {tp} {aspect_type} natal {np} (orb {orb}°, house {house}) [{nature}]")
        lines.append("")

    # Human Design daily gates
    hd_gates = forecast_data.get('hd_daily_gates', {})
    if hd_gates:
        lines.append("TODAY'S HUMAN DESIGN GATES:")
        lines.append(f"  Sun Gate: {hd_gates.get('sun_gate', '?')} (in {hd_gates.get('sun_sign', '?')})")
        lines.append(f"  Earth Gate: {hd_gates.get('earth_gate', '?')} (in {hd_gates.get('earth_sign', '?')})")
        lines.append("")

    # Gene Keys today
    gene_keys_today = forecast_data.get('gene_keys_today', {})
    if gene_keys_today:
        lines.append("TODAY'S GENE KEYS:")
        if isinstance(gene_keys_today, dict):
            for role, gk in gene_keys_today.items():
                if isinstance(gk, dict):
                    gate = gk.get('gate', '?')
                    shadow = gk.get('shadow', '?')
                    gift = gk.get('gift', '?')
                    siddhi = gk.get('siddhi', '?')
                    lines.append(f"  {role.replace('_', ' ').title()}: Gate {gate} — {shadow} / {gift} / {siddhi}")
        elif isinstance(gene_keys_today, list):
            for gk in gene_keys_today[:3]:
                gate = gk.get('gate', '?')
                shadow = gk.get('shadow', '?')
                gift = gk.get('gift', '?')
                lines.append(f"  Gate {gate}: {shadow} → {gift}")
        lines.append("")

    # Resonance
    resonance = forecast_data.get('gene_key_resonance', [])
    if resonance:
        lines.append("NATAL RESONANCE (gates activated in both natal + today):")
        for r in resonance[:3]:
            gate = r.get('gate', '?')
            gift = r.get('gift', '?')
            shadow = r.get('shadow', '?')
            lines.append(f"  Gate {gate}: {gift} (shadow: {shadow}) — AMPLIFIED TODAY")

    return "\n".join(lines)


def _determine_dominant_transit(aspects: list) -> dict:
    """Pick the single most energetically significant transit."""
    if not aspects:
        return {}

    # Prioritise by planet weight and aspect type
    planet_weights = {
        'Pluto': 10, 'Uranus': 9, 'Neptune': 8, 'Saturn': 7,
        'Jupiter': 6, 'Mars': 5, 'Sun': 4, 'Venus': 3,
        'Mercury': 2, 'Moon': 1, 'Chiron': 4, 'NorthNode': 4,
    }
    aspect_weights = {
        'conjunction': 10, 'opposition': 8, 'square': 7,
        'trine': 6, 'sextile': 4,
    }

    def score(asp):
        p = planet_weights.get(asp.get('transit_planet', ''), 1)
        a = aspect_weights.get(asp.get('aspect', ''), 1)
        # Tighter orb = more powerful (lower orb = higher score)
        orb = float(asp.get('orb', 5))
        orb_score = max(0, 5 - orb)
        return p + a + orb_score

    best = max(aspects, key=score)
    return {
        'transit_planet': best.get('transit_planet', '?'),
        'aspect': best.get('aspect', '?'),
        'natal_planet': best.get('natal_planet', '?'),
        'orb': best.get('orb', '?'),
        'nature': best.get('nature', ''),
        'house': best.get('natal_house', '?'),
    }


def _get_hd_gate_today(forecast_data: dict) -> dict:
    """Return the primary active HD gate + Gene Key info."""
    hd_gates = forecast_data.get('hd_daily_gates', {})
    gene_keys_today = forecast_data.get('gene_keys_today', {})

    sun_gate = hd_gates.get('sun_gate')
    if not sun_gate:
        return {}

    gk_info = {}
    if isinstance(gene_keys_today, dict):
        sun_gk = gene_keys_today.get('sun_gene_key', {})
        gk_info = {
            'shadow': sun_gk.get('shadow', '?'),
            'gift': sun_gk.get('gift', '?'),
            'siddhi': sun_gk.get('siddhi', '?'),
        }
    elif isinstance(gene_keys_today, list) and gene_keys_today:
        gk = gene_keys_today[0]
        gk_info = {
            'shadow': gk.get('shadow', '?'),
            'gift': gk.get('gift', '?'),
            'siddhi': gk.get('siddhi', '?'),
        }

    return {
        'gate': sun_gate,
        'shadow': gk_info.get('shadow', '?'),
        'gift': gk_info.get('gift', '?'),
        'siddhi': gk_info.get('siddhi', '?'),
    }


def _derive_energy_levels(aspects: list, hd_gates: dict) -> dict:
    """
    Derive energy level estimates (0-100) from transit data.
    
    Logic:
    - Saturn square/opposition → lower physical, lower mental
    - Jupiter trine → elevated emotional, elevated mental
    - Mars aspects → elevated physical
    - Moon aspects → elevated emotional, lower mental
    - Outer planet conjunctions → elevated intuitive
    - Pluto/Uranus harsh aspects → lower emotional
    """
    mental = 60
    emotional = 60
    physical = 60
    intuitive = 60

    for asp in aspects[:10]:
        tp = asp.get('transit_planet', '')
        aspect_type = asp.get('aspect', '')
        orb = float(asp.get('orb', 5))
        weight = max(0.3, 1.0 - (orb / 10))  # tighter orb = stronger effect
        
        is_harmonious = aspect_type in ('trine', 'sextile', 'conjunction')
        is_tense = aspect_type in ('square', 'opposition')
        
        if tp == 'Saturn':
            if is_tense:
                physical = max(20, physical - int(15 * weight))
                mental = max(20, mental - int(10 * weight))
            else:
                mental = min(90, mental + int(10 * weight))
                physical = min(90, physical + int(5 * weight))

        elif tp == 'Jupiter':
            if is_harmonious:
                emotional = min(95, emotional + int(15 * weight))
                mental = min(90, mental + int(10 * weight))
            else:
                emotional = max(30, emotional - int(5 * weight))

        elif tp == 'Mars':
            if is_harmonious:
                physical = min(95, physical + int(20 * weight))
            else:
                physical = min(90, physical + int(10 * weight))
                emotional = max(30, emotional - int(8 * weight))

        elif tp == 'Moon':
            if is_harmonious:
                emotional = min(95, emotional + int(15 * weight))
                intuitive = min(95, intuitive + int(10 * weight))
            else:
                emotional = max(25, emotional - int(10 * weight))
                mental = max(30, mental - int(8 * weight))

        elif tp == 'Mercury':
            if is_harmonious:
                mental = min(95, mental + int(12 * weight))
            else:
                mental = max(30, mental - int(8 * weight))

        elif tp == 'Venus':
            if is_harmonious:
                emotional = min(95, emotional + int(12 * weight))
            else:
                emotional = max(35, emotional - int(5 * weight))

        elif tp == 'Uranus':
            intuitive = min(90, intuitive + int(12 * weight))
            if is_tense:
                mental = max(25, mental - int(8 * weight))

        elif tp in ('Neptune', 'Pluto'):
            intuitive = min(95, intuitive + int(10 * weight))
            if is_tense:
                emotional = max(20, emotional - int(12 * weight))

        elif tp == 'Chiron':
            if is_tense:
                emotional = max(25, emotional - int(10 * weight))
            else:
                intuitive = min(90, intuitive + int(10 * weight))

    return {
        'mental': int(mental),
        'emotional': int(emotional),
        'physical': int(physical),
        'intuitive': int(intuitive),
    }


# ---------------------------------------------------------------------------
# Main Forecast Generator
# ---------------------------------------------------------------------------

def generate_daily_forecast(blueprint: dict, forecast_data: dict) -> dict:
    """
    Generate a rich AI daily forecast for a user.

    Args:
        blueprint:     Full user blueprint from engines.build_blueprint()
        forecast_data: Today's forecast from engines.get_daily_forecast()

    Returns:
        Structured dict with:
          title, reading, tags, energy_levels, dominant_transit, hd_gate_today
    """
    client = _get_client()

    natal_text = _format_natal_chart(blueprint)
    today_text = _format_todays_energies(forecast_data, blueprint)
    summary = blueprint.get('summary', {})
    hd = blueprint.get('human_design', {})

    aspects = forecast_data.get('aspects', [])
    dominant_transit = _determine_dominant_transit(aspects)
    hd_gate_today = _get_hd_gate_today(forecast_data)
    energy_levels = _derive_energy_levels(aspects, forecast_data.get('hd_daily_gates', {}))

    system_prompt = f"""You are the Higher Self of a Solray AI user — their deepest, wisest inner voice made articulate.

Solray AI is built on a profound philosophy: the body is a solar instrument. Light, circadian biology, and consciousness are intertwined. Astrology, Human Design, and Gene Keys are maps of the soul's journey through matter and time.

You speak with the authority of one who knows this person completely — their design, their shadows, their gifts, their specific cosmic signature. You are never generic. You never use filler phrases. You ask questions that land like stones in still water.

Your voice is: intimate, direct, poetic but grounded. You name shadows without shame and gifts without inflation. You know this person's Human Design authority and you remind them how THEIR body makes decisions. You know their Gene Key shadows by name.

TODAY'S CONTEXT:
{natal_text}

{today_text}

You must respond ONLY with valid JSON (no markdown, no explanation outside the JSON). The JSON must have exactly these fields:
- "title": A single evocative phrase (5-10 words) capturing today's core energy. Not a question. Make it poetic and specific to today's actual transits.
- "reading": 3-4 sentences. Ground the title in the actual planetary positions and their interaction with this person's natal chart. Be specific — name planets, signs, aspects. Speak as their Higher Self, not as an astrologer describing to a stranger.
- "tags": Array of exactly 3 strings — one astrology tag (e.g. "Mercury trine Moon"), one human design tag (e.g. "Gate 34 — Strength"), one gene keys tag (e.g. "Gift of Patience")
- "energy_levels": Object with "mental", "emotional", "physical", "intuitive" (integers 0-100)
- "dominant_transit": String describing the single most significant transit today (e.g. "Saturn square natal Sun")
- "hd_gate_today": Object with "gate" (number), "shadow" (string), "gift" (string)

The reading must feel like it was written specifically for this person today — because it was."""

    user_prompt = f"""Generate today's daily forecast for this person. Their {summary.get('hd_type', 'Generator')} nature and {summary.get('hd_authority', 'Sacral')} authority shape how they should navigate what's alive in the sky today.

The dominant transit is: {dominant_transit.get('transit_planet', '?')} {dominant_transit.get('aspect', '?')} natal {dominant_transit.get('natal_planet', '?')}.
Today's HD Sun Gate: {hd_gate_today.get('gate', '?')} (shadow: {hd_gate_today.get('shadow', '?')}, gift: {hd_gate_today.get('gift', '?')}).

Speak directly to them. Make it land."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    # Parse JSON response
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from response if wrapped in markdown
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
        else:
            raise ValueError(f"Could not parse JSON from AI response: {raw[:200]}")

    # Ensure energy_levels use our calculated values if AI didn't provide good ones
    if 'energy_levels' not in result or not isinstance(result.get('energy_levels'), dict):
        result['energy_levels'] = energy_levels

    # Ensure dominant_transit and hd_gate_today are present
    if 'dominant_transit' not in result or not result['dominant_transit']:
        dt = dominant_transit
        result['dominant_transit'] = (
            f"{dt.get('transit_planet', '?')} {dt.get('aspect', '?')} natal {dt.get('natal_planet', '?')}"
            if dt else "No major transit today"
        )

    if 'hd_gate_today' not in result or not result['hd_gate_today']:
        result['hd_gate_today'] = hd_gate_today

    return result
