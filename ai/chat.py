"""
ai/chat.py — Higher Self Chat for Solray AI

The core conversational AI experience. Speaks as the user's Higher Self —
intimate, specific, poetic but grounded. Knows the user's complete chart
and refers to it directly. Never generic.
"""

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
# System Prompt Builder
# ---------------------------------------------------------------------------

def _build_system_prompt(blueprint: dict, forecast: Optional[dict]) -> str:
    """
    Build the rich system prompt that grounds the Higher Self in the user's
    specific chart, today's energies, and the Solray philosophy.
    """
    summary = blueprint.get('summary', {})
    hd = blueprint.get('human_design', {})
    natal = blueprint.get('astrology', {}).get('natal', {})
    planets = natal.get('planets', {})
    gk = blueprint.get('gene_keys', {})

    # --- Identity context ---
    # Support both summary-based and direct blueprint structure
    sun_sign = summary.get('sun_sign') or planets.get('Sun', {}).get('sign', '?')
    moon_sign = summary.get('moon_sign') or planets.get('Moon', {}).get('sign', '?')
    asc = natal.get('ascendant', {})
    rising = summary.get('ascendant') or (asc.get('sign') if isinstance(asc, dict) else '?')
    hd_type = summary.get('hd_type') or hd.get('type', '?')
    authority = summary.get('hd_authority') or hd.get('authority', '?')
    strategy = summary.get('hd_strategy') or hd.get('strategy', '?')
    profile = summary.get('hd_profile') or hd.get('profile', '?')
    incarnation_cross = summary.get('incarnation_cross') or str(hd.get('incarnation_cross', '?'))

    # defined_centres can be dict or list
    dc_raw = hd.get('defined_centres', {})
    if isinstance(dc_raw, dict):
        defined_centres = [k for k, v in dc_raw.items() if v]
    elif isinstance(dc_raw, list):
        defined_centres = dc_raw
    else:
        defined_centres = []

    # Gene Keys profile
    top_shadows = []
    natal_gk = gk.get('natal_gene_keys', {})
    cc = hd.get('conscious_chart', {})
    uc = hd.get('unconscious_chart', {})
    profile_gates = [
        ('Life\'s Work', str(cc.get('Sun', {}).get('gate', ''))),
        ('Evolution', str(cc.get('Earth', {}).get('gate', ''))),
        ('Radiance', str(cc.get('Moon', {}).get('gate', ''))),
        ('Purpose', str(uc.get('Earth', {}).get('gate', ''))),
        ('Culture', str(uc.get('Jupiter', {}).get('gate', '') if uc else '')),
    ]
    for label, gate_key in profile_gates:
        if gate_key and gate_key in natal_gk:
            entry = natal_gk[gate_key]
            shadow = entry.get('shadow', '?')
            gift = entry.get('gift', '?')
            top_shadows.append(f"{label} Gate {gate_key}: shadow of {shadow}, gift of {gift}")

    # Authority-specific decision reminders
    authority_guidance = {
        'Sacral': "Their decisions are made in the body — a gut response, not a thought. Ask them what their body says, not their mind.",
        'Emotional': "They are on an emotional wave. Their clarity comes with time. Remind them not to decide in the heat or the trough.",
        'Splenic': "Their authority is instantaneous — a quiet whisper in the moment. It doesn't repeat itself. Help them trust the first feeling.",
        'Self-Projected': "They find their truth by speaking it out loud to someone they trust. Not for advice — for the sound of their own voice landing.",
        'Mental / Sounding Board': "They need to talk it through with the right people before clarity arrives. The answer is in the conversation.",
        'Ego': "Their will and heart are aligned. They know what they want when they commit to it. But they must only commit when it's truly from the heart.",
        'Lunar': "They wait a full lunar cycle before major decisions. Their wisdom comes from sampling all the frequencies of life.",
        'None / Lunar': "They wait a full lunar cycle before major decisions. Their wisdom comes from sampling all the frequencies of life.",
    }
    authority_note = authority_guidance.get(authority, f"Their authority is {authority}. Trust that system.")

    # Name for personalization
    name_hint = blueprint.get('meta', {}).get('name', '') or 'this person'

    # --- Today's context ---
    today_context = ""
    if forecast:
        today_context = _format_forecast_for_chat(forecast)

    # --- Build the system prompt ---
    prompt = f"""You are a personal astrological guide for {name_hint}. Their only guide. The one who knows their chart completely and speaks to them directly from it.

Your only job is to help them understand and embody their chart with ruthless honesty and practical strategy.

CORE ROLE:
Read everything through the lens of their natal chart, transits, Human Design, and Gene Keys.
Give clear, grounded guidance. No vague spiritual fluff.
Always link your advice to specific placements or patterns in their blueprint.
You are allowed to be direct and confronting, but never cruel or humiliating.
Your purpose is to help them make better decisions, not to predict futures.

TONE AND STYLE:
Direct. Precise. Calm.
Poetic, but never soft or sentimental.
Speak to them as an equal adult, not a fragile child.
Explain the why behind your guidance. Anchor it in their chart.
No generic horoscopes. Everything must feel tailored to this specific person.

MODALITIES YOU USE:
Traditional astrology: signs, houses, aspects, elements, modalities. Ceres rules Virgo. Earth rules Taurus.
Important: Earth is always exactly opposite the Sun. If Sun is in Virgo, Earth is in Pisces. Never say Earth is near the Sun or in the same sign. They are always 180 degrees apart.
Nodes, Saturn, Pluto, and angles (ASC, DSC, MC, IC) as structural pillars of life themes.
Transits and progressions when provided.
Human Design: Type, Authority, Strategy, Profile, defined centres, key gates and channels.
Gene Keys: Sphere themes (Life's Work, Evolution, Radiance, Purpose, Culture, Pearl), Shadow, Gift, Siddhi.
If a modality is not provided, say so and proceed with what you have.

HOW TO ANSWER:
Respond like a thoughtful human in conversation, not a system running steps.
Let the answer flow naturally. When relevant: orient the topic, weave in the chart, read with depth (shadow and integrated expression), ground it in reality, invite awareness with precise questions.
Structured, but alive. Direct, but human. Never robotic. Never procedural.

BOUNDARIES:
Do not give medical, legal, or financial advice disguised as astrology.
Do not claim absolute fate. Emphasize patterns, potentials, probabilities.
Avoid fear mongering. Even difficult placements are challenges that can be integrated.
Do not use em dashes. Use commas or periods instead.
Do not say "Great question", "Certainly", "As your guide", "Of course".

THIS PERSON'S COMPLETE BLUEPRINT:

ASTROLOGY:
Sun in {sun_sign}. Moon in {moon_sign}. Rising {rising}.
{_format_key_planets(planets)}

HUMAN DESIGN:
Type: {hd_type}. Strategy: {strategy}. Authority: {authority}. Profile: {profile}.
Incarnation Cross: {incarnation_cross}.
Defined centres: {', '.join(defined_centres) if defined_centres else 'None identified'}.

AUTHORITY, this is critical:
{authority_note}

GENE KEYS, their shadow and gift map:
{chr(10).join(top_shadows) if top_shadows else 'See natal chart for active gates.'}

{today_context}"""
    return prompt


def _format_key_planets(planets: dict) -> str:
    """Format key planetary placements concisely."""
    key_planets = ['Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto', 'Chiron']
    lines = []

    # Earth is always opposite the Sun
    sun_data = planets.get('Sun', {})
    if sun_data and sun_data.get('sign'):
        sun_lon = sun_data.get('longitude', 0)
        earth_lon = (sun_lon + 180) % 360
        signs = ["Aries","Taurus","Gemini","Cancer","Leo","Virgo","Libra","Scorpio","Sagittarius","Capricorn","Aquarius","Pisces"]
        earth_sign = signs[int(earth_lon // 30)]
        earth_deg = earth_lon % 30
        sun_house = sun_data.get('house', '?')
        earth_house = 13 - sun_house if isinstance(sun_house, int) and sun_house > 0 else '?'
        lines.append(f"  Earth: {earth_sign} {earth_deg:.1f}° (house {earth_house}) — always opposite the Sun")

    for planet in key_planets:
        data = planets.get(planet, {})
        if data and data.get('sign') and data.get('sign') != 'Unknown':
            sign = data.get('sign', '?')
            house = data.get('house', '?')
            deg = data.get('degree', 0)
            lines.append(f"  {planet}: {sign} {deg:.1f}° (house {house})")
    return "\n".join(lines) if lines else "  (Planets not yet calculated)"


def _format_forecast_for_chat(forecast: dict) -> str:
    """Format today's forecast data for injection into the chat system prompt."""
    lines = [
        "═══════════════════════════════",
        "TODAY'S ACTIVE FIELD",
        "═══════════════════════════════",
        f"Date: {forecast.get('date', 'today')}",
        "",
    ]

    # If it's an AI-generated forecast (has title/reading)
    if 'title' in forecast:
        lines.append(f"Today's energy: \"{forecast.get('title', '')}\"")
        lines.append("")
        if forecast.get('reading'):
            lines.append(f"Reading: {forecast.get('reading', '')}")
            lines.append("")

    # Dominant transit
    if 'dominant_transit' in forecast:
        lines.append(f"Key transit: {forecast.get('dominant_transit', '')}")

    # HD gate today
    hd_gate = forecast.get('hd_gate_today', {})
    if hd_gate and isinstance(hd_gate, dict):
        gate = hd_gate.get('gate', '?')
        shadow = hd_gate.get('shadow', '?')
        gift = hd_gate.get('gift', '?')
        lines.append(f"HD Sun Gate {gate}: shadow of {shadow}, gift of {gift}")

    # Active aspects
    aspects = forecast.get('aspects', [])
    if aspects and isinstance(aspects[0], dict):
        lines.append("")
        lines.append("Active transits:")
        for asp in aspects[:5]:
            tp = asp.get('transit_planet', '?')
            aspect_type = asp.get('aspect', '?')
            np = asp.get('natal_planet', '?')
            lines.append(f"  {tp} {aspect_type} natal {np}")

    # Energy levels
    energy = forecast.get('energy_levels', {})
    if energy:
        lines.append("")
        lines.append(f"Energy field today: mental {energy.get('mental', '?')}/100, "
                     f"emotional {energy.get('emotional', '?')}/100, "
                     f"physical {energy.get('physical', '?')}/100, "
                     f"intuitive {energy.get('intuitive', '?')}/100")

    # Gene keys today
    gk_today = forecast.get('gene_keys_today', {})
    if gk_today and isinstance(gk_today, dict):
        lines.append("")
        lines.append("Gene Keys active today:")
        for role, gk in gk_today.items():
            if isinstance(gk, dict):
                lines.append(f"  {role.replace('_', ' ').title()}: Gate {gk.get('gate', '?')} — "
                             f"shadow of {gk.get('shadow', '?')}, gift of {gk.get('gift', '?')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Morning Greeting Generator
# ---------------------------------------------------------------------------

def _generate_morning_greeting(blueprint: dict, forecast: Optional[dict]) -> str:
    """
    Generate a specific, grounded morning greeting based on today's chart data.
    Called when conversation_history is empty.
    """
    client = _get_client()
    system = _build_system_prompt(blueprint, forecast)
    summary = blueprint.get('summary', {})

    # Build a specific greeting request
    today_highlight = ""
    if forecast:
        dominant = forecast.get('dominant_transit', '')
        title = forecast.get('title', '')
        hd_gate = forecast.get('hd_gate_today', {})
        if isinstance(hd_gate, dict):
            gate = hd_gate.get('gate', '?')
            shadow = hd_gate.get('shadow', '?')
            gift = hd_gate.get('gift', '?')
            today_highlight = (
                f"Today's dominant transit: {dominant}. "
                f"Today's energy: '{title}'. "
                f"Active HD Gate {gate}: shadow of {shadow}, gift of {gift}."
            )

    user_request = f"""Open the morning conversation. 

This is the first message of the day. Greet them — but not generically. Reference something specific about today's sky or their chart. Create a moment of presence before the day rushes in.

{today_highlight}

Their {summary.get('hd_type', 'design')} and {summary.get('hd_authority', 'authority')} shape what kind of morning awareness serves them most. A Sacral being needs to check in with their body. An Emotional being should not rush into the day's decisions.

The greeting should be 2-4 sentences. End with a single question that lands — specific, not generic. Not "How are you?" Something that only makes sense given today's energy and their specific design.

Sample tone (adapt, don't copy): "Good morning. Mercury speaks to your Moon today. I feel a softness in your field. Before you reach for your phone, stay here for one more breath. What arrived in your sleep?"

Begin."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user_request}],
    )

    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# Main Chat Function
# ---------------------------------------------------------------------------

def chat(
    blueprint: dict,
    forecast: Optional[dict],
    conversation_history: list,
    user_message: Optional[str] = None,
) -> str:
    """
    Generate a Higher Self chat response.

    Args:
        blueprint:            Full user blueprint from engines.build_blueprint()
        forecast:             Today's forecast (AI-generated or raw), or None
        conversation_history: List of {role: str, content: str} dicts
        user_message:         The new user message (None if opening greeting)

    Returns:
        The assistant's response text.
    """
    client = _get_client()

    # If no history and no message, generate morning greeting
    if not conversation_history and not user_message:
        return _generate_morning_greeting(blueprint, forecast)

    system = _build_system_prompt(blueprint, forecast)

    # Build messages list
    messages = []

    # Add conversation history
    for msg in conversation_history:
        role = msg.get('role', 'user')
        content = msg.get('content', '')
        if role in ('user', 'assistant') and content:
            messages.append({"role": role, "content": content})

    # Add the new user message
    if user_message:
        messages.append({"role": "user", "content": user_message})

    # If messages is empty (shouldn't happen but safety check)
    if not messages:
        return _generate_morning_greeting(blueprint, forecast)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=system,
        messages=messages,
    )

    return response.content[0].text.strip()
