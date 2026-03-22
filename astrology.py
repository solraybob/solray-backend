"""
astrology.py — Astrology Engine for Solray AI
Uses pyswisseph for accurate ephemeris calculations (Swiss Ephemeris).
Supports natal charts, transits, and aspects.
"""

import swisseph as swe
from datetime import datetime, date, timedelta
from typing import Optional
import math
import os

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

# Extended bodies: asteroids + Black Moon Lilith (Mean Apogee)
EXTENDED_BODIES = {
    'Chiron':          swe.CHIRON,
    'Ceres':           swe.CERES,
    'Pallas':          swe.PALLAS,
    'Juno':            swe.JUNO,
    'Vesta':           swe.VESTA,
    'BlackMoonLilith': swe.MEAN_APOG,
}

SIGNS = [
    'Aries', 'Taurus', 'Gemini', 'Cancer',
    'Leo', 'Virgo', 'Libra', 'Scorpio',
    'Sagittarius', 'Capricorn', 'Aquarius', 'Pisces'
]

# Corrected sign rulerships:
#   Earth rules Taurus (not Venus — Venus is still a planet but not the ruler)
#   Ceres rules Virgo  (not Mercury — Mercury is still a planet but not the ruler)
SIGN_RULERS = {
    'Aries':       'Mars',
    'Taurus':      'Earth',      # True ruler (classical: Venus)
    'Gemini':      'Mercury',
    'Cancer':      'Moon',
    'Leo':         'Sun',
    'Virgo':       'Ceres',      # True ruler (classical: Mercury)
    'Libra':       'Venus',
    'Scorpio':     'Pluto',
    'Sagittarius': 'Jupiter',
    'Capricorn':   'Saturn',
    'Aquarius':    'Uranus',
    'Pisces':      'Neptune',
}

ASPECT_TYPES = {
    'conjunction':   (0,   8),
    'opposition':    (180, 8),
    'trine':         (120, 7),
    'square':        (90,  7),
    'sextile':       (60,  6),
    'quincunx':      (150, 3),
    'semi_sextile':  (30,  2),
    'semi_square':   (45,  2),
    'sesquiquadrate':(135, 2),
    'quintile':      (72,  2),
    'bi_quintile':   (144, 2),
}

# --- Ephemeris path setup ---
# Swiss Ephemeris .se1 files for asteroid calculations (Chiron, Ceres, etc.)
# Download from: https://github.com/aloistr/swisseph/tree/master/ephe
# Place files in ~/ephe/ (or set SWISSEPH_PATH env var)
_EPHE_PATH = os.environ.get('SWISSEPH_PATH', os.path.expanduser('~/ephe'))

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
    city_lower = city.lower().strip()
    if city_lower in CITY_COORDS:
        return CITY_COORDS[city_lower]

    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
        geolocator = Nominatim(user_agent="solray_ai_v1")
        location = geolocator.geocode(city, timeout=5)
        if location:
            return (location.latitude, location.longitude)
    except Exception:
        pass

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


def _find_house(planet_lon: float, house_cusps: list) -> int:
    """
    Determine which house a given ecliptic longitude falls in.
    house_cusps: list of dicts with 'longitude' key (12 entries).
    """
    cusp_lons = [h['longitude'] for h in house_cusps]
    for i in range(12):
        cusp_start = cusp_lons[i]
        cusp_end = cusp_lons[(i + 1) % 12]
        if cusp_end < cusp_start:
            # Crosses 0°/360° boundary
            if planet_lon >= cusp_start or planet_lon < cusp_end:
                return i + 1
        else:
            if cusp_start <= planet_lon < cusp_end:
                return i + 1
    return 12  # fallback


def get_planet_positions(jd: float, planets: dict = PLANETS) -> dict:
    """
    Calculate positions for all given planets at Julian Day jd.
    Returns dict of planet_name -> {longitude, sign, degree, retrograde}

    Strategy:
    - Core planets (Sun–Pluto, NorthNode): use Moshier built-in (no files needed).
    - Chiron: try Swiss Ephemeris files first (~/ephe/), fall back to Moshier.
    """
    swe.set_ephe_path('')  # default: Moshier built-in
    positions = {}
    for name, planet_id in planets.items():
        # For Chiron, try SWIEPH first (more accurate) then fall back to Moshier
        if name == 'Chiron':
            swe.set_ephe_path(_EPHE_PATH)
            flags = swe.FLG_SWIEPH | swe.FLG_SPEED
        else:
            swe.set_ephe_path('')
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
        except Exception:
            # Chiron: fall back to Moshier if SE1 files missing
            if name == 'Chiron':
                try:
                    swe.set_ephe_path('')
                    result, _ = swe.calc_ut(jd, planet_id, swe.FLG_MOSEPH | swe.FLG_SPEED)
                    lon = result[0]; speed = result[3]
                    sign, degree = degree_to_sign(lon)
                    positions[name] = {
                        'longitude': round(lon, 6),
                        'sign': sign,
                        'degree': round(degree, 4),
                        'retrograde': speed < 0,
                        'note': 'Moshier fallback (no SE1 files)',
                    }
                    continue
                except Exception as e2:
                    positions[name] = {
                        'longitude': None, 'sign': 'Unknown',
                        'degree': None, 'retrograde': False, 'error': str(e2),
                    }
            else:
                positions[name] = {
                    'longitude': None, 'sign': 'Unknown',
                    'degree': None, 'retrograde': False,
                }
    return positions


def get_ascendant_and_houses(jd: float, lat: float, lon: float, hsys: bytes = b'P') -> dict:
    """
    Calculate Ascendant, MC, house cusps, Vertex, and East Point using Placidus system.
    hsys: b'P' = Placidus (default), b'W' = Whole Sign, etc.

    ascmc indices from swe.houses():
      [0] Ascendant
      [1] MC
      [2] ARMC
      [3] Vertex
      [4] Equatorial Ascendant (East Point)
    """
    swe.set_ephe_path('')  # houses calculation uses built-in Moshier
    cusps, ascmc = swe.houses(jd, lat, lon, hsys)

    asc_sign, asc_deg = degree_to_sign(ascmc[0])
    mc_sign, mc_deg = degree_to_sign(ascmc[1])

    # Vertex (ascmc[3]) — intersection of prime vertical and ecliptic, west side
    vertex_lon = ascmc[3] % 360.0
    vertex_sign, vertex_deg = degree_to_sign(vertex_lon)

    # East Point / Equatorial Ascendant (ascmc[4])
    ep_lon = ascmc[4] % 360.0
    ep_sign, ep_deg = degree_to_sign(ep_lon)

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
        'armc': round(ascmc[2], 6),
        'vertex_longitude': round(vertex_lon, 6),
        'east_point_longitude': round(ep_lon, 6),
        'house_cusps': house_cusps,
    }


def assign_houses_to_planets(planet_positions: dict, house_cusps: list) -> dict:
    """
    Determine which house each planet falls in based on house cusp longitudes.
    Returns updated planet_positions with 'house' key added.
    """
    result = {}
    for name, data in planet_positions.items():
        result[name] = dict(data)
        if data.get('longitude') is not None:
            result[name]['house'] = _find_house(data['longitude'], house_cusps)
        else:
            result[name]['house'] = None
    return result


def calc_extended_points(
    jd: float,
    natal_planets: dict,
    house_data: dict,
) -> dict:
    """
    Calculate extended chart points:
      - Asteroids: Chiron, Ceres, Pallas, Juno, Vesta
      - Black Moon Lilith (Mean Apogee)
      - Earth (Sun + 180°)
      - Part of Fortune (ASC + Moon − Sun for day; ASC + Sun − Moon for night)
      - Vertex (from house data via ascmc[3])
      - East Point / Equatorial Ascendant (from house data via ascmc[4])

    Returns dict of key -> point data dict.
    Each point data dict contains:
      name, sign, degree, absolute_degree, house, retrograde
    """
    swe.set_ephe_path('')
    extended = {}
    house_cusps = house_data['house_cusps']
    asc_lon     = house_data['ascendant']['longitude']
    vertex_lon  = house_data.get('vertex_longitude')
    ep_lon      = house_data.get('east_point_longitude')

    # ----------------------------------------------------------------
    # Extended bodies (asteroids + Black Moon Lilith)
    # Asteroids require Swiss Ephemeris SE1 files; BML uses Moshier.
    # ----------------------------------------------------------------
    for name, body_id in EXTENDED_BODIES.items():
        # Black Moon Lilith (Mean Apogee) works with Moshier
        if body_id == swe.MEAN_APOG:
            swe.set_ephe_path('')
            flags = swe.FLG_MOSEPH | swe.FLG_SPEED
        else:
            swe.set_ephe_path(_EPHE_PATH)
            flags = swe.FLG_SWIEPH | swe.FLG_SPEED
        try:
            result, _ = swe.calc_ut(jd, body_id, flags)
            lon   = result[0]
            speed = result[3]
            sign, degree = degree_to_sign(lon)
            extended[name] = {
                'name': name,
                'absolute_degree': round(lon, 4),
                'sign': sign,
                'degree': round(degree, 4),
                'house': _find_house(lon, house_cusps),
                'retrograde': speed < 0,
            }
        except Exception as e:
            extended[name] = {
                'name': name,
                'absolute_degree': None,
                'sign': 'Unknown',
                'degree': None,
                'house': None,
                'retrograde': False,
                'error': str(e),
            }

    # ----------------------------------------------------------------
    # Earth — always exactly opposite the Sun (geocentric)
    # ----------------------------------------------------------------
    sun_data = natal_planets.get('Sun', {})
    sun_lon  = sun_data.get('longitude')
    if sun_lon is not None:
        earth_lon = (sun_lon + 180.0) % 360.0
        sign, degree = degree_to_sign(earth_lon)
        extended['Earth'] = {
            'name': 'Earth',
            'absolute_degree': round(earth_lon, 4),
            'sign': sign,
            'degree': round(degree, 4),
            'house': _find_house(earth_lon, house_cusps),
            'retrograde': False,
        }

    # ----------------------------------------------------------------
    # Part of Fortune
    # Day chart  (Sun in houses 7–12, above horizon): ASC + Moon − Sun
    # Night chart (Sun in houses 1–6, below horizon): ASC + Sun  − Moon
    # ----------------------------------------------------------------
    moon_lon = natal_planets.get('Moon', {}).get('longitude')
    if sun_lon is not None and moon_lon is not None:
        sun_house = natal_planets.get('Sun', {}).get('house', 1) or 1
        if sun_house >= 7:
            pof_lon = (asc_lon + moon_lon - sun_lon) % 360.0
            chart_type = 'day'
        else:
            pof_lon = (asc_lon + sun_lon - moon_lon) % 360.0
            chart_type = 'night'
        sign, degree = degree_to_sign(pof_lon)
        extended['PartOfFortune'] = {
            'name': 'Part of Fortune',
            'absolute_degree': round(pof_lon, 4),
            'sign': sign,
            'degree': round(degree, 4),
            'house': _find_house(pof_lon, house_cusps),
            'retrograde': False,
            'chart_type': chart_type,
        }

    # ----------------------------------------------------------------
    # Vertex — intersection of prime vertical and ecliptic (west side)
    # Provided directly by swe.houses() in ascmc[3]
    # ----------------------------------------------------------------
    if vertex_lon is not None:
        sign, degree = degree_to_sign(vertex_lon)
        extended['Vertex'] = {
            'name': 'Vertex',
            'absolute_degree': round(vertex_lon, 4),
            'sign': sign,
            'degree': round(degree, 4),
            'house': _find_house(vertex_lon, house_cusps),
            'retrograde': False,
        }

    # ----------------------------------------------------------------
    # East Point (Equatorial Ascendant)
    # The degree of the ecliptic rising on the east horizon with 0° obliquity.
    # Provided directly by swe.houses() in ascmc[4].
    # Equivalent to ARMC + 90° projected onto the ecliptic.
    # ----------------------------------------------------------------
    if ep_lon is not None:
        sign, degree = degree_to_sign(ep_lon)
        extended['EastPoint'] = {
            'name': 'East Point',
            'absolute_degree': round(ep_lon, 4),
            'sign': sign,
            'degree': round(degree, 4),
            'house': _find_house(ep_lon, house_cusps),
            'retrograde': False,
        }

    return extended


def calc_natal_chart(
    birth_date: str,
    birth_time: str,
    birth_lat: float,
    birth_lon: float,
    tz_offset: float = 0.0
) -> dict:
    """
    Calculate complete natal chart including extended points.
    Returns dict with planets, ascendant, mc, house_cusps, extended_points.
    """
    jd = datetime_to_jd(birth_date, birth_time, tz_offset)
    planet_positions = get_planet_positions(jd)
    house_data = get_ascendant_and_houses(jd, birth_lat, birth_lon)
    planets_with_houses = assign_houses_to_planets(planet_positions, house_data['house_cusps'])

    extended_points = calc_extended_points(jd, planets_with_houses, house_data)

    return {
        'julian_day': jd,
        'planets': planets_with_houses,
        'ascendant': house_data['ascendant'],
        'mc': house_data['mc'],
        'armc': house_data['armc'],
        'house_cusps': house_data['house_cusps'],
        'extended_points': extended_points,
    }


def calc_transits(
    transit_date: str,
    transit_time: str = "12:00",
    tz_offset: float = 0.0,
    birth_lat: float = None,
    birth_lon: float = None,
) -> dict:
    """
    Calculate planet positions for a given transit date, including extended bodies.
    Returns dict with 'transit_planets' and 'extended_transit_points'.

    birth_lat/birth_lon are optional — only needed to compute chart-sensitive
    extended points (Vertex, East Point, Part of Fortune) for the transit moment.
    If not provided those three points are omitted from extended transits.
    """
    jd = datetime_to_jd(transit_date, transit_time, tz_offset)
    transit_planets = get_planet_positions(jd)

    # Extended body transits (asteroids + BML + Earth)
    extended_transit = {}
    for name, body_id in EXTENDED_BODIES.items():
        if body_id == swe.MEAN_APOG:
            swe.set_ephe_path('')
            flags = swe.FLG_MOSEPH | swe.FLG_SPEED
        else:
            swe.set_ephe_path(_EPHE_PATH)
            flags = swe.FLG_SWIEPH | swe.FLG_SPEED
        try:
            result, _ = swe.calc_ut(jd, body_id, flags)
            lon   = result[0]
            speed = result[3]
            sign, degree = degree_to_sign(lon)
            extended_transit[name] = {
                'name': name,
                'absolute_degree': round(lon, 4),
                'sign': sign,
                'degree': round(degree, 4),
                'house': None,  # no birth chart context for transits
                'retrograde': speed < 0,
                'longitude': round(lon, 6),
            }
        except Exception as e:
            extended_transit[name] = {
                'name': name,
                'absolute_degree': None,
                'sign': 'Unknown',
                'degree': None,
                'house': None,
                'retrograde': False,
                'longitude': None,
                'error': str(e),
            }

    # Transit Earth (Sun + 180°)
    t_sun_lon = transit_planets.get('Sun', {}).get('longitude')
    if t_sun_lon is not None:
        earth_lon = (t_sun_lon + 180.0) % 360.0
        sign, degree = degree_to_sign(earth_lon)
        extended_transit['Earth'] = {
            'name': 'Earth',
            'absolute_degree': round(earth_lon, 4),
            'sign': sign,
            'degree': round(degree, 4),
            'house': None,
            'retrograde': False,
            'longitude': round(earth_lon, 6),
        }

    # Chart-sensitive points (only if birth location provided)
    if birth_lat is not None and birth_lon is not None:
        try:
            t_house_data = get_ascendant_and_houses(jd, birth_lat, birth_lon)
            # For transit PoF we need transit Moon & Sun with houses
            t_planets_with_houses = assign_houses_to_planets(transit_planets, t_house_data['house_cusps'])
            sensitive = calc_extended_points(jd, t_planets_with_houses, t_house_data)
            for key in ('PartOfFortune', 'Vertex', 'EastPoint'):
                if key in sensitive:
                    sensitive[key]['longitude'] = sensitive[key]['absolute_degree']
                    extended_transit[key] = sensitive[key]
        except Exception:
            pass

    return {
        'transit_planets': transit_planets,
        'extended_transit_points': extended_transit,
    }


def angular_difference(lon1: float, lon2: float) -> float:
    """
    Return the shortest angular difference between two ecliptic longitudes.
    Result is always in [0, 180].
    """
    diff = abs(lon1 - lon2) % 360
    if diff > 180:
        diff = 360 - diff
    return diff


def calc_aspects(transit_planets: dict, natal_planets: dict,
                 transit_extended: dict = None, natal_extended: dict = None) -> list:
    """
    Calculate aspects between transit planets and natal planets.
    Optionally includes extended points in both natal and transit.
    Returns list of aspect dicts sorted by orb (tightest first).
    """
    # Merge extended points into working copies (read-only, flat longitude access)
    def _flatten(base: dict, ext: dict) -> dict:
        merged = dict(base)
        if ext:
            for k, v in ext.items():
                if v.get('longitude') is None and v.get('absolute_degree') is not None:
                    v = dict(v); v['longitude'] = v['absolute_degree']
                merged[k] = v
        return merged

    all_transit = _flatten(transit_planets, transit_extended)
    all_natal   = _flatten(natal_planets,   natal_extended)

    aspects = []
    for t_name, t_data in all_transit.items():
        if t_data.get('longitude') is None:
            continue
        for n_name, n_data in all_natal.items():
            if n_data.get('longitude') is None:
                continue
            diff = angular_difference(t_data['longitude'], n_data['longitude'])
            for aspect_name, (exact_angle, orb) in ASPECT_TYPES.items():
                if abs(diff - exact_angle) <= orb:
                    aspects.append({
                        'transit_planet': t_name,
                        'natal_planet':   n_name,
                        'aspect':         aspect_name,
                        'orb':            round(abs(diff - exact_angle), 4),
                        'exact_angle':    exact_angle,
                        'actual_angle':   round(diff, 4),
                        'transit_sign':   t_data['sign'],
                        'natal_sign':     n_data['sign'],
                        'natal_house':    n_data.get('house'),
                    })
    aspects.sort(key=lambda x: x['orb'])
    return aspects


def calc_natal_aspects(natal_planets: dict, orb_factor: float = 1.0) -> list:
    """
    Calculate aspects between natal planets (planet-to-planet in the birth chart).
    Returns list of aspect dicts sorted by orb (tightest first).
    """
    planet_names = list(natal_planets.keys())
    aspects = []
    
    for i, p1 in enumerate(planet_names):
        d1 = natal_planets[p1]
        lon1 = d1.get('longitude')
        if lon1 is None:
            continue
        for p2 in planet_names[i+1:]:
            d2 = natal_planets[p2]
            lon2 = d2.get('longitude')
            if lon2 is None:
                continue
            diff = abs(lon1 - lon2) % 360
            if diff > 180:
                diff = 360 - diff
            for aspect_name, (exact_angle, max_orb) in ASPECT_TYPES.items():
                orb = abs(diff - exact_angle)
                if orb <= max_orb * orb_factor:
                    aspects.append({
                        'planet1':  p1,
                        'planet2':  p2,
                        'aspect':   aspect_name,
                        'orb':      round(orb, 2),
                        'applying': None,
                    })
                    break
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
    Returns chart with 'planets' (classic) and 'extended_points' (new bodies/points).
    """
    if birth_lat is None or birth_lon is None:
        if birth_city:
            birth_lat, birth_lon = geocode_city(birth_city, birth_lat, birth_lon)
        else:
            raise ValueError("Must provide either birth_city or both birth_lat and birth_lon")

    chart = calc_natal_chart(birth_date, birth_time, birth_lat, birth_lon, tz_offset)
    chart['birth_data'] = {
        'date':      birth_date,
        'time':      birth_time,
        'city':      birth_city,
        'lat':       birth_lat,
        'lon':       birth_lon,
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
    Get transit planets (including extended bodies) and their aspects to the natal chart.
    """
    birth_data = natal_chart.get('birth_data', {})
    birth_lat  = birth_data.get('lat')
    birth_lon  = birth_data.get('lon')

    transit_data = calc_transits(
        transit_date, transit_time, tz_offset,
        birth_lat=birth_lat, birth_lon=birth_lon,
    )
    transit_planets   = transit_data['transit_planets']
    extended_transits = transit_data['extended_transit_points']

    natal_planets  = natal_chart['planets']
    natal_extended = natal_chart.get('extended_points', {})

    aspects = calc_aspects(
        transit_planets, natal_planets,
        transit_extended=extended_transits,
        natal_extended=natal_extended,
    )

    return {
        'date':                   transit_date,
        'transit_planets':        transit_planets,
        'extended_transit_points': extended_transits,
        'aspects':                aspects,
    }
