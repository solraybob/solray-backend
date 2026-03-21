"""
engines.py — Main Orchestrator for Solray AI

Runs all three engines (Astrology, Human Design, Gene Keys) and returns
a unified blueprint dict. Also supports date-specific forecast data.
"""

from datetime import date as date_cls
from typing import Optional

from astrology import get_natal_chart, get_transits_and_aspects, geocode_city
from human_design import calc_human_design
from gene_keys import get_full_gene_keys_profile


def build_blueprint(
    birth_date: str,
    birth_time: str,
    birth_city: str = None,
    birth_lat: float = None,
    birth_lon: float = None,
    tz_offset: float = 0.0,
    transit_date: str = None,
    transit_time: str = "12:00",
) -> dict:
    """
    Generate a complete Solray AI blueprint for a given person.

    Args:
        birth_date:   'YYYY-MM-DD'
        birth_time:   'HH:MM'
        birth_city:   City name (geocoded automatically)
        birth_lat:    Latitude (optional if birth_city provided)
        birth_lon:    Longitude (optional if birth_city provided)
        tz_offset:    UTC offset in hours at time of birth (e.g. 1.0 for BST)
        transit_date: Date for transit/forecast calculations (default: today)
        transit_time: Time for transit calculations (default: '12:00')

    Returns:
        Unified blueprint dict with all astrology, human design, and gene keys data.
    """
    if transit_date is None:
        transit_date = date_cls.today().isoformat()

    # --- Resolve coordinates ---
    if (birth_lat is None or birth_lon is None) and birth_city:
        birth_lat, birth_lon = geocode_city(birth_city, birth_lat, birth_lon)
    elif birth_lat is None or birth_lon is None:
        raise ValueError("Must provide either birth_city or both birth_lat and birth_lon")

    # --- Run Astrology Engine ---
    natal_chart = get_natal_chart(
        birth_date=birth_date,
        birth_time=birth_time,
        birth_city=birth_city,
        birth_lat=birth_lat,
        birth_lon=birth_lon,
        tz_offset=tz_offset,
    )

    transits = get_transits_and_aspects(
        natal_chart=natal_chart,
        transit_date=transit_date,
        transit_time=transit_time,
        tz_offset=0.0,  # transits are always in UTC/world time
    )

    # --- Run Human Design Engine ---
    hd_chart = calc_human_design(
        birth_date=birth_date,
        birth_time=birth_time,
        birth_lat=birth_lat,
        birth_lon=birth_lon,
        tz_offset=tz_offset,
        transit_date=transit_date,
    )

    # --- Run Gene Keys Engine ---
    gene_keys = get_full_gene_keys_profile(
        active_gates=hd_chart['active_gates'],
        todays_gates=hd_chart['todays_gates'],
    )

    # --- Assemble Blueprint ---
    blueprint = {
        'meta': {
            'birth_date': birth_date,
            'birth_time': birth_time,
            'birth_city': birth_city,
            'birth_lat': birth_lat,
            'birth_lon': birth_lon,
            'tz_offset': tz_offset,
            'forecast_date': transit_date,
        },

        # Astrology
        'astrology': {
            'natal': natal_chart,
            'transits': transits,
        },

        # Human Design
        'human_design': hd_chart,

        # Gene Keys
        'gene_keys': gene_keys,

        # Summary card (easy access for UI rendering)
        'summary': {
            'sun_sign': natal_chart['planets']['Sun']['sign'],
            'moon_sign': natal_chart['planets']['Moon']['sign'],
            'ascendant': natal_chart['ascendant']['sign'],
            'hd_type': hd_chart['type'],
            'hd_strategy': hd_chart['strategy'],
            'hd_authority': hd_chart['authority'],
            'hd_profile': hd_chart['profile'],
            'incarnation_cross': hd_chart['incarnation_cross']['label'],
            'active_gates_count': len(hd_chart['active_gates']),
            'defined_centres': [k for k, v in hd_chart['defined_centres'].items() if v],
            'defined_channels_count': len(hd_chart['defined_channels']),
            'todays_sun_gate': hd_chart['todays_gates']['sun_gate'],
            'todays_earth_gate': hd_chart['todays_gates']['earth_gate'],
            'active_aspects_count': len(transits['aspects']),
        }
    }

    return blueprint


def get_daily_forecast(
    birth_date: str,
    birth_time: str,
    birth_city: str = None,
    birth_lat: float = None,
    birth_lon: float = None,
    tz_offset: float = 0.0,
    forecast_date: str = None,
) -> dict:
    """
    Convenience wrapper to get today's (or a specific date's) forecast data.
    Returns a focused forecast dict with transits, aspects, and active gene keys.
    """
    bp = build_blueprint(
        birth_date=birth_date,
        birth_time=birth_time,
        birth_city=birth_city,
        birth_lat=birth_lat,
        birth_lon=birth_lon,
        tz_offset=tz_offset,
        transit_date=forecast_date,
    )

    forecast = {
        'date': bp['meta']['forecast_date'],
        'transits': bp['astrology']['transits']['transit_planets'],
        'aspects': bp['astrology']['transits']['aspects'][:10],  # top 10 tightest
        'hd_daily_gates': bp['human_design']['todays_gates'],
        'gene_keys_today': bp['gene_keys'].get('todays_gene_keys'),
        'gene_key_resonance': bp['gene_keys'].get('resonance', []),
    }

    return forecast
