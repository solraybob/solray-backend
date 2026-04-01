"""
long_range.py — Long-Range Transit Calculations for Solray AI

Identifies major astrological cycles the user is currently in:
- Saturn Return (transit Saturn conjunct natal Saturn, orb 10°)
- Jupiter Return (transit Jupiter conjunct natal Jupiter, orb 8°)
- Nodal Return (transit North Node conjunct natal North Node, orb 8°)
- Outer planet transits over natal Sun, Moon, Ascendant (conjunctions only)

All calculations use pyswisseph with Moshier fallback (no SE1 files needed).
"""

import swisseph as swe
from datetime import date, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_planet_lon(jd: float, planet_id: int) -> tuple:
    """Return (longitude, speed) for a planet at julian day."""
    swe.set_ephe_path('')
    result, _ = swe.calc_ut(jd, planet_id, swe.FLG_MOSEPH | swe.FLG_SPEED)
    return result[0], result[3]


def _angular_diff(lon1: float, lon2: float) -> float:
    """Shortest angular distance between two ecliptic longitudes [0, 180]."""
    diff = abs(lon1 - lon2) % 360
    if diff > 180:
        diff = 360 - diff
    return diff


def _jd_to_date(jd: float) -> date:
    """Convert julian day to Python date."""
    year, month, day, _ = swe.revjul(jd)
    return date(year, month, day)


def _date_to_jd(d: date) -> float:
    """Convert Python date to julian day (noon)."""
    return swe.julday(d.year, d.month, d.day, 12.0)


def _find_transit_start(natal_lon: float, planet_id: int, current_jd: float, orb: float) -> float:
    """
    Walk backwards from current_jd to find when the transit entered orb.
    Handles retrograde: only considers a break "real" if outside orb >= 60 days.
    Returns JD of approximate transit start.
    """
    step = 3  # 3-day steps
    jd = current_jd
    outside_run_days = 0
    last_inside_jd = current_jd
    first_outside_jd = None  # start of the current outside block (going backward)

    max_steps = int(5 * 365 / step)

    for _ in range(max_steps):
        jd -= step
        lon, _ = _get_planet_lon(jd, planet_id)
        diff = _angular_diff(lon, natal_lon)

        if diff > orb:
            if outside_run_days == 0:
                first_outside_jd = jd
            outside_run_days += step

            if outside_run_days >= 60:
                # Real pre-transit period found.
                # The transit started near last_inside_jd.
                # Refine: walk forward day by day from first_outside_jd to find exact entry.
                search_start = min(jd, first_outside_jd) if first_outside_jd else jd
                for i in range(90):
                    check_jd = search_start + i
                    lon2, _ = _get_planet_lon(check_jd, planet_id)
                    if _angular_diff(lon2, natal_lon) <= orb:
                        return check_jd
                return last_inside_jd
        else:
            # Back inside orb — reset outside counter (brief retrograde excursion)
            outside_run_days = 0
            first_outside_jd = None
            last_inside_jd = jd

    # Transit has been active for the entire 5-year search window
    return jd


def _find_transit_end(natal_lon: float, planet_id: int, current_jd: float, orb: float) -> float:
    """
    Walk forward from current_jd to find when the transit finally exits orb.
    Handles retrograde: only considers a break "real" if outside orb >= 60 days.
    Returns JD of approximate transit end.
    """
    step = 3  # 3-day steps
    jd = current_jd
    outside_run_days = 0
    last_inside_jd = current_jd
    first_outside_jd = None

    max_steps = int(5 * 365 / step)

    for _ in range(max_steps):
        jd += step
        lon, _ = _get_planet_lon(jd, planet_id)
        diff = _angular_diff(lon, natal_lon)

        if diff > orb:
            if outside_run_days == 0:
                first_outside_jd = jd
            outside_run_days += step

            if outside_run_days >= 60:
                # Real post-transit period found.
                # The transit ended near last_inside_jd.
                # Refine: walk backward from first_outside_jd to find exact exit.
                search_end = first_outside_jd if first_outside_jd else jd
                for i in range(90):
                    check_jd = search_end - i
                    lon2, _ = _get_planet_lon(check_jd, planet_id)
                    if _angular_diff(lon2, natal_lon) <= orb:
                        return check_jd
                return last_inside_jd
        else:
            # Back inside orb — reset (retrograde re-entry)
            outside_run_days = 0
            first_outside_jd = None
            last_inside_jd = jd

    # Transit still active 5 years out
    return jd


def _find_transit_peak(natal_lon: float, planet_id: int, start_jd: float, end_jd: float) -> float:
    """
    Find the date of closest approach (minimum orb) within the transit window.
    Returns JD of peak.
    """
    best_jd = start_jd
    best_orb = float('inf')

    # Coarse scan: 7-day steps
    step = 7
    jd = start_jd
    while jd <= end_jd:
        lon, _ = _get_planet_lon(jd, planet_id)
        diff = _angular_diff(lon, natal_lon)
        if diff < best_orb:
            best_orb = diff
            best_jd = jd
        jd += step

    # Refine: 1-day steps around best
    refine_start = max(start_jd, best_jd - 30)
    refine_end = min(end_jd, best_jd + 30)
    jd = refine_start
    while jd <= refine_end:
        lon, _ = _get_planet_lon(jd, planet_id)
        diff = _angular_diff(lon, natal_lon)
        if diff < best_orb:
            best_orb = diff
            best_jd = jd
        jd += 1.0

    return best_jd


def _build_transit_entry(
    planet_name: str,
    planet_id: int,
    natal_point: str,
    natal_lon: float,
    current_orb: float,
    current_lon: float,
    today_jd: float,
    orb_threshold: float,
    title: str,
) -> dict:
    """Build a transit dict with start/peak/end dates. summary=None (filled by AI later)."""

    start_jd = _find_transit_start(natal_lon, planet_id, today_jd, orb_threshold)
    end_jd   = _find_transit_end(natal_lon, planet_id, today_jd, orb_threshold)
    peak_jd  = _find_transit_peak(natal_lon, planet_id, start_jd, end_jd)

    # Phase: applying or separating?
    tomorrow_lon, _ = _get_planet_lon(today_jd + 1, planet_id)
    phase = 'applying' if _angular_diff(tomorrow_lon, natal_lon) < current_orb else 'separating'

    return {
        'transit_planet': planet_name,
        'natal_point':    natal_point,
        'aspect':         'conjunction',
        'orb':            round(current_orb, 2),
        'phase':          phase,
        'started':        _jd_to_date(start_jd).isoformat(),
        'peak':           _jd_to_date(peak_jd).isoformat(),
        'ends':           _jd_to_date(end_jd).isoformat(),
        'title':          title,
        'summary':        None,  # filled by AI
    }


# ---------------------------------------------------------------------------
# Main calculation
# ---------------------------------------------------------------------------

def calc_long_range_transits(blueprint: dict, today: Optional[date] = None) -> list:
    """
    Calculate all active major long-range transits for a given blueprint.

    Checks:
      - Saturn Return  (Saturn → natal Saturn, orb 10°)
      - Jupiter Return (Jupiter → natal Jupiter, orb 8°)
      - Nodal Return   (NorthNode → natal NorthNode, orb 8°)
      - Outer planets (Pluto/Neptune/Uranus, orb 5°) and (Saturn/Jupiter, orb 8°)
        over natal Sun, Moon, Ascendant

    Returns list of active transit dicts sorted by orb (tightest first).
    """
    if today is None:
        today = date.today()

    today_jd = _date_to_jd(today)

    natal_planets = blueprint.get('astrology', {}).get('natal', {}).get('planets', {})
    natal_asc     = blueprint.get('astrology', {}).get('natal', {}).get('ascendant', {})

    active = []

    # ------------------------------------------------------------------
    # 1. Saturn Return — transit Saturn conjunct natal Saturn, orb 10°
    # ------------------------------------------------------------------
    natal_saturn_lon = natal_planets.get('Saturn', {}).get('longitude')
    if natal_saturn_lon is not None:
        cur_lon, _ = _get_planet_lon(today_jd, swe.SATURN)
        orb = _angular_diff(cur_lon, natal_saturn_lon)
        if orb <= 10.0:
            active.append(_build_transit_entry(
                planet_name='Saturn', planet_id=swe.SATURN,
                natal_point='Saturn', natal_lon=natal_saturn_lon,
                current_orb=orb, current_lon=cur_lon,
                today_jd=today_jd, orb_threshold=10.0,
                title='Your Saturn Return',
            ))

    # ------------------------------------------------------------------
    # 2. Jupiter Return — transit Jupiter conjunct natal Jupiter, orb 8°
    # ------------------------------------------------------------------
    natal_jupiter_lon = natal_planets.get('Jupiter', {}).get('longitude')
    if natal_jupiter_lon is not None:
        cur_lon, _ = _get_planet_lon(today_jd, swe.JUPITER)
        orb = _angular_diff(cur_lon, natal_jupiter_lon)
        if orb <= 8.0:
            active.append(_build_transit_entry(
                planet_name='Jupiter', planet_id=swe.JUPITER,
                natal_point='Jupiter', natal_lon=natal_jupiter_lon,
                current_orb=orb, current_lon=cur_lon,
                today_jd=today_jd, orb_threshold=8.0,
                title='Your Jupiter Return',
            ))

    # ------------------------------------------------------------------
    # 3. Nodal Return — transit NorthNode conjunct natal NorthNode, orb 8°
    # ------------------------------------------------------------------
    natal_node_lon = natal_planets.get('NorthNode', {}).get('longitude')
    if natal_node_lon is not None:
        cur_lon, _ = _get_planet_lon(today_jd, swe.TRUE_NODE)
        orb = _angular_diff(cur_lon, natal_node_lon)
        if orb <= 8.0:
            active.append(_build_transit_entry(
                planet_name='NorthNode', planet_id=swe.TRUE_NODE,
                natal_point='NorthNode', natal_lon=natal_node_lon,
                current_orb=orb, current_lon=cur_lon,
                today_jd=today_jd, orb_threshold=8.0,
                title='Nodal Return',
            ))

    # ------------------------------------------------------------------
    # 4. Outer planet transits over natal Sun, Moon, Ascendant
    # ------------------------------------------------------------------
    outer_planets = [
        ('Pluto',   swe.PLUTO,   5.0),
        ('Neptune', swe.NEPTUNE, 5.0),
        ('Uranus',  swe.URANUS,  5.0),
        ('Saturn',  swe.SATURN,  8.0),
        ('Jupiter', swe.JUPITER, 8.0),
    ]

    # Key natal points to watch
    natal_points = []
    sun_lon = natal_planets.get('Sun', {}).get('longitude')
    if sun_lon is not None:
        natal_points.append(('Sun', sun_lon))
    moon_lon = natal_planets.get('Moon', {}).get('longitude')
    if moon_lon is not None:
        natal_points.append(('Moon', moon_lon))
    asc_lon = natal_asc.get('longitude')
    if asc_lon is not None:
        natal_points.append(('Ascendant', asc_lon))
    # Chiron — wounds and healing, significant when outer planets activate it
    chiron_lon = natal_planets.get('Chiron', {}).get('longitude')
    if chiron_lon is not None:
        natal_points.append(('Chiron', chiron_lon))

    # Pre-calculate current positions for the 5 planets
    planet_positions = {}
    for planet_name, planet_id, _ in outer_planets:
        cur_lon, _ = _get_planet_lon(today_jd, planet_id)
        planet_positions[planet_name] = (planet_id, cur_lon)

    for transit_planet_name, orb_thresh in [
        ('Pluto', 5.0), ('Neptune', 5.0), ('Uranus', 5.0),
        ('Saturn', 8.0), ('Jupiter', 8.0),
    ]:
        planet_id, cur_lon = planet_positions[transit_planet_name]

        for natal_point_name, natal_lon in natal_points:
            # Skip: already captured as return transits
            if transit_planet_name == 'Saturn' and natal_point_name == 'Saturn':
                continue
            if transit_planet_name == 'Jupiter' and natal_point_name == 'Jupiter':
                continue

            orb = _angular_diff(cur_lon, natal_lon)
            if orb <= orb_thresh:
                active.append(_build_transit_entry(
                    planet_name=transit_planet_name,
                    planet_id=planet_id,
                    natal_point=natal_point_name,
                    natal_lon=natal_lon,
                    current_orb=orb,
                    current_lon=cur_lon,
                    today_jd=today_jd,
                    orb_threshold=orb_thresh,
                    title=f'{transit_planet_name} meets your {natal_point_name}',
                ))

    # Sort by orb (tightest first) — most significant cycles first
    active.sort(key=lambda x: x['orb'])
    return active


# ---------------------------------------------------------------------------
# Upcoming cycles (entering orb within the next N days)
# ---------------------------------------------------------------------------

def get_upcoming_cycles(blueprint: dict, today: Optional[date] = None, days_ahead: int = 500) -> list:
    """
    Scan the next `days_ahead` days for major cycles that are NOT yet in orb
    but will enter orb within that window.

    Checks the same transits as calc_long_range_transits:
      - Saturn Return  (orb 10°)
      - Jupiter Return (orb 8°)
      - Nodal Return   (orb 8°)
      - Outer planets (Pluto/Neptune/Uranus orb 5°, Saturn/Jupiter orb 8°)
        conjunct natal Sun / Moon / Ascendant

    Returns a list of upcoming transit dicts sorted by days_until_orb (soonest first).
    Returns at most 2 entries.
    """
    if today is None:
        today = date.today()

    today_jd = _date_to_jd(today)

    natal_planets = blueprint.get('astrology', {}).get('natal', {}).get('planets', {})
    natal_asc     = blueprint.get('astrology', {}).get('natal', {}).get('ascendant', {})

    # Build the candidate list: (planet_name, planet_id, natal_point_name, natal_lon, orb_threshold, title)
    candidates = []

    natal_saturn_lon = natal_planets.get('Saturn', {}).get('longitude')
    if natal_saturn_lon is not None:
        candidates.append(('Saturn', swe.SATURN, 'Saturn', natal_saturn_lon, 10.0, 'Your Saturn Return'))

    natal_jupiter_lon = natal_planets.get('Jupiter', {}).get('longitude')
    if natal_jupiter_lon is not None:
        candidates.append(('Jupiter', swe.JUPITER, 'Jupiter', natal_jupiter_lon, 8.0, 'Your Jupiter Return'))

    natal_node_lon = natal_planets.get('NorthNode', {}).get('longitude')
    if natal_node_lon is not None:
        candidates.append(('NorthNode', swe.TRUE_NODE, 'NorthNode', natal_node_lon, 8.0, 'Nodal Return'))

    # Outer planet × natal point combos
    natal_points_outer = []
    sun_lon = natal_planets.get('Sun', {}).get('longitude')
    if sun_lon is not None:
        natal_points_outer.append(('Sun', sun_lon))
    moon_lon = natal_planets.get('Moon', {}).get('longitude')
    if moon_lon is not None:
        natal_points_outer.append(('Moon', moon_lon))
    asc_lon = natal_asc.get('longitude')
    if asc_lon is not None:
        natal_points_outer.append(('Ascendant', asc_lon))

    outer_combos = [
        ('Pluto',   swe.PLUTO,   5.0),
        ('Neptune', swe.NEPTUNE, 5.0),
        ('Uranus',  swe.URANUS,  5.0),
        ('Saturn',  swe.SATURN,  8.0),
        ('Jupiter', swe.JUPITER, 8.0),
    ]

    for transit_planet_name, planet_id, orb_thresh in outer_combos:
        for natal_point_name, natal_lon in natal_points_outer:
            candidates.append((
                transit_planet_name, planet_id,
                natal_point_name, natal_lon,
                orb_thresh,
                f'{transit_planet_name} meets your {natal_point_name}',
            ))

    upcoming = []

    for planet_name, planet_id, natal_point, natal_lon, orb_thresh, title in candidates:
        # Check: is it currently in orb? If yes, skip (it's active).
        cur_lon, _ = _get_planet_lon(today_jd, planet_id)
        if _angular_diff(cur_lon, natal_lon) <= orb_thresh:
            continue  # already active

        # Scan ahead in 3-day steps to find when it enters orb
        entry_jd = None
        step = 3
        for i in range(0, days_ahead, step):
            check_jd = today_jd + i
            lon, _ = _get_planet_lon(check_jd, planet_id)
            if _angular_diff(lon, natal_lon) <= orb_thresh:
                # Refine: walk back day by day to find the exact entry day
                for j in range(step + 1):
                    refine_jd = check_jd - j
                    lon2, _ = _get_planet_lon(refine_jd, planet_id)
                    if _angular_diff(lon2, natal_lon) > orb_thresh:
                        entry_jd = refine_jd + 1  # first day inside orb
                        break
                if entry_jd is None:
                    entry_jd = check_jd
                break

        if entry_jd is None:
            continue  # doesn't enter orb within days_ahead

        days_until = round(entry_jd - today_jd)
        if days_until < 0:
            continue

        enters_orb_date = _jd_to_date(entry_jd)

        upcoming.append({
            'transit_planet': planet_name,
            'natal_point':    natal_point,
            'aspect':         'conjunction',
            'title':          title,
            'status':         'upcoming',
            'days_until_orb': days_until,
            'enters_orb':     enters_orb_date.isoformat(),
            'summary':        None,  # filled by AI caller if needed
        })

    # Sort by soonest first, return top 2
    upcoming.sort(key=lambda x: x['days_until_orb'])
    return upcoming[:2]
