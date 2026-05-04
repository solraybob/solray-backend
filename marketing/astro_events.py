"""
marketing/astro_events.py — upcoming sky events that mark marketing windows.

For an astrology brand, the next 60 days of the sky IS the editorial
calendar. Mercury retrograde isn't a meme; it's a real weeklong
content moment. New moon in Aries is a real launch window.

This module returns a sorted list of dated events for the next N days:

  retrograde stations (Mercury, Venus, Mars, Jupiter, Saturn, Uranus,
                       Neptune, Pluto)
  ingresses (a planet entering a new sign)
  lunar phases (new moon, first quarter, full moon, last quarter)

Implementation notes:
  Uses pyswisseph (already a Solray dependency). Computes in UT, returns
  ISO 8601 timestamps. Granularity is 6h for retrograde scans (good
  enough for marketing planning) and 1h for ingress / lunar scans.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict

import swisseph as swe

log = logging.getLogger("solray.marketing.astro_events")

# Bodies we track for retrograde + ingress.
_PLANETS = {
    "Mercury": swe.MERCURY,
    "Venus":   swe.VENUS,
    "Mars":    swe.MARS,
    "Jupiter": swe.JUPITER,
    "Saturn":  swe.SATURN,
    "Uranus":  swe.URANUS,
    "Neptune": swe.NEPTUNE,
    "Pluto":   swe.PLUTO,
}

_SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]


def _datetime_to_jd(dt: datetime) -> float:
    return swe.julday(dt.year, dt.month, dt.day, dt.hour + dt.minute / 60.0)


def _jd_to_datetime(jd: float) -> datetime:
    y, m, d, h = swe.revjul(jd)
    hour = int(h)
    minute = int(round((h - hour) * 60))
    if minute >= 60:
        minute = 0
        hour += 1
    return datetime(y, m, d, hour, minute, tzinfo=timezone.utc)


def _planet_speed(jd: float, body: int) -> float:
    """Return ecliptic longitudinal speed in deg/day. Negative = retrograde."""
    res, _ = swe.calc_ut(jd, body, swe.FLG_SPEED)
    return res[3]  # speed in longitude


def _planet_lon(jd: float, body: int) -> float:
    res, _ = swe.calc_ut(jd, body, 0)
    return res[0]


def _sign_index(lon: float) -> int:
    return int(lon // 30) % 12


def upcoming_events(days: int = 60) -> List[Dict]:
    """Return all marketing-relevant sky events from now until now + days.

    Returns a list of dicts:
      { kind, label, happens_at (ISO), body? }
    sorted ascending by happens_at.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = now + timedelta(days=days)

    events: List[Dict] = []
    try:
        events.extend(_retrograde_stations(now, end))
    except Exception as e:
        log.warning("retrograde scan failed: %s", e)
    try:
        events.extend(_ingresses(now, end))
    except Exception as e:
        log.warning("ingress scan failed: %s", e)
    try:
        events.extend(_lunar_phases(now, end))
    except Exception as e:
        log.warning("lunar scan failed: %s", e)

    events.sort(key=lambda e: e["happens_at"])
    return events


def _retrograde_stations(start: datetime, end: datetime) -> List[Dict]:
    """Find sign changes in planetary speed (zero crossings) for each planet
    within [start, end). Each crossing is either a station retrograde (speed
    going from + to -) or station direct (- to +).
    Step at 6h granularity, then refine with bisection to 5min.
    """
    out: List[Dict] = []
    step = timedelta(hours=6)

    for name, body in _PLANETS.items():
        # Skip Pluto for retrograde noise: long retrograde spans, low
        # marketing density. We still include it but bracket it lightly.
        prev_speed = _planet_speed(_datetime_to_jd(start), body)
        cur = start + step
        while cur <= end:
            speed = _planet_speed(_datetime_to_jd(cur), body)
            if (prev_speed >= 0 and speed < 0) or (prev_speed < 0 and speed >= 0):
                # Bisect to a tighter timestamp.
                station = _bisect_zero_speed(cur - step, cur, body)
                out.append({
                    "kind": "station_retrograde" if speed < 0 else "station_direct",
                    "label": f"{name} stations {'retrograde' if speed < 0 else 'direct'}",
                    "happens_at": station.isoformat(),
                    "body": name,
                })
            prev_speed = speed
            cur += step
    return out


def _bisect_zero_speed(t_lo: datetime, t_hi: datetime, body: int, iterations: int = 14) -> datetime:
    for _ in range(iterations):
        mid = t_lo + (t_hi - t_lo) / 2
        s_lo = _planet_speed(_datetime_to_jd(t_lo), body)
        s_mid = _planet_speed(_datetime_to_jd(mid), body)
        if (s_lo > 0) == (s_mid > 0):
            t_lo = mid
        else:
            t_hi = mid
    return t_lo + (t_hi - t_lo) / 2


def _ingresses(start: datetime, end: datetime) -> List[Dict]:
    """Find sign changes for the visible planets within [start, end)."""
    out: List[Dict] = []
    step = timedelta(hours=6)

    for name, body in _PLANETS.items():
        prev_sign = _sign_index(_planet_lon(_datetime_to_jd(start), body))
        cur = start + step
        while cur <= end:
            lon = _planet_lon(_datetime_to_jd(cur), body)
            sign = _sign_index(lon)
            if sign != prev_sign:
                ingress_t = _bisect_sign_change(cur - step, cur, body, prev_sign)
                out.append({
                    "kind": "ingress",
                    "label": f"{name} enters {_SIGNS[sign]}",
                    "happens_at": ingress_t.isoformat(),
                    "body": name,
                })
                prev_sign = sign
            cur += step
    return out


def _bisect_sign_change(t_lo: datetime, t_hi: datetime, body: int, sign_lo: int, iterations: int = 14) -> datetime:
    for _ in range(iterations):
        mid = t_lo + (t_hi - t_lo) / 2
        s = _sign_index(_planet_lon(_datetime_to_jd(mid), body))
        if s == sign_lo:
            t_lo = mid
        else:
            t_hi = mid
    return t_lo + (t_hi - t_lo) / 2


def _lunar_phases(start: datetime, end: datetime) -> List[Dict]:
    """Find new moon, first quarter, full moon, last quarter within
    [start, end). The moon-sun angle (mod 360) increases roughly 12 deg
    per day. We detect a crossing of each quarter target by tracking
    which 90-deg quadrant the angle is in and noticing when it advances.
    """
    out: List[Dict] = []
    step = timedelta(hours=1)

    quarter_names = ["New Moon", "First Quarter", "Full Moon", "Last Quarter"]

    def angle(jd: float) -> float:
        sun = _planet_lon(jd, swe.SUN)
        moon = _planet_lon(jd, swe.MOON)
        return (moon - sun) % 360

    def quadrant(a: float) -> int:
        return int(a // 90) % 4

    a_prev = angle(_datetime_to_jd(start))
    q_prev = quadrant(a_prev)
    cur = start + step
    while cur <= end:
        a = angle(_datetime_to_jd(cur))
        q = quadrant(a)
        if q != q_prev:
            # We just entered quadrant q. The crossing happened between
            # cur-step and cur. Bisect down to a tighter timestamp.
            crossing_t = _bisect_lunar_quadrant(cur - step, cur, q_prev, q)
            out.append({
                "kind": "lunar_phase",
                "label": quarter_names[q],
                "happens_at": crossing_t.isoformat(),
                "body": "Moon",
            })
            q_prev = q
        a_prev = a
        cur += step
    return out


def _bisect_lunar_quadrant(t_lo: datetime, t_hi: datetime, q_lo: int, q_hi: int, iterations: int = 10) -> datetime:
    def quadrant_at(t: datetime) -> int:
        sun = _planet_lon(_datetime_to_jd(t), swe.SUN)
        moon = _planet_lon(_datetime_to_jd(t), swe.MOON)
        return int(((moon - sun) % 360) // 90) % 4

    for _ in range(iterations):
        mid = t_lo + (t_hi - t_lo) / 2
        if quadrant_at(mid) == q_lo:
            t_lo = mid
        else:
            t_hi = mid
    return t_lo + (t_hi - t_lo) / 2
