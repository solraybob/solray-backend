"""
astrology.py — Astrology Engine for Solray AI
Uses pyswisseph for accurate ephemeris calculations (Swiss Ephemeris).
Supports natal charts, transits, and aspects.
"""

import swisseph as swe
from datetime import datetime, date, timedelta
from typing import Optional
import math

# --- Constants ---

PLANETS = {
    'Sun':       swe.SUN,
    'Moon':      swe.MOON,
    'Mercury':   swe.MERCURY,
    'Venus':     swe.VENUS,
    'Mars':      swe.MARS,
    'Jupiter':   swe.JUPITER,
    'Saturn':    swe.SATURN,
    'Uranus':    swe.URANUS,
    'Neptune':   swe.NEPTUNE,
    'Pluto':     swe.PLUTO,
    'NorthNode': swe.TRUE_NODE,
    'Chiron':    swe.CHIRON,
}

SIGNS = [
    'Aries', 'Taurus', 'Gemini', 'Cancer',
    'Leo', 'Virgo', 'Libra', 'Scorpio',
    'Sagittarius', 'Capricorn', 'Aquarius', 'Pisces'
]

ASPECT_TYPES = {
    'conjunction': (0,   8),
    'opposition':  (180, 8),
    'trine':       (120, 7),
    'square':      (90,  7),
    'sextile':     (60,  6),
}

# Major city coordinates as fallback
CITY_COORDS = {
    'london':        (51.5074, -0.1278),
    'new york':      (40.7128, -74.0060),
    'paris':         (48.8566, 2.3522),
    'madrid':        (40.4168, -3.7038),
    'barcelona':     (41.3851, 2.1734),
    'berlin':        (52.5200, 13.4050),
    'tokyo':         (35.6762, 139.6503),
    'sydney':        (-33.8688, 151.2093),
    'los angeles':   (34.0522, -118.2437),
    'chicago':       (41.8781, -87.6298),
    'toronto':       (43.6532, -79.3832),
    'amsterdam':     (52.3676, 4.9041),
    'rome':          (41.9028, 12.4964),
    'moscow':        (55.7558, 37.6173),
    'dubai':         (25.2048, 55.2708),
    'singapore':     (1.3521, 103.8198),
    'mumbai':        (19.0760, 72.8777),
    'buenos aires':  (-34.6037, -58.3816),
    'mexico city':   (19.4326, -99.1332),
    'cairo':         (30.0444, 31.2357),
}


def geocode_city(city: str, fallback_lat: Optional[float] = None, fallback_lon: Optional[float] = None):
    """
    Convert city name to (lat, lon). 
    Tries geopy Nominatim first, then hardcoded lookup, then raises.
    """
    # Try hardcoded lookup first (fast, no network)
    city_lower = city.lower().strip()
    if city_lower in CITY_COORDS:
        return CITY_COORDS[city_lower]

    # Try geopy Nominatim
    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
        geolocator = Nominatim(user_agent="solray_ai_v1")
        location = geolocator.geocode(city, timeout=5)
        if location:
            return (location.latitude, location.longitude)
    except Exception:
        pass

    # Use provided fallback
    if fallback_lat is not None and fallback_lon is not None:
        return (fallback_lat, fallback_lon)

    raise ValueError(f"Could not geocode city: {city}. Please provide birth_lat and birth_lon.")


def datetime_to_jd(birth_date: str, birth_time: str, tz_offset: float = 0.0) -> float:
    """
    Convert date/time string to Julian Day (UT).
    birth_date: 'YYYY-MM-DD'
    birth_time: 'HH:MM'
    tz_offset: hours offset from UTC (e.g. 1.0 for BST)
    """
    dt = datetime.strptime(f"{birth_date} {birth_time}", "%Y-%m-%d %H:%M")
    # Convert local time to UT
    ut_hour = dt.hour + dt.minute / 60.0 - tz_offset
    jd = swe.julday(dt.year, dt.month, dt.day, ut_hour)
    return jd


def degree_to_sign(lon: float) -> tuple:
    """
    Convert ecliptic longitude (0–360) to (sign, degree_in_sign).
    Returns: (sign_name: str, degree: float)
    """
    sign_idx = int(lon // 30)
    degree = lon % 30
    return SIGNS[sign_idx], round(degree, 4)


def get_planet_positions(jd: float, planets: dict = PLANETS) -> dict:
    """
    Calculate positions for all given planets at Julian Day jd.
    Returns dict of planet_name -> {longitude, sign, degree, retrograde}
    Uses Moshier built-in ephemeris (no external files required).
    Note: Chiron requires external ephemeris files; it will be skipped gracefully
    if those files are not installed.
    """
    swe.set_ephe_path('')  # use built-in Moshier ephemeris
    positions = {}
    for name, planet_id in planets.items():
        flags = swe.FLG_MOSEPH | swe.FLG_SPEED
        try:
            result, ret_flag = swe.calc_ut(jd, planet_id, flags)
            lon = result[0]
            speed = result[3]  # longitudinal speed
            sign, degree = degree_to_sign(lon)
            positions[name] = {
                'longitude': round(lon, 6),
                'sign': sign,
                'degree': round(degree, 4),
                'retrograde': speed < 0,
            }
        except Exception as e:
            # Chiron and other asteroid bodies may require external ephemeris files.
            # Skip gracefully with a placeholder.
            positions[name] = {
                'longitude': None,
                'sign': 'Unknown',
                'degree': None,
                'retrograde': False,
                'error': str(e),
            }
    return positions


def get_ascendant_and_houses(jd: float, lat: float, lon: float, hsys: bytes = b'P') -> dict:
    """
    Calculate Ascendant, MC, and house cusps using Placidus system.
    hsys: b'P' = Placidus (default), b'W' = Whole Sign, etc.
    Returns dict with ascendant, mc, and house_cusps list (12 cusps).
    Note: swe.houses() uses the ARMC and obliquity; no separate flags needed.
    """
    swe.set_ephe_path('')
    cusps, ascmc = swe.houses(jd, lat, lon, hsys)
    # ascmc[0] = Ascendant, ascmc[1] = MC, ascmc[2] = ARMC, etc.
    asc_sign, asc_deg = degree_to_sign(ascmc[0])
    mc_sign, mc_deg = degree_to_sign(ascmc[1])

    house_cusps = []
    for i, cusp in enumerate(cusps):
        sign, deg = degree_to_sign(cusp)
        house_cusps.append({
            'house': i + 1,
            'longitude': round(cusp, 6),
            'sign': sign,
            'degree': round(deg, 4),
        })

    return {
        'ascendant': {
            'longitude': round(ascmc[0], 6),
            'sign': asc_sign,
            'degree': round(asc_deg, 4),
        },
        'mc': {
            'longitude': round(ascmc[1], 6),
            'sign': mc_sign,
            'degree': round(mc_deg, 4),
        },
        'house_cusps': house_cusps,
    }


def assign_houses_to_planets(planet_positions: dict, house_cusps: list) -> dict:
    """
    Determine which house each planet falls in based on house cusp longitudes.
    Returns updated planet_positions with 'house' key added.
    """
    cusp_lons = [h['longitude'] for h in house_cusps]

    def find_house(planet_lon: float) -> int:
        for i in range(12):
            cusp_start = cusp_lons[i]
            cusp_end = cusp_lons[(i + 1) % 12]
            # Handle wrap-around (e.g. cusp 12 end is cusp 1 = early degrees)
            if cusp_end < cusp_start:
                # Crosses 0°/360° boundary
                if planet_lon >= cusp_start or planet_lon < cusp_end:
                    return i + 1
            else:
                if cusp_start <= planet_lon < cusp_end:
                    return i + 1
        return 12  # fallback: last house

    result = {}
    for name, data in planet_positions.items():
        result[name] = dict(data)
        if data.get('longitude') is not None:
            result[name]['house'] = find_house(data['longitude'])
        else:
            result[name]['house'] = None
    return result


def calc_natal_chart(
    birth_date: str,
    birth_time: str,
    birth_lat: float,
    birth_lon: float,
    tz_offset: float = 0.0
) -> dict:
    """
    Calculate complete natal chart.
    Returns dict with planets, ascendant, mc, house_cusps.
    """
    jd = datetime_to_jd(birth_date, birth_time, tz_offset)
    planet_positions = get_planet_positions(jd)
    house_data = get_ascendant_and_houses(jd, birth_lat, birth_lon)
    planets_with_houses = assign_houses_to_planets(planet_positions, house_data['house_cusps'])

    return {
        'julian_day': jd,
        'planets': planets_with_houses,
        'ascendant': house_data['ascendant'],
        'mc': house_data['mc'],
        'house_cusps': house_data['house_cusps'],
    }


def calc_transits(transit_date: str, transit_time: str = "12:00", tz_offset: float = 0.0) -> dict:
    """
    Calculate planet positions for a given transit date.
    Returns planet positions dict (same format as natal planets).
    """
    jd = datetime_to_jd(transit_date, transit_time, tz_offset)
    return get_planet_positions(jd)


def angular_difference(lon1: float, lon2: float) -> float:
    """
    Return the shortest angular difference between two ecliptic longitudes.
    Result is always in [0, 180].
    """
    diff = abs(lon1 - lon2) % 360
    if diff > 180:
        diff = 360 - diff
    return diff


def calc_aspects(transit_planets: dict, natal_planets: dict) -> list:
    """
    Calculate aspects between transit planets and natal planets.
    Returns list of aspect dicts with all relevant info.
    """
    aspects = []
    for t_name, t_data in transit_planets.items():
        if t_data.get('longitude') is None:
            continue
        for n_name, n_data in natal_planets.items():
            if n_data.get('longitude') is None:
                continue
            diff = angular_difference(t_data['longitude'], n_data['longitude'])
            for aspect_name, (exact_angle, orb) in ASPECT_TYPES.items():
                if abs(diff - exact_angle) <= orb:
                    aspects.append({
                        'transit_planet': t_name,
                        'natal_planet': n_name,
                        'aspect': aspect_name,
                        'orb': round(abs(diff - exact_angle), 4),
                        'exact_angle': exact_angle,
                        'actual_angle': round(diff, 4),
                        'transit_sign': t_data['sign'],
                        'natal_sign': n_data['sign'],
                        'natal_house': n_data.get('house'),
                    })
    # Sort by orb (tightest first)
    aspects.sort(key=lambda x: x['orb'])
    return aspects


def get_natal_chart(
    birth_date: str,
    birth_time: str,
    birth_city: str = None,
    birth_lat: float = None,
    birth_lon: float = None,
    tz_offset: float = 0.0
) -> dict:
    """
    Main entry point for natal chart calculation.
    Handles geocoding automatically.
    """
    if birth_lat is None or birth_lon is None:
        if birth_city:
            birth_lat, birth_lon = geocode_city(birth_city, birth_lat, birth_lon)
        else:
            raise ValueError("Must provide either birth_city or both birth_lat and birth_lon")

    chart = calc_natal_chart(birth_date, birth_time, birth_lat, birth_lon, tz_offset)
    chart['birth_data'] = {
        'date': birth_date,
        'time': birth_time,
        'city': birth_city,
        'lat': birth_lat,
        'lon': birth_lon,
        'tz_offset': tz_offset,
    }
    return chart


def get_transits_and_aspects(
    natal_chart: dict,
    transit_date: str,
    transit_time: str = "12:00",
    tz_offset: float = 0.0
) -> dict:
    """
    Get transit planets and their aspects to the natal chart.
    """
    transit_planets = calc_transits(transit_date, transit_time, tz_offset)
    natal_planets = natal_chart['planets']
    aspects = calc_aspects(transit_planets, natal_planets)

    return {
        'date': transit_date,
        'transit_planets': transit_planets,
        'aspects': aspects,
    }
