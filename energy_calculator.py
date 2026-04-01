"""
energy_calculator.py — Deterministic Energy Score Calculator for Solray AI

Calculates 4 energy scores (mental, emotional, physical, intuitive) from 1-10
based on the user's transit aspects to their natal chart.

Scores are fully deterministic and mathematical — not AI-estimated.
Same input always produces the same output.
"""

from typing import List, Dict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLANET_INFLUENCE: Dict[str, Dict[str, float]] = {
    'Sun':       {'mental': 0.6, 'emotional': 0.3, 'physical': 0.8, 'intuitive': 0.2},
    'Moon':      {'mental': 0.2, 'emotional': 1.0, 'physical': 0.2, 'intuitive': 0.7},
    'Mercury':   {'mental': 1.0, 'emotional': 0.1, 'physical': 0.1, 'intuitive': 0.3},
    'Venus':     {'mental': 0.2, 'emotional': 0.8, 'physical': 0.3, 'intuitive': 0.4},
    'Mars':      {'mental': 0.4, 'emotional': 0.3, 'physical': 1.0, 'intuitive': 0.2},
    'Jupiter':   {'mental': 0.5, 'emotional': 0.6, 'physical': 0.5, 'intuitive': 0.5},
    'Saturn':    {'mental': 0.6, 'emotional': 0.2, 'physical': 0.5, 'intuitive': 0.1},
    'Uranus':    {'mental': 0.8, 'emotional': 0.3, 'physical': 0.2, 'intuitive': 0.6},
    'Neptune':   {'mental': 0.1, 'emotional': 0.7, 'physical': 0.1, 'intuitive': 1.0},
    'Pluto':     {'mental': 0.4, 'emotional': 0.8, 'physical': 0.3, 'intuitive': 0.6},
    'Chiron':    {'mental': 0.3, 'emotional': 0.7, 'physical': 0.4, 'intuitive': 0.6},
    'NorthNode': {'mental': 0.3, 'emotional': 0.4, 'physical': 0.2, 'intuitive': 0.5},
}

ASPECT_MODIFIER: Dict[str, float] = {
    'conjunction':    0.0,   # handled separately via benefic/malefic logic
    'trine':          1.0,   # strongly positive
    'sextile':        0.6,   # positive
    'opposition':    -0.8,   # draining/tension
    'square':        -1.0,   # strongly draining
    'quincunx':      -0.4,   # mildly draining
    'semi_sextile':   0.3,   # mildly positive
    'semi_square':   -0.5,   # mildly draining
    'sesquiquadrate':-0.6,   # moderately draining
    'quintile':       0.4,   # creative/positive
    'bi_quintile':    0.4,   # creative/positive
}

BENEFICS = {'Sun', 'Venus', 'Jupiter', 'Moon'}
MALEFICS = {'Saturn', 'Mars', 'Pluto', 'Uranus'}

DIMENSIONS = ('mental', 'emotional', 'physical', 'intuitive')
BASE_SCORE = 6.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_max_orb(aspect_name: str) -> float:
    """Return max orb for aspect type."""
    ORB_MAP = {
        'conjunction':    8,
        'opposition':     8,
        'trine':          7,
        'square':         7,
        'sextile':        6,
        'quincunx':       3,
        'semi_sextile':   2,
        'semi_square':    2,
        'sesquiquadrate': 2,
        'quintile':       2,
        'bi_quintile':    2,
    }
    return float(ORB_MAP.get(aspect_name, 5))


def _conjunction_modifier(transit_planet: str) -> float:
    """
    Determine the effective modifier for a conjunction aspect.
    Benefics boost, malefics drain, others are mildly positive.
    """
    if transit_planet in BENEFICS:
        return 0.8
    elif transit_planet in MALEFICS:
        return -0.6
    else:
        return 0.2


# ---------------------------------------------------------------------------
# Main Calculator
# ---------------------------------------------------------------------------

def calculate_energy_scores(transit_aspects: List[dict], natal_planets: dict, natal_chart: dict = None) -> dict:
    """
    Calculate energy scores from transit aspects.

    Args:
        transit_aspects: list of aspect dicts from get_transits_and_aspects().
                         Each has: transit_planet, natal_planet, aspect, orb
        natal_planets:   natal chart planets dict (used for context, not directly in calc)

    Returns:
        dict with mental, emotional, physical, intuitive scores (integers 1-10)
    """
    deltas = {dim: 0.0 for dim in DIMENSIONS}

    for asp in transit_aspects:
        transit_planet = asp.get('transit_planet', '')
        aspect_name    = asp.get('aspect', '')
        orb            = float(asp.get('orb', 0))

        # Skip unknown planets or aspects
        influences = PLANET_INFLUENCE.get(transit_planet)
        if influences is None:
            continue

        if aspect_name not in ASPECT_MODIFIER and aspect_name != 'conjunction':
            continue

        # Determine effective aspect modifier
        if aspect_name == 'conjunction':
            modifier = _conjunction_modifier(transit_planet)
        else:
            modifier = ASPECT_MODIFIER[aspect_name]

        # Orb weight: tighter orb = more impact; approaches 0 at max_orb
        max_orb = get_max_orb(aspect_name)
        if orb >= max_orb:
            continue  # outside orb, no effect
        weight = (max_orb - orb) / max_orb

        # Accumulate deltas per dimension
        for dim in DIMENSIONS:
            planet_weight = influences.get(dim, 0.0)
            delta = planet_weight * modifier * weight * 2.0
            deltas[dim] += delta

    # Build final scores
    scores = {}
    for dim in DIMENSIONS:
        raw = BASE_SCORE + deltas[dim]
        clamped = max(1.0, min(10.0, raw))
        scores[dim] = int(round(clamped))

    return scores


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys
    import os

    # Add project root so engines is importable
    sys.path.insert(0, os.path.dirname(__file__))

    import engines

    birth_date = '1989-09-05'
    birth_time = '14:38'
    birth_city = 'Reykjavik'

    print(f'Testing energy calculator for {birth_date} {birth_time} {birth_city}')
    print('Fetching today\'s forecast data from ephemeris...')

    forecast_data = engines.get_daily_forecast(
        birth_date=birth_date,
        birth_time=birth_time,
        birth_city=birth_city,
    )

    aspects = forecast_data.get('aspects', [])
    natal_planets = {}

    # Try to get natal planets from blueprint
    try:
        blueprint = engines.build_blueprint(
            birth_date=birth_date,
            birth_time=birth_time,
            birth_city=birth_city,
        )
        natal_planets = blueprint.get('astrology', {}).get('natal', {}).get('planets', {})
    except Exception as e:
        print(f'  (Blueprint build failed: {e} — using empty natal_planets)')

    print(f'\nFound {len(aspects)} transit aspects:')
    for asp in aspects[:10]:
        tp = asp.get('transit_planet', '?')
        aspect_type = asp.get('aspect', '?')
        np = asp.get('natal_planet', '?')
        orb = asp.get('orb', '?')
        print(f'  {tp} {aspect_type} natal {np} (orb {orb}°)')
    if len(aspects) > 10:
        print(f'  ... and {len(aspects) - 10} more')

    scores = calculate_energy_scores(aspects, natal_planets)

    print(f'\n=== CALCULATED ENERGY SCORES ===')
    print(f'  Mental:    {scores["mental"]}/10')
    print(f'  Emotional: {scores["emotional"]}/10')
    print(f'  Physical:  {scores["physical"]}/10')
    print(f'  Intuitive: {scores["intuitive"]}/10')

    print('\n=== RAW DELTAS (for debugging) ===')
    deltas = {dim: 0.0 for dim in ('mental', 'emotional', 'physical', 'intuitive')}
    for asp in aspects:
        tp = asp.get('transit_planet', '')
        aspect_name = asp.get('aspect', '')
        orb = float(asp.get('orb', 0))
        influences = PLANET_INFLUENCE.get(tp)
        if not influences:
            continue
        if aspect_name == 'conjunction':
            modifier = _conjunction_modifier(tp)
        elif aspect_name in ASPECT_MODIFIER:
            modifier = ASPECT_MODIFIER[aspect_name]
        else:
            continue
        max_orb = get_max_orb(aspect_name)
        if orb >= max_orb:
            continue
        weight = (max_orb - orb) / max_orb
        for dim in ('mental', 'emotional', 'physical', 'intuitive'):
            deltas[dim] += influences.get(dim, 0.0) * modifier * weight * 2.0

    for dim, delta in deltas.items():
        print(f'  {dim}: base=5.0, delta={delta:+.2f}, raw={5.0 + delta:.2f}, final={max(1, min(10, round(5.0 + delta)))}')
