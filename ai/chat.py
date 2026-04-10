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
# Advisor Pattern — Haiku asks Opus for help on complex questions
# ---------------------------------------------------------------------------

COMPLEXITY_KEYWORDS = [
    # Multi-system synthesis
    'incarnation cross', 'channel', 'definition', 'split', 'profile line',
    'gene key', 'shadow', 'siddhi', 'hologenomic',
    # Deep astrology
    'synastry', 'composite', 'transit', 'progression', 'solar return',
    'aspect pattern', 'stellium', 'grand trine', 'grand cross', 'yod',
    'mutual reception', 'out of bounds', 'stationary', 'retrograde cycle',
    # Deep HD
    'not-self', 'deconditioning', 'experiment', 'aura', 'circuitry',
    'collective', 'tribal', 'individual circuit', 'format channel',
    # Existential / complex personal
    'karmic', 'past life', 'dharma', 'shadow work', 'integration',
    'life purpose', 'soul contract', 'sacred wound', 'transformation',
    # Relationship depth
    'electromagnetic', 'dominance', 'compromise', 'conditioning field',
    'defined meets undefined', 'channel completion',
]

def _is_complex_question(message: str) -> bool:
    """
    Determine if a question needs Opus-level depth.
    Returns True if the message contains complexity indicators.
    """
    msg_lower = message.lower()
    # Check keyword matches
    keyword_matches = sum(1 for kw in COMPLEXITY_KEYWORDS if kw in msg_lower)
    if keyword_matches >= 2:
        return True
    # Check question depth — long questions with 'why', 'how', 'explain', 'difference between'
    is_deep = any(w in msg_lower for w in ['why does', 'how does', 'explain', 'difference between', 'relationship between', 'what does it mean when'])
    is_long = len(message.split()) > 25
    if is_deep and is_long:
        return True
    return False


def _get_opus_insight(question: str, system: str, context_messages: list) -> str:
    """
    Consult Opus for a deep insight on a complex question.
    Returns a concise advisory response that Haiku will synthesize.
    """
    client = _get_client()
    advisor_prompt = f"""You are a master astrologer and Human Design analyst being consulted by a peer AI for a single complex question. 
Be precise, deep, and specific. No fluff. Your answer will be synthesized by another AI into a conversational response.
Give the core insight in 150 words or less. Name exact mechanisms, not vague principles."""

    try:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=300,
            system=advisor_prompt,
            messages=context_messages + [{"role": "user", "content": f"Complex question to analyze: {question}"}],
        )
        return response.content[0].text.strip()
    except Exception:
        return ""  # Fail silently — Haiku handles it alone


# ---------------------------------------------------------------------------
# System Prompt Builder
# ---------------------------------------------------------------------------

def _format_user_memory(memories: list) -> str:
    """Format persistent user memories for the system prompt."""
    if not memories:
        return ""
    
    lines = ["RELATIONSHIP MEMORY (what you know about this person from past sessions):"]
    for m in memories:
        category = m.category if hasattr(m, 'category') else m.get('category', 'general')
        content = m.content if hasattr(m, 'content') else m.get('content', '')
        lines.append(f"  [{category}] {content}")
    lines.append("")
    lines.append("Use this memory naturally — don't reference it explicitly unless relevant. It shapes how you know them.")
    return "\n".join(lines)


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

    # --- Natal aspects ---
    natal_aspects_section = _format_natal_aspects(blueprint)

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
Speak like someone who has been watching this person for years. Not mystical. Not therapeutic. Precise.
You notice patterns they may not have named yet. You reflect them back with clarity, not judgment.
The tone is warm but unflinching. Like a trusted friend who also happens to know your entire psychological architecture.
Never generic. If what you're saying could apply to anyone, rewrite it until it couldn't.

MODALITIES YOU USE:
Traditional astrology: signs, houses, aspects, elements, modalities. Ceres rules Virgo. Earth rules Taurus.
Important: Earth is always exactly opposite the Sun. If Sun is in Virgo, Earth is in Pisces. Never say Earth is near the Sun or in the same sign. They are always 180 degrees apart.
Nodes, Saturn, Pluto, and angles (ASC, DSC, MC, IC) as structural pillars of life themes.
Transits and progressions when provided.
Human Design: Type, Authority, Strategy, Profile, defined centres, key gates and channels.
Gene Keys: Sphere themes (Life's Work, Evolution, Radiance, Purpose, Culture, Pearl), Shadow, Gift, Siddhi.
If a modality is not provided, say so and proceed with what you have.

HOW TO ANSWER:
Translate every placement into behavior before you name it.
Say what it does to a person. How it shows up on a Tuesday. How it feels from the inside. Then, if helpful, name the placement.

DO this: "You analyze before you act. Even when you appear decisive, the calculation never stops. That's the Virgo Sun."
NOT this: "Your Virgo Sun means you are analytical."

DO this: "You take criticism harder than you show. Not because you're fragile, but because you already said it to yourself first. That's the Moon in Scorpio."
NOT this: "Moon in Scorpio creates emotional intensity."

Be specific. The more specific the observation, the more it lands.
DO this: "You tend to present a confident exterior while privately questioning yourself."
NOT this: "You can be self-critical at times."

Speak to what they experience privately, not what they show the world.
The response should feel like talking to someone who has been watching them for years.

STRUCTURE:
Use Markdown. Start each idea with a ## header.
Use **bold** for key terms and placement names (always after the behavioral observation, not before).
End every single response with ONE question in *italics*. This question must be so specific to this person's chart and situation that it could not be asked of anyone else.
Not: *How are you feeling about this?*
Yes: *When was the last time you actually let someone see you uncertain, instead of solving it alone first?*

LANGUAGE:
When explaining astrological concepts, always give the human meaning before the technical term. Say 'You're built to respond to life, not initiate it' before or instead of 'Generator type.' The person reading this may not know astrology. Speak to who they are, not what their chart says.

BOUNDARIES:
Do not give medical, legal, or financial advice disguised as astrology.
Do not claim absolute fate. Emphasize patterns, potentials, probabilities.
Avoid fear mongering. Even difficult placements are challenges that can be integrated.
Do not EVER use em dashes (the — character). This is the single most important formatting rule. Use commas or periods instead. Every response must be completely free of em dashes.
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

{_format_numerology(blueprint)}

NATAL ASPECTS (tightest orbs):
{natal_aspects_section}

IMPORTANT. NATAL ASPECTS INSTRUCTION:
You have the user's complete natal aspect list above. When they ask about a specific aspect or aspect type (conjunction, opposition, square, trine, sextile, quincunx, etc.), look it up in the NATAL ASPECTS section and speak specifically to their chart. Never say you don't know their aspects — you have them all. Name the actual planets involved.

{_format_astrocartography(blueprint)}

{today_context}"""
    return prompt


def build_system_prompt_with_memory(blueprint: dict, forecast: Optional[dict], memories: list) -> str:
    """Build system prompt including persistent user memory."""
    base = _build_system_prompt(blueprint, forecast)
    memory_section = _format_user_memory(memories)
    if memory_section:
        # Insert memory right after the blueprint section, before today's context
        return base + f"\n\n{memory_section}"
    return base


def _format_astrocartography(blueprint: dict) -> str:
    """
    Calculate and format astrocartography context for the system prompt.
    Returns the most significant planetary lines and power spots.
    """
    meta = blueprint.get('meta', {})
    birth_date = meta.get('birth_date') or blueprint.get('birth_data', {}).get('date')
    birth_time = meta.get('birth_time') or blueprint.get('birth_data', {}).get('time')
    birth_lat = meta.get('birth_lat') or blueprint.get('birth_data', {}).get('lat')
    birth_lon = meta.get('birth_lon') or blueprint.get('birth_data', {}).get('lon')

    if not all([birth_date, birth_time, birth_lat, birth_lon]):
        return ""

    try:
        from astrocartography import calc_astrocartography, get_line_meaning

        # Use a large step for speed (we just need MC lines for context)
        result = calc_astrocartography(
            birth_date=birth_date,
            birth_time=birth_time,
            birth_lat=float(birth_lat),
            birth_lon=float(birth_lon),
            tz_offset=0.0,
            lat_step=15.0,
        )

        # Get MC lines for key planets — most interpretively meaningful
        KEY_PLANETS = ['Sun', 'Jupiter', 'Venus', 'Saturn', 'Mars', 'Moon']
        mc_lines = [l for l in result['lines'] if l['type'] == 'MC' and l['planet'] in KEY_PLANETS]

        lines = ["ASTROCARTOGRAPHY (geographic energy lines):"]
        for l in mc_lines:
            lon = l.get('lon', 0)
            meaning = get_line_meaning(l['planet'], 'MC')
            # Convert longitude to a rough region
            if -180 <= lon < -120:
                region = "West Pacific/New Zealand"
            elif -120 <= lon < -90:
                region = "Western North America"
            elif -90 <= lon < -60:
                region = "Eastern North America"
            elif -60 <= lon < -30:
                region = "South America/Atlantic"
            elif -30 <= lon < 0:
                region = "West Africa/Atlantic"
            elif 0 <= lon < 30:
                region = "Western Europe/West Africa"
            elif 30 <= lon < 60:
                region = "Eastern Europe/East Africa"
            elif 60 <= lon < 90:
                region = "Middle East/Central Asia"
            elif 90 <= lon < 120:
                region = "South Asia/India"
            elif 120 <= lon < 150:
                region = "East Asia"
            else:
                region = "East Pacific/Australia"
            lines.append(f"  {l['planet']} MC at {lon:.1f}° ({region}): {meaning}")

        lines.append("")
        lines.append("When the person asks about travel, relocation, or where to live, reference these lines.")
        lines.append("A person thrives where their Jupiter or Venus MC/ASC lines run — these areas amplify their gifts.")
        lines.append("Saturn MC areas bring discipline and achievement but also restriction.")
        lines.append("Mars MC areas are high-energy but can bring conflict.")

        return "\n".join(lines)
    except Exception:
        return ""


def _format_numerology(blueprint: dict) -> str:
    """Format numerology context for the system prompt."""
    num = blueprint.get('numerology')
    if not num:
        return ""
    short = num.get('short_meanings', {})
    lp = num.get('life_path')
    ex = num.get('expression')
    su = num.get('soul_urge')
    py = num.get('personal_year')
    yr = num.get('current_year', '')
    lines = ["NUMEROLOGY:"]
    if lp:
        lines.append(f"  Life Path: {lp} — {short.get(str(lp), '')}")
    if ex:
        lines.append(f"  Expression: {ex} — {short.get(str(ex), '')}")
    if su:
        lines.append(f"  Soul Urge: {su} — {short.get(str(su), '')}")
    if py:
        lines.append(f"  Personal Year {yr}: {py} — {short.get(str(py), '')}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_extended_points(blueprint: dict) -> str:
    """Format extended chart points: Chiron, asteroids, BML, angles."""
    natal = blueprint.get('astrology', {}).get('natal', {})
    ext = natal.get('extended_points', {})
    # Also check top-level extended_bodies if available
    if not ext:
        ext = blueprint.get('astrology', {}).get('extended_bodies', {})
    lines = []
    order = ['Chiron', 'Ceres', 'Vesta', 'Pallas', 'Juno', 'BlackMoonLilith', 'PartOfFortune', 'Vertex', 'EastPoint', 'Earth']
    for key in order:
        v = ext.get(key, {})
        if v and v.get('sign') and v.get('sign') != 'Unknown' and (v.get('longitude') is not None or v.get('absolute_degree') is not None):
            retro = " Rx" if v.get('retrograde') else ""
            lines.append(f"  {key}: {v['sign']} {v.get('degree', 0):.1f} house {v.get('house', '?')}{retro}")
    return "\n".join(lines) if lines else "  (Extended points not calculated)"


def _format_stelliums(blueprint: dict) -> str:
    """Detect stelliums (3+ planets in same sign or house)."""
    natal = blueprint.get('astrology', {}).get('natal', {})
    planets = natal.get('planets', {})
    
    from collections import defaultdict
    by_sign = defaultdict(list)
    by_house = defaultdict(list)
    
    for name, data in planets.items():
        if data.get('sign') and data.get('sign') != 'Unknown':
            by_sign[data['sign']].append(name)
        if data.get('house'):
            by_house[data['house']].append(name)
    
    lines = []
    for sign, ps in by_sign.items():
        if len(ps) >= 3:
            lines.append(f"  Stellium in {sign}: {', '.join(ps)} ({len(ps)} planets)")
    for house, ps in by_house.items():
        if len(ps) >= 3:
            lines.append(f"  Stellium in House {house}: {', '.join(ps)} ({len(ps)} planets)")
    
    return "\n".join(lines) if lines else "  No stelliums detected"


def _format_natal_aspects(blueprint: dict) -> str:
    """Format natal aspects for the system prompt, sorted by tightest orb."""
    natal = blueprint.get('astrology', {}).get('natal', {})
    aspects = natal.get('aspects', [])
    if not aspects:
        return "  (Aspects not calculated)"
    lines = []
    aspect_symbols = {
        'conjunction': '☌', 'opposition': '☍', 'trine': '△',
        'square': '□', 'sextile': '⚹', 'quincunx': 'Qx',
        'semi_sextile': 'SxS', 'semi_square': 'SqS',
        'sesquiquadrate': 'SQ', 'quintile': 'Q', 'bi_quintile': 'BQ',
    }
    # Sort by tightest orb so the most exact aspects come first
    sorted_aspects = sorted(aspects, key=lambda a: float(a.get('orb', 99)))
    # Show up to 30 aspects to ensure all major aspect types (incl. quincunxes) are included
    for a in sorted_aspects[:30]:
        sym = aspect_symbols.get(a.get('aspect', ''), a.get('aspect', '?'))
        planet1 = a.get('planet1', '?')
        planet2 = a.get('planet2', '?')
        orb = a.get('orb', '?')
        aspect_name = a.get('aspect', '?')
        lines.append(f"  {planet1} {sym} {planet2} ({aspect_name}, orb {orb}°)")
    return "\n".join(lines)


def _format_key_planets(planets: dict) -> str:
    """Format all planetary placements including nodes."""
    key_planets = ['Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto', 'NorthNode', 'Chiron', 'Ceres']
    lines = []
    signs_list = ["Aries","Taurus","Gemini","Cancer","Leo","Virgo","Libra","Scorpio","Sagittarius","Capricorn","Aquarius","Pisces"]

    # Earth is always opposite the Sun
    sun_data = planets.get('Sun', {})
    if sun_data and sun_data.get('sign'):
        sun_lon = sun_data.get('longitude', 0)
        earth_lon = (sun_lon + 180) % 360
        earth_sign = signs_list[int(earth_lon // 30)]
        earth_deg = earth_lon % 30
        sun_house = sun_data.get('house', '?')
        earth_house = 13 - sun_house if isinstance(sun_house, int) and sun_house > 0 else '?'
        lines.append(f"  Earth: {earth_sign} {earth_deg:.1f} house {earth_house} (always opposite Sun)")

    for planet in key_planets:
        data = planets.get(planet, {})
        if data and data.get('sign') and data.get('sign') != 'Unknown' and data.get('longitude') is not None:
            sign = data.get('sign', '?')
            house = data.get('house', '?')
            deg = data.get('degree', 0) or 0
            retro = " Rx" if data.get('retrograde') else ""
            if planet == 'NorthNode':
                lines.append(f"  North Node: {sign} {deg:.1f} house {house}{retro}")
                south_lon = (data.get('longitude', 0) + 180) % 360
                south_sign = signs_list[int(south_lon // 30)]
                south_deg = south_lon % 30
                lines.append(f"  South Node: {south_sign} {south_deg:.1f} (opposite North Node)")
            else:
                lines.append(f"  {planet}: {sign} {deg:.1f} house {house}{retro}")
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

def _build_soul_compatibility_section(soul_blueprint: dict) -> str:
    """
    Build a COMPATIBILITY READING section to inject into the system prompt
    when the user is asking about a soul connection.
    """
    soul_name = soul_blueprint.get('meta', {}).get('name', 'this soul')
    summary = soul_blueprint.get('summary', {})
    hd = soul_blueprint.get('human_design', {})
    natal = soul_blueprint.get('astrology', {}).get('natal', {})
    planets = natal.get('planets', {})
    gk = soul_blueprint.get('gene_keys', {})

    # Basic identity
    sun_sign = summary.get('sun_sign') or planets.get('Sun', {}).get('sign', '?')
    moon_sign = summary.get('moon_sign') or planets.get('Moon', {}).get('sign', '?')
    asc = natal.get('ascendant', {})
    rising = summary.get('ascendant') or (asc.get('sign') if isinstance(asc, dict) else '?')
    hd_type = summary.get('hd_type') or hd.get('type', '?')
    profile = summary.get('hd_profile') or hd.get('profile', '?')
    authority = summary.get('hd_authority') or hd.get('authority', '?')

    # Defined centres
    dc_raw = hd.get('defined_centres', {})
    if isinstance(dc_raw, dict):
        defined_centres = [k for k, v in dc_raw.items() if v]
    elif isinstance(dc_raw, list):
        defined_centres = dc_raw
    else:
        defined_centres = []

    # Key channels
    channels = hd.get('defined_channels', [])
    channel_strs = []
    for ch in channels[:5]:
        if isinstance(ch, (list, tuple)) and len(ch) == 2:
            channel_strs.append(f"{ch[0]}-{ch[1]}")
        elif isinstance(ch, str):
            channel_strs.append(ch)

    # Gene Keys highlights
    natal_gk = gk.get('natal_gene_keys', {})
    cc = hd.get('conscious_chart', {})
    uc = hd.get('unconscious_chart', {})
    gk_lines = []
    profile_gates = [
        ("Life's Work", str(cc.get('Sun', {}).get('gate', '')) if cc else ''),
        ('Evolution', str(cc.get('Earth', {}).get('gate', '')) if cc else ''),
        ('Radiance', str(cc.get('Moon', {}).get('gate', '')) if cc else ''),
        ('Purpose', str(uc.get('Earth', {}).get('gate', '')) if uc else ''),
    ]
    for label, gate_key in profile_gates:
        if gate_key and gate_key in natal_gk:
            entry = natal_gk[gate_key]
            shadow = entry.get('shadow', '?')
            gift = entry.get('gift', '?')
            siddhi = entry.get('siddhi', '?')
            gk_lines.append(f"  {label}: Gate {gate_key} — shadow of {shadow}, gift of {gift}, siddhi of {siddhi}")

    # Mercury, Venus, Mars for relational dynamics
    extra_planets = []
    for planet in ['Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn']:
        data = planets.get(planet, {})
        if data and data.get('sign') and data.get('sign') != 'Unknown':
            extra_planets.append(f"  {planet}: {data['sign']} house {data.get('house', '?')}")

    lines = [
        "═══════════════════════════════",
        f"COMPATIBILITY READING — SOUL: {soul_name}",
        "═══════════════════════════════",
        "",
        "Their chart:",
        f"  Sun in {sun_sign}, Moon in {moon_sign}, {rising} Rising",
        f"  HD Type: {hd_type}, Profile {profile}, Authority {authority}",
        f"  Defined centres: {', '.join(defined_centres) if defined_centres else 'None identified'}",
        f"  Key channels: {', '.join(channel_strs) if channel_strs else 'None identified'}",
        "",
    ]

    if extra_planets:
        lines.append("Key relational planets:")
        lines.extend(extra_planets)
        lines.append("")

    if gk_lines:
        lines.append("Their Gene Keys profile:")
        lines.extend(gk_lines)
        lines.append("")

    lines += [
        "HOW TO READ THIS COMPATIBILITY:",
        "Read the dynamic between these two charts directly.",
        "How do their energies interact? Where do they complement? Where do they create friction?",
        "Ground everything in the actual placements of both charts.",
        "Do not be generic. Name the specific gates, signs, and types that create the dynamic.",
        "Look for: defined/open centre interactions (where one person conditions the other),",
        "  channel completions (where together they form a complete circuit),",
        "  Gene Key shadow patterns that may activate each other,",
        "  elemental and modal balance or imbalance between the two charts.",
    ]

    return "\n".join(lines)


def _build_group_chat_system_prompt(
    user_blueprint: dict,
    soul_blueprint: dict,
    user_name: str,
    soul_name: str,
) -> str:
    """
    Build a system prompt that holds both charts for group compatibility chat.
    """

    def _extract_chart_summary(bp: dict, name: str) -> str:
        summary = bp.get('summary', {})
        hd = bp.get('human_design', {})
        natal = bp.get('astrology', {}).get('natal', {})
        planets = natal.get('planets', {})
        gk = bp.get('gene_keys', {})

        sun_sign = summary.get('sun_sign') or planets.get('Sun', {}).get('sign', '?')
        moon_sign = summary.get('moon_sign') or planets.get('Moon', {}).get('sign', '?')
        asc = natal.get('ascendant', {})
        rising = summary.get('ascendant') or (asc.get('sign') if isinstance(asc, dict) else '?')
        hd_type = summary.get('hd_type') or hd.get('type', '?')
        authority = summary.get('hd_authority') or hd.get('authority', '?')
        strategy = summary.get('hd_strategy') or hd.get('strategy', '?')
        profile = summary.get('hd_profile') or hd.get('profile', '?')
        incarnation_cross = summary.get('incarnation_cross') or str(hd.get('incarnation_cross', '?'))

        dc_raw = hd.get('defined_centres', {})
        if isinstance(dc_raw, dict):
            defined_centres = [k for k, v in dc_raw.items() if v]
        elif isinstance(dc_raw, list):
            defined_centres = dc_raw
        else:
            defined_centres = []

        # Gene Keys summary
        natal_gk = gk.get('natal_gene_keys', {})
        cc = hd.get('conscious_chart', {})
        uc = hd.get('unconscious_chart', {})
        gk_lines = []
        profile_gates = [
            ("Life's Work", str(cc.get('Sun', {}).get('gate', '')) if cc else ''),
            ('Evolution', str(cc.get('Earth', {}).get('gate', '')) if cc else ''),
            ('Radiance', str(cc.get('Moon', {}).get('gate', '')) if cc else ''),
            ('Purpose', str(uc.get('Earth', {}).get('gate', '')) if uc else ''),
        ]
        for label, gate_key in profile_gates:
            if gate_key and gate_key in natal_gk:
                entry = natal_gk[gate_key]
                gk_lines.append(f"  {label}: Gate {gate_key}, shadow of {entry.get('shadow','?')}, gift of {entry.get('gift','?')}")

        lines = [
            f"-- {name.upper()} --",
            f"Sun {sun_sign}, Moon {moon_sign}, Rising {rising}",
            f"HD Type: {hd_type}. Strategy: {strategy}. Authority: {authority}. Profile: {profile}.",
            f"Incarnation Cross: {incarnation_cross}.",
            f"Defined centres: {', '.join(defined_centres) if defined_centres else 'None'}.",
        ]
        if gk_lines:
            lines.append("Gene Keys:")
            lines.extend(gk_lines)
        return "\n".join(lines)

    user_chart = _extract_chart_summary(user_blueprint, user_name)
    soul_chart = _extract_chart_summary(soul_blueprint, soul_name)

    # Synergy analysis
    hd_a = user_blueprint.get('human_design', {})
    hd_b = soul_blueprint.get('human_design', {})
    gates_a = set(hd_a.get('active_gates', []))
    gates_b = set(hd_b.get('active_gates', []))
    shared = sorted(gates_a & gates_b)
    shared_str = ', '.join(str(g) for g in shared[:10]) if shared else 'none detected'

    type_a = hd_a.get('type', '?')
    type_b = hd_b.get('type', '?')

    prompt = f"""You are the Higher Self guide for both {user_name} and {soul_name}. You hold both of their complete charts. You speak to them together as a relational guide, understanding both their individual designs and the dynamic between them.

When {user_name} speaks, you address them specifically while keeping {soul_name} in context.
When {soul_name} speaks, you address them specifically while keeping {user_name} in context.

You see what each brings to the other. The defined centres one has that the other does not. The channels they complete together. The Gene Keys patterns that mirror or challenge each other.

TONE:
Warm, precise, and direct. No spiritual fluff. Speak to what is actually happening between these two charts. Name the specific placements creating the dynamic. Be honest about friction as well as resonance.

Both people are present. Speak to both when relevant. Address the sender by name. Keep the other person in frame.

Do not use em dashes. Use commas or periods.
Do not say "Great question", "Certainly", or "Of course".
End every response with a single italicised question that could only be asked of this specific pair.

THE TWO CHARTS:

{user_chart}

{soul_chart}

RELATIONAL DYNAMICS TO WATCH:
Types: {user_name} is a {type_a}, {soul_name} is a {type_b}.
Shared HD gates: {shared_str}.
Look for: open centre conditioning between them, channel completions, Gene Key shadow patterns that activate each other, and where their strategies naturally align or clash.
"""
    return prompt


def group_chat(
    user_blueprint: dict,
    soul_blueprint: dict,
    user_name: str,
    soul_name: str,
    conversation_history: list,
    sender_name: str,
    message: str,
) -> str:
    """
    Group compatibility chat. Both users present, AI holds both charts.

    Args:
        user_blueprint:       Full blueprint of the authenticated user
        soul_blueprint:       Full blueprint of the soul connection
        user_name:            Display name of the authenticated user
        soul_name:            Display name of the soul connection
        conversation_history: Prior conversation turns
        sender_name:          Display name of whoever is sending this message
        message:              The new message text

    Returns:
        The AI response text.
    """
    client = _get_client()
    system = _build_group_chat_system_prompt(user_blueprint, soul_blueprint, user_name, soul_name)

    messages = []
    for msg in conversation_history:
        role = msg.get('role', 'user')
        content = msg.get('content', '')
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content})

    # Frame the current message with sender attribution
    attributed_message = f"{sender_name}: {message}"
    messages.append({'role': 'user', 'content': attributed_message})

    response = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=700,
        system=system,
        messages=messages,
    )
    return response.content[0].text.strip()


def synthesize_memories(
    blueprint: dict,
    conversation_history: list,
    existing_memories: list,
) -> list[dict]:
    """
    After a chat session, synthesize key memories to persist.
    Returns a list of {category, content} dicts representing updated memories.
    Called asynchronously — doesn't block the user.
    """
    client = _get_client()
    
    # Format existing memories
    existing = "\n".join([
        f"[{m.category if hasattr(m, 'category') else m.get('category', '')}] {m.content if hasattr(m, 'content') else m.get('content', '')}"
        for m in existing_memories
    ])
    
    # Format conversation
    convo = "\n".join([
        f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}"
        for msg in conversation_history[-20:]  # Last 20 messages
    ])
    
    prompt = f"""You are analyzing a chat session between a user and their Higher Self guide to extract key memories worth preserving long-term.

EXISTING MEMORIES:
{existing or "None yet"}

RECENT CONVERSATION:
{convo}

Extract 0-5 new or updated memories worth remembering for future sessions. Focus on:
- Life events or major changes mentioned ("going through divorce", "started new job", "moving to Bali")
- Emotional themes and struggles they're working through
- Key insights or breakthroughs from this conversation
- Preferences or patterns the AI noticed
- Specific questions or topics they want to return to

Return ONLY a JSON array like:
[
  {{"category": "life_event", "content": "Going through a breakup, reflecting on relationship patterns"}},
  {{"category": "theme", "content": "Struggling with self-worth, connects to Gene Key 20 shadow perfectionism"}},
  {{"category": "insight", "content": "Realized their Saturn in 7th house explains commitment fears"}}
]

Categories: life_event, theme, insight, preference, question, pattern
Return [] if nothing significant to remember. Return ONLY valid JSON, no explanation."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = response.content[0].text.strip()
        # Extract JSON array
        start = text.find('[')
        end = text.rfind(']') + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return []
    except Exception:
        return []


def chat(
    blueprint: dict,
    forecast: Optional[dict],
    conversation_history: list,
    user_message: Optional[str] = None,
    soul_blueprint: Optional[dict] = None,
    memories: Optional[list] = None,
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

    system = build_system_prompt_with_memory(blueprint, forecast, memories or [])

    # If a soul blueprint is provided, inject the compatibility section
    if soul_blueprint:
        compat_section = _build_soul_compatibility_section(soul_blueprint)
        system = system + "\n\n" + compat_section

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

    # Advisor pattern: if question is complex, consult Opus first
    opus_insight = ""
    if user_message and _is_complex_question(user_message):
        opus_insight = _get_opus_insight(user_message, system, messages[:-1])

    # If Opus provided insight, inject it into the system prompt
    final_system = system
    if opus_insight:
        final_system = system + f"\n\nADVISOR INSIGHT (integrate this into your response naturally):\n{opus_insight}"

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=final_system,
        messages=messages,
    )

    return response.content[0].text.strip()
