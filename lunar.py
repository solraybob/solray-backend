"""
lunar.py — Lunar Phase Detection for Solray AI

Detects upcoming (or just-passed) New Moon and Full Moon events within a
configurable window, and returns personalised meaning based on the user's
natal chart house placements.
"""

import swisseph as swe
from datetime import datetime
from typing import Optional, Dict, Any


SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]

HOUSE_MEANINGS = {
    1:  "identity and self-presentation — a new chapter in how you show up",
    2:  "values and finances — what you own and what owns you",
    3:  "communication, siblings, and local environment",
    4:  "home, roots, and family — the private self beneath everything",
    5:  "creativity, romance, and play",
    6:  "health, daily routines, and service",
    7:  "partnerships and relationships — mirrors of the self",
    8:  "transformation, shared resources, and deep change",
    9:  "philosophy, travel, and higher beliefs",
    10: "career, public reputation, and life direction",
    11: "community, friendships, and hopes",
    12: "solitude, hidden depths, and spiritual dissolution",
}

NEW_MOON_HOUSE_NOTES = {
    1:  "Plant intentions around identity. How you meet the world is being seeded.",
    2:  "New beginnings in your resources or relationship with money. What do you truly value?",
    3:  "Fresh conversations are opening. Say what you've been holding back.",
    4:  "Something shifts at the root. Your private world is being reset.",
    5:  "Creative and romantic energy wants to be born. Make room to play.",
    6:  "A new chapter in how you care for your body and structure your days.",
    7:  "An important relationship is entering a new phase. Who are you in partnership?",
    8:  "Deep transformation is seeded. Something must be released to be reborn.",
    9:  "New beliefs, new horizons. An adventure of the mind or the road begins.",
    10: "Your public path is shifting. New intentions around work or legacy.",
    11: "New alliances and dreams. Your community is changing.",
    12: "A quiet, inward seeding. Dreams and solitude hold the answers now.",
}

FULL_MOON_HOUSE_NOTES = {
    1:  "Something about who you are comes into the light. Others see you clearly now.",
    2:  "A financial or values matter reaches its peak. What's been building is now visible.",
    3:  "A conversation or idea reaches culmination. Speak your truth clearly.",
    4:  "Something in the home or family reaches a peak. Tend to your roots.",
    5:  "Creative or romantic energy peaks. Let yourself be fully expressed.",
    6:  "Your body and routines are asking for attention. What needs to change?",
    7:  "A relationship reaches a turning point. Something must be named between you.",
    8:  "A powerful release is available. Let what no longer serves you go.",
    9:  "A belief or journey reaches completion. What have you learned?",
    10: "Your work or reputation reaches a peak moment. Be seen.",
    11: "Your community or a long-held dream comes into focus.",
    12: "Something hidden surfaces. Give yourself time in solitude.",
}


def _find_next_phase(jd_start: float, target_angle: float) -> Optional[float]:
    """
    Find the next Julian Day when the Moon-Sun angle equals target_angle (0=new, 180=full).
    Uses iterative narrowing. Returns None if not found within ~30 days.
    """
    jd = jd_start
    for _ in range(400):
        sun = swe.calc_ut(jd, swe.SUN)[0][0]
        moon = swe.calc_ut(jd, swe.MOON)[0][0]
        angle = (moon - sun) % 360
        diff = angle - target_angle
        if diff > 180:
            diff -= 360
        if abs(diff) < 0.3:
            return jd
        step = max(0.05, abs(diff) / 14)
        jd += step
    return None


def _find_prev_phase(jd_start: float, target_angle: float) -> Optional[float]:
    """
    Walk BACKWARDS from jd_start to find the most recent occurrence of target_angle.
    """
    jd = jd_start
    for _ in range(400):
        sun = swe.calc_ut(jd, swe.SUN)[0][0]
        moon = swe.calc_ut(jd, swe.MOON)[0][0]
        angle = (moon - sun) % 360
        diff = angle - target_angle
        if diff > 180:
            diff -= 360
        if abs(diff) < 0.3:
            return jd
        step = max(0.05, abs(diff) / 14)
        jd -= step
    return None


def _moon_sign_at(jd: float) -> tuple[str, float]:
    moon_lon = swe.calc_ut(jd, swe.MOON)[0][0]
    sign = SIGNS[int(moon_lon // 30)]
    degree = moon_lon % 30
    return sign, degree, moon_lon


def _natal_house_for_longitude(moon_lon: float, house_cusps: list) -> int:
    """Return the natal house (1-12) that contains moon_lon.
    house_cusps items can be dicts with longitude key or plain floats.
    """
    if not house_cusps or len(house_cusps) < 12:
        return 1

    def _lon(c):
        return float(c["longitude"]) if isinstance(c, dict) else float(c)

    for i in range(12):
        cusp_start = _lon(house_cusps[i])
        cusp_end = _lon(house_cusps[(i + 1) % 12])
        if cusp_end > cusp_start:
            if cusp_start <= moon_lon < cusp_end:
                return i + 1
        else:
            if moon_lon >= cusp_start or moon_lon < cusp_end:
                return i + 1
    return 1


def get_upcoming_lunar_event(natal_chart: dict, days_window: int = 3) -> Optional[Dict[str, Any]]:
    """
    Check if a New or Full Moon is within days_window days (before or after today).

    Args:
        natal_chart: The natal chart dict (from astrology.get_natal_chart).
                     Needs 'house_cusps' key for personalised house meaning.
        days_window: How many days on each side of today to check (default 3).

    Returns:
        Dict with event details + personalised note, or None if no event is imminent.
    """
    now = datetime.utcnow()
    jd_now = swe.julday(now.year, now.month, now.day, now.hour + now.minute / 60.0)

    # Find next and previous for both new + full moons
    next_new = _find_next_phase(jd_now, 0)
    next_full = _find_next_phase(jd_now, 180)
    prev_new = _find_prev_phase(jd_now - 0.1, 0)
    prev_full = _find_prev_phase(jd_now - 0.1, 180)

    candidates = []
    for jd, event_type in [
        (next_new, "New Moon"),
        (next_full, "Full Moon"),
        (prev_new, "New Moon"),
        (prev_full, "Full Moon"),
    ]:
        if jd is None:
            continue
        days_diff = jd - jd_now  # positive = future, negative = past
        if abs(days_diff) <= days_window:
            candidates.append((jd, event_type, days_diff))

    if not candidates:
        return None

    # Pick the closest event to now
    candidates.sort(key=lambda x: abs(x[2]))
    closest_jd, event_type, days_until = candidates[0]

    # Moon sign + longitude at the event
    sign, degree, moon_lon = _moon_sign_at(closest_jd)

    # Which natal house does this fall in?
    house_cusps = natal_chart.get("house_cusps", [])
    natal_house = _natal_house_for_longitude(moon_lon, house_cusps)

    # Build personalised note
    notes_map = NEW_MOON_HOUSE_NOTES if event_type == "New Moon" else FULL_MOON_HOUSE_NOTES
    personal_note = notes_map.get(natal_house, "A powerful lunar moment is illuminating something important.")

    # Format event date
    y, m, d, _ = swe.revjul(closest_jd)
    event_date = f"{int(y)}-{int(m):02d}-{int(d):02d}"

    return {
        "type": event_type,
        "sign": sign,
        "degree": round(degree, 1),
        "house": natal_house,
        "house_meaning": HOUSE_MEANINGS.get(natal_house, ""),
        "date": event_date,
        "days_until": round(days_until, 2),
        "is_today": abs(days_until) < 0.5,
        "note": personal_note,
    }
