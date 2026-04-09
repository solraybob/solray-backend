"""
astrocartography.py — Astrocartography (AstroGeography) calculation

Calculates the 4 planetary lines for each planet:
  - MC line: longitudes where the planet was on the Midheaven at birth
  - IC line: opposite of MC (antipodal)
  - ASC line: longitudes where the planet was Rising at birth (latitude-dependent arc)
  - DSC line: opposite of ASC

Method:
  MC/IC lines are single meridians (great circles of longitude).
  ASC/DSC lines are curved arcs that vary with latitude.

Each line is returned as a list of {lat, lon} points for map rendering.
"""

import swisseph as swe
import math
from typing import Optional


PLANETS = {
    'Sun':     swe.SUN,
    'Moon':    swe.MOON,
    'Mercury': swe.MERCURY,
    'Venus':   swe.VENUS,
    'Mars':    swe.MARS,
    'Jupiter': swe.JUPITER,
    'Saturn':  swe.SATURN,
    'Uranus':  swe.URANUS,
    'Neptune': swe.NEPTUNE,
    'Pluto':   swe.PLUTO,
}

PLANET_SYMBOLS = {
    'Sun': '☉', 'Moon': '☽', 'Mercury': '☿', 'Venus': '♀',
    'Mars': '♂', 'Jupiter': '♃', 'Saturn': '♄',
    'Uranus': '♅', 'Neptune': '♆', 'Pluto': '♇',
}

# Line colors for frontend rendering
LINE_COLORS = {
    'Sun':     '#F5C842',
    'Moon':    '#C8D8E8',
    'Mercury': '#A8C8A8',
    'Venus':   '#E8A0A0',
    'Mars':    '#E85030',
    'Jupiter': '#E8C080',
    'Saturn':  '#A09080',
    'Uranus':  '#80C8E8',
    'Neptune': '#8080D8',
    'Pluto':   '#C080C8',
}


def calc_astrocartography(
    birth_date: str,
    birth_time: str,
    birth_lat: float,
    birth_lon: float,
    tz_offset: float = 0.0,
    lat_step: float = 5.0,
) -> dict:
    """
    Calculate astrocartography lines for all planets.

    Args:
        birth_date:  'YYYY-MM-DD'
        birth_time:  'HH:MM'
        birth_lat:   birth latitude
        birth_lon:   birth longitude
        tz_offset:   UTC offset in hours
        lat_step:    latitude step for ASC/DSC arc calculation (degrees)

    Returns:
        Dict with 'lines' list and 'birth_location' dict.
    """
    from datetime import datetime

    swe.set_ephe_path('')

    dt = datetime.strptime(f'{birth_date} {birth_time}', '%Y-%m-%d %H:%M')
    ut_hour = dt.hour + dt.minute / 60.0 - tz_offset
    jd = swe.julday(dt.year, dt.month, dt.day, ut_hour)

    # GMST in degrees
    gmst_deg = swe.sidtime(jd) * 15.0

    lines = []

    for planet_name, planet_id in PLANETS.items():
        # Get equatorial coordinates (RA, Dec)
        flags = swe.FLG_MOSEPH | swe.FLG_EQUATORIAL
        result, _ = swe.calc_ut(jd, planet_id, flags)
        ra = result[0]    # Right Ascension in degrees
        dec = result[1]   # Declination in degrees

        # ── MC Line ──────────────────────────────────────────────────────
        # MC longitude: where LST = RA, so lon = RA - GMST
        mc_lon = (ra - gmst_deg + 180) % 360 - 180  # -180..180
        ic_lon = mc_lon + 180 if mc_lon <= 0 else mc_lon - 180

        # MC is a vertical line at mc_lon from -90 to +90
        mc_points = [{'lat': lat, 'lon': round(mc_lon, 3)} for lat in range(-90, 91, 5)]
        ic_points = [{'lat': lat, 'lon': round(ic_lon, 3)} for lat in range(-90, 91, 5)]

        lines.append({
            'planet': planet_name,
            'symbol': PLANET_SYMBOLS.get(planet_name, ''),
            'color':  LINE_COLORS.get(planet_name, '#888888'),
            'type':   'MC',
            'points': mc_points,
            'lon':    round(mc_lon, 3),
        })
        lines.append({
            'planet': planet_name,
            'symbol': PLANET_SYMBOLS.get(planet_name, ''),
            'color':  LINE_COLORS.get(planet_name, '#888888'),
            'type':   'IC',
            'points': ic_points,
            'lon':    round(ic_lon, 3),
        })

        # ── ASC/DSC Lines ─────────────────────────────────────────────────
        # At each latitude φ, the ASC longitude is where the planet rises.
        # Planet rises when: cos(H) = (sin(dec)*sin(φ) - sin(alt)) / (cos(dec)*cos(φ))
        # where alt ≈ -0.5667° (standard refraction for horizon)
        # H = hour angle, ASC_lon = RA - H - GMST
        alt_rad = math.radians(-0.5667)
        dec_rad = math.radians(dec)

        asc_points = []
        dsc_points = []

        for lat_int in range(-85, 86, int(lat_step)):
            lat_rad = math.radians(lat_int)

            cos_h = (math.sin(alt_rad) - math.sin(dec_rad) * math.sin(lat_rad)) / (
                math.cos(dec_rad) * math.cos(lat_rad)
            )

            if abs(cos_h) > 1.0:
                # Planet never rises or sets at this latitude (circumpolar or never rises)
                continue

            H = math.degrees(math.acos(cos_h))  # 0..180

            # ASC: planet rising → H negative (east of meridian)
            asc_ha = -H
            asc_lon = (ra + asc_ha - gmst_deg + 180) % 360 - 180

            # DSC: planet setting → H positive (west of meridian)
            dsc_ha = +H
            dsc_lon = (ra + dsc_ha - gmst_deg + 180) % 360 - 180

            asc_points.append({'lat': lat_int, 'lon': round(asc_lon, 3)})
            dsc_points.append({'lat': lat_int, 'lon': round(dsc_lon, 3)})

        if asc_points:
            lines.append({
                'planet': planet_name,
                'symbol': PLANET_SYMBOLS.get(planet_name, ''),
                'color':  LINE_COLORS.get(planet_name, '#888888'),
                'type':   'ASC',
                'points': asc_points,
            })
        if dsc_points:
            lines.append({
                'planet': planet_name,
                'symbol': PLANET_SYMBOLS.get(planet_name, ''),
                'color':  LINE_COLORS.get(planet_name, '#888888'),
                'type':   'DSC',
                'points': dsc_points,
            })

    return {
        'lines': lines,
        'birth_location': {
            'lat': round(birth_lat, 4),
            'lon': round(birth_lon, 4),
        },
        'planet_colors': LINE_COLORS,
        'planet_symbols': PLANET_SYMBOLS,
    }


def get_nearest_lines(
    birth_date: str,
    birth_time: str,
    birth_lat: float,
    birth_lon: float,
    tz_offset: float = 0.0,
    check_lat: Optional[float] = None,
    check_lon: Optional[float] = None,
    radius_deg: float = 15.0,
) -> list:
    """
    Find which planetary lines pass near a given location.
    Returns list of nearby lines sorted by distance.
    """
    if check_lat is None:
        check_lat = birth_lat
    if check_lon is None:
        check_lon = birth_lon

    result = calc_astrocartography(birth_date, birth_time, birth_lat, birth_lon, tz_offset)
    nearby = []

    for line in result['lines']:
        if line['type'] in ('MC', 'IC'):
            # MC/IC: distance is longitude difference
            dist = abs(check_lon - line['lon'])
            if dist > 180:
                dist = 360 - dist
            if dist <= radius_deg:
                nearby.append({
                    'planet': line['planet'],
                    'type': line['type'],
                    'distance_deg': round(dist, 2),
                    'color': line['color'],
                    'symbol': line['symbol'],
                })
        else:
            # ASC/DSC: find closest point on the arc
            min_dist = float('inf')
            for pt in line['points']:
                dlat = abs(check_lat - pt['lat'])
                dlon = abs(check_lon - pt['lon'])
                if dlon > 180:
                    dlon = 360 - dlon
                dist = math.sqrt(dlat**2 + dlon**2)
                if dist < min_dist:
                    min_dist = dist
            if min_dist <= radius_deg:
                nearby.append({
                    'planet': line['planet'],
                    'type': line['type'],
                    'distance_deg': round(min_dist, 2),
                    'color': line['color'],
                    'symbol': line['symbol'],
                })

    nearby.sort(key=lambda x: x['distance_deg'])
    return nearby


# Line meanings for interpretations
LINE_MEANINGS = {
    'MC': {
        'Sun':     'Career visibility, public recognition, leadership. You are seen here.',
        'Moon':    'Emotional sensitivity heightened. Strong sense of home and belonging.',
        'Mercury': 'Mental clarity and communication flow. Good for study and writing.',
        'Venus':   'Beauty, love, and artistic expression flourish. Relationships thrive.',
        'Mars':    'Drive and ambition energized. Conflict and passion both amplified.',
        'Jupiter': 'Expansion, luck, and opportunity. Growth in career and reputation.',
        'Saturn':  'Discipline and long-term achievement. Hard work rewarded here.',
        'Uranus':  'Unexpected change and innovation. Breakthroughs in public life.',
        'Neptune': 'Spiritual purpose and creative vision. Idealism shapes your work.',
        'Pluto':   'Transformative power and intensity. Deep career metamorphosis.',
    },
    'IC': {
        'Sun':     'Strong sense of identity at home. Family legacy matters here.',
        'Moon':    'Deep emotional roots. Past and heritage feel close. Rest and healing.',
        'Mercury': 'Active family communication. Home becomes a place for ideas.',
        'Venus':   'Beautiful, harmonious home life. Domestic happiness and comfort.',
        'Mars':    'Active, energetic home environment. Family conflicts possible.',
        'Jupiter': 'Expansive, generous home life. Family brings growth and luck.',
        'Saturn':  'Structured but stable home. Lessons around family and foundation.',
        'Uranus':  'Unconventional home life. Restlessness and frequent changes.',
        'Neptune': 'Dreamy, spiritual home environment. Boundaries can blur.',
        'Pluto':   'Intense transformation of home and roots. Deep ancestral work.',
    },
    'ASC': {
        'Sun':     'Vitality and charisma shine. Others see your full authentic self.',
        'Moon':    'Emotional and nurturing qualities highlighted. Deep intuition.',
        'Mercury': 'Quick thinking and wit. You communicate easily and are well-received.',
        'Venus':   'Magnetic and attractive energy. Beauty and grace in your presence.',
        'Mars':    'Assertive and energetic presence. Physical vitality at its peak.',
        'Jupiter': 'Expansive optimism and generosity. People gravitate toward you.',
        'Saturn':  'Serious and authoritative presence. Respect through perseverance.',
        'Uranus':  'Eccentric and original energy. You stand out and spark curiosity.',
        'Neptune': 'Mysterious and ethereal quality. Spiritual sensitivity amplified.',
        'Pluto':   'Intense and magnetic. Profound impact on others. Transformation catalyst.',
    },
    'DSC': {
        'Sun':     'Powerful partnerships. Relationships define you here.',
        'Moon':    'Emotional bonds deep and lasting. Partnerships feel fated.',
        'Mercury': 'Intellectual partnerships. Communication with others is key.',
        'Venus':   'Romantic and harmonious partnerships. Love and beauty in relationships.',
        'Mars':    'Passionate but potentially contentious relationships. High energy.',
        'Jupiter': 'Lucky in partnerships. Growth through others. Generous bonds.',
        'Saturn':  'Serious, committed relationships. Karmic partnerships possible.',
        'Uranus':  'Unusual and awakening partnerships. Others challenge your norms.',
        'Neptune': 'Spiritual or creative partnerships. Idealization risk.',
        'Pluto':   'Transformative relationships. Power dynamics and deep bonds.',
    },
}


def get_line_meaning(planet: str, line_type: str) -> str:
    return LINE_MEANINGS.get(line_type, {}).get(planet, '')
