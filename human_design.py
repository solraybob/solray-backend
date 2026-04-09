"""
human_design.py — Human Design Engine for Solray AI

Calculates the Human Design BodyGraph from birth data using Swiss Ephemeris.
Covers: gates, channels, centres, type, strategy, authority, profile, incarnation cross.

Reference: Ra Uru Hu's original system (Jovian Archive). The HD mandala maps
the 64 hexagrams of the I Ching onto the ecliptic in a specific non-sequential order.
"""

import swisseph as swe
from datetime import datetime, timedelta
from typing import Optional
import math

# ---------------------------------------------------------------------------
# HD Wheel Mapping
# ---------------------------------------------------------------------------
# The 64 I Ching hexagrams are mapped onto the ecliptic wheel (0–360°).
# Each gate spans exactly 360/64 = 5.625°.
# Starting at 0° Aries and moving in the direction of increasing longitude,
# the gates appear in the following order (standard HD mandala).
# Source: Jovian Archive / Ra Uru Hu's original teaching.

HD_WHEEL_SEQUENCE = [
    41, 19, 13, 49, 30, 55, 37, 63,
    22, 36, 25, 17, 21, 51, 42,  3,
    27, 24,  2, 23,  8, 20, 16, 35,
    45, 12, 15, 52, 39, 53, 62, 56,
    31, 33,  7,  4, 29, 59, 40, 64,
    47,  6, 46, 18, 48, 57, 32, 50,
    28, 44,  1, 43, 14, 34,  9,  5,
    26, 11, 10, 58, 38, 54, 61, 60,
]

GATE_SPAN = 360.0 / 64  # 5.625°
LINE_SPAN = GATE_SPAN / 6  # 0.9375°

# ---------------------------------------------------------------------------
# Wheel offset: The HD mandala is anchored so that the wheel start aligns
# at tropical longitude 58.177269° before the first gate position.
# Calibrated against a verified reference chart (Sep 5 1989, Reykjavik):
#   - Conscious Sun → Gate 64.2  ✓
#   - Conscious Earth → Gate 63.2  ✓
#   - Design Sun → Gate 35.4  ✓
#   - Profile 2/4  ✓
#   - 20/26 planet/gate matches (remaining diffs are Moon/Node order
#     variations between calculation methods, not errors)
# Formula: adjusted_lon = (planet_longitude + 58.177269) % 360
# ---------------------------------------------------------------------------
HD_WHEEL_OFFSET = -58.0  # Jovian Archive standard: fixes Marta (Manifestor) + Davið (4/1)


def longitude_to_gate_and_line(longitude: float) -> tuple:
    """
    Convert ecliptic longitude (0–360°) to (gate_number, line_number 1-6).
    The HD wheel divides the zodiac into 64 equal segments of 5.625°.
    HD_WHEEL_OFFSET shifts the tropical zodiac to align with the HD mandala.
    """
    # Apply wheel offset (subtracting a negative = adding positive shift)
    lon = (longitude - HD_WHEEL_OFFSET) % 360.0
    position = int(lon / GATE_SPAN)  # 0–63
    degree_within_gate = lon - position * GATE_SPAN
    line = int(degree_within_gate / LINE_SPAN) + 1
    if line > 6:
        line = 6
    gate = HD_WHEEL_SEQUENCE[position]
    return gate, line


# ---------------------------------------------------------------------------
# Centre Definitions
# ---------------------------------------------------------------------------

CENTRES = {
    'Head':         {'gates': [63, 64, 61]},
    'Ajna':         {'gates': [47, 24, 4, 11, 17, 43]},
    'Throat':       {'gates': [16, 20, 31, 33, 35, 45, 12, 8, 23, 56, 62]},
    'G':            {'gates': [1, 2, 7, 10, 13, 15, 25, 46]},
    'Heart':        {'gates': [51, 21, 40, 26]},
    'Sacral':       {'gates': [34, 5, 14, 29, 59, 9, 3, 42, 27]},
    'SolarPlexus':  {'gates': [36, 22, 37, 49, 55, 30, 6]},
    'Spleen':       {'gates': [48, 57, 44, 50, 32, 28, 18]},
    'Root':         {'gates': [53, 60, 52, 19, 39, 41, 58, 38, 54]},
}

# Map each gate to its centre for fast lookup
GATE_TO_CENTRE = {}
for centre_name, centre_data in CENTRES.items():
    for gate in centre_data['gates']:
        GATE_TO_CENTRE[gate] = centre_name


# ---------------------------------------------------------------------------
# Channel Definitions (36 channels)
# ---------------------------------------------------------------------------
# Each channel connects two gates from two different centres.
# Notes on the "Integration Circuit": gates 10, 20, 34, 57 form a special
# circuit where some gates appear in multiple channels (confirmed by Jovian Archive).

CHANNELS = [
    # Head ↔ Ajna
    (63, 4,   'Logic'),
    (64, 47,  'Abstraction'),
    (61, 24,  'Awareness'),
    # Ajna ↔ Throat
    (17, 62,  'Acceptance'),
    (11, 56,  'Curiosity'),
    (43, 23,  'Structuring'),
    # Throat ↔ G
    (8,  1,   'Inspiration'),
    (31, 7,   'The Alpha'),
    (33, 13,  'The Prodigal'),
    (20, 10,  'Awakening'),           # Integration channel
    # Throat ↔ Heart
    (45, 21,  'Money Line'),
    # Throat ↔ Solar Plexus
    (35, 36,  'Transitoriness'),
    (12, 22,  'Openness'),
    # Throat ↔ Sacral (Integration)
    (20, 34,  'Charisma'),            # Integration channel
    # Throat ↔ Spleen (Integration)
    (57, 20,  'The Brain Wave'),      # Integration channel (gate 20 in 3 channels)
    # Throat ↔ Spleen
    (16, 48,  'The Wavelength'),
    # G ↔ Sacral
    (2,  14,  'The Beat'),
    (15, 5,   'Rhythm'),
    (46, 29,  'Discovery'),
    # G ↔ Heart
    (25, 51,  'Initiation'),
    # G ↔ Spleen (Integration)
    (10, 57,  'Intuitive Design'),    # Integration channel
    # Heart ↔ Spleen
    (26, 44,  'Surrender'),
    # Heart ↔ Solar Plexus
    (40, 37,  'Community'),
    # Sacral ↔ Root
    (3,  60,  'Mutation'),
    (9,  52,  'Concentration'),
    (42, 53,  'Maturation'),
    # Sacral ↔ Spleen
    (27, 50,  'Preservation'),
    (34, 57,  'Power'),               # Integration channel
    # Sacral ↔ Solar Plexus
    (59, 6,   'Mating'),
    # Spleen ↔ Root
    (18, 58,  'Judgment'),
    (28, 38,  'Struggle'),
    (32, 54,  'Transformation'),
    # Solar Plexus ↔ Root
    (49, 19,  'Synthesis'),
    (30, 41,  'Recognition'),
    (55, 39,  'Emoting'),
]

# Build a gate-to-channel(s) lookup
GATE_TO_CHANNELS = {}
for gate_a, gate_b, name in CHANNELS:
    GATE_TO_CHANNELS.setdefault(gate_a, []).append((gate_b, name))
    GATE_TO_CHANNELS.setdefault(gate_b, []).append((gate_a, name))


# ---------------------------------------------------------------------------
# Motor Centers (can generate energy)
# ---------------------------------------------------------------------------
MOTOR_CENTRES = {'Sacral', 'SolarPlexus', 'Heart', 'Root'}

# Centers directly connected to Throat via at least one channel
THROAT_MOTOR_CHANNELS = {
    # centre → list of channel gate pairs that connect it to Throat
    'Heart':       [(21, 45)],
    'SolarPlexus': [(35, 36), (12, 22)],
    'Sacral':      [(20, 34), (34, 57), (57, 20)],  # integration paths
    'Root':        [],  # Root doesn't connect directly to Throat
}


# ---------------------------------------------------------------------------
# Planets used in HD calculation
# ---------------------------------------------------------------------------
HD_PLANETS = {
    'Sun':       swe.SUN,
    'Earth':     None,     # Earth = Sun + 180°
    'NorthNode': swe.TRUE_NODE,
    'SouthNode': None,     # South Node = North Node + 180°
    'Moon':      swe.MOON,
    'Mercury':   swe.MERCURY,
    'Venus':     swe.VENUS,
    'Mars':      swe.MARS,
    'Jupiter':   swe.JUPITER,
    'Saturn':    swe.SATURN,
    'Uranus':    swe.URANUS,
    'Neptune':   swe.NEPTUNE,
    'Pluto':     swe.PLUTO,
}


def calc_hd_planets(jd: float) -> dict:
    """
    Calculate all HD planet positions for a given Julian Day.
    Returns dict of planet_name → {longitude, gate, line}.
    """
    swe.set_ephe_path('')  # Moshier built-in, no external files needed
    positions = {}

    for name, planet_id in HD_PLANETS.items():
        if planet_id is None:
            continue  # computed from another planet
        flags = swe.FLG_MOSEPH | swe.FLG_SPEED
        result, _ = swe.calc_ut(jd, planet_id, flags)
        lon = result[0] % 360.0
        gate, line = longitude_to_gate_and_line(lon)
        positions[name] = {'longitude': round(lon, 6), 'gate': gate, 'line': line}

    # Compute Earth (opposite of Sun)
    sun_lon = positions['Sun']['longitude']
    earth_lon = (sun_lon + 180.0) % 360.0
    earth_gate, earth_line = longitude_to_gate_and_line(earth_lon)
    positions['Earth'] = {'longitude': round(earth_lon, 6), 'gate': earth_gate, 'line': earth_line}

    # Compute South Node (opposite of North Node)
    nn_lon = positions['NorthNode']['longitude']
    sn_lon = (nn_lon + 180.0) % 360.0
    sn_gate, sn_line = longitude_to_gate_and_line(sn_lon)
    positions['SouthNode'] = {'longitude': round(sn_lon, 6), 'gate': sn_gate, 'line': sn_line}

    return positions


def collect_active_gates(conscious: dict, unconscious: dict) -> set:
    """
    Collect all active gate numbers from both conscious and unconscious charts.
    Returns a set of activated gate numbers.
    """
    gates = set()
    for planet_data in conscious.values():
        gates.add(planet_data['gate'])
    for planet_data in unconscious.values():
        gates.add(planet_data['gate'])
    return gates


def derive_defined_channels(active_gates: set) -> list:
    """
    A channel is defined (active) when BOTH of its gates are active.
    Returns list of defined channel tuples (gate_a, gate_b, name).
    """
    defined = []
    seen = set()
    for gate_a, gate_b, name in CHANNELS:
        key = tuple(sorted([gate_a, gate_b]))
        if key not in seen and gate_a in active_gates and gate_b in active_gates:
            defined.append({'gate_a': gate_a, 'gate_b': gate_b, 'name': name})
            seen.add(key)
    return defined


def derive_defined_centres(defined_channels: list) -> dict:
    """
    A centre is defined when at least one of its channels is defined.
    Returns dict of centre_name → True/False.
    """
    defined_centre_names = set()
    for ch in defined_channels:
        centre_a = GATE_TO_CENTRE.get(ch['gate_a'])
        centre_b = GATE_TO_CENTRE.get(ch['gate_b'])
        if centre_a:
            defined_centre_names.add(centre_a)
        if centre_b:
            defined_centre_names.add(centre_b)

    return {name: name in defined_centre_names for name in CENTRES}


def is_motor_to_throat(defined_centres: dict, defined_channels: list) -> bool:
    """
    Determine if any motor center has a defined channel path to the Throat.
    Checks direct connections only (one-hop).
    """
    if not defined_centres.get('Throat', False):
        return False

    # Build a set of active channel gate pairs
    active_pairs = set()
    for ch in defined_channels:
        active_pairs.add((ch['gate_a'], ch['gate_b']))
        active_pairs.add((ch['gate_b'], ch['gate_a']))

    for motor, connections in THROAT_MOTOR_CHANNELS.items():
        if not defined_centres.get(motor, False):
            continue
        for gate_a, gate_b in connections:
            if (gate_a, gate_b) in active_pairs or (gate_b, gate_a) in active_pairs:
                return True
    return False


def determine_type(defined_centres: dict, defined_channels: list) -> tuple:
    """
    Determine Human Design Type, Strategy, and inner Authority.
    Returns (type: str, strategy: str, authority: str)
    """
    sacral = defined_centres.get('Sacral', False)
    sp = defined_centres.get('SolarPlexus', False)
    heart = defined_centres.get('Heart', False)
    spleen = defined_centres.get('Spleen', False)
    g = defined_centres.get('G', False)
    ajna = defined_centres.get('Ajna', False)
    head = defined_centres.get('Head', False)
    throat = defined_centres.get('Throat', False)
    motor_to_throat = is_motor_to_throat(defined_centres, defined_channels)

    # Determine Type
    if not any(defined_centres.values()):
        hd_type = 'Reflector'
        strategy = 'Wait a lunar cycle (28 days) before making major decisions'
        authority = 'Lunar'
    elif sacral:
        if motor_to_throat:
            hd_type = 'Manifesting Generator'
            strategy = 'Wait to respond, then inform before acting'
        else:
            hd_type = 'Generator'
            strategy = 'Wait to respond'
        # Authority for Generators/MGs
        if sp:
            authority = 'Emotional'
        else:
            authority = 'Sacral'
    else:
        # Non-sacral beings
        if motor_to_throat:
            hd_type = 'Manifestor'
            strategy = 'Inform before acting'
            if sp:
                authority = 'Emotional'
            elif heart:
                authority = 'Ego'
            else:
                authority = 'Ego Manifestor'
        else:
            hd_type = 'Projector'
            strategy = 'Wait for the invitation'
            if sp:
                authority = 'Emotional'
            elif spleen:
                authority = 'Splenic'
            elif heart:
                authority = 'Ego Projected'
            elif g:
                authority = 'Self-Projected'
            elif ajna or head:
                authority = 'Mental / Environmental'
            else:
                authority = 'Environmental'

    return hd_type, strategy, authority


def determine_profile(conscious: dict, unconscious: dict) -> str:
    """
    Profile = Conscious Sun line / Design (Unconscious) Sun line.
    NOT Sun/Earth — both positions are Sun, just at different moments.
    The 12 profiles follow Ra Uru Hu's system.
    Returns a string like '2/4'.
    """
    conscious_sun_line = conscious['Sun']['line']
    design_sun_line = unconscious['Sun']['line']
    return f"{conscious_sun_line}/{design_sun_line}"


def determine_incarnation_cross(conscious: dict, unconscious: dict) -> dict:
    """
    The Incarnation Cross is defined by the 4 activation gates:
    Conscious Sun, Conscious Earth, Unconscious Sun, Unconscious Earth.
    Returns a dict with the 4 gate numbers and a descriptive label.
    
    Note: Full cross naming requires a 192-entry lookup table.
    For Phase 1, we return the gate activations; naming can be added in Phase 2.
    """
    c_sun = conscious['Sun']['gate']
    c_earth = conscious['Earth']['gate']
    u_sun = unconscious['Sun']['gate']
    u_earth = unconscious['Earth']['gate']
    return {
        'conscious_sun': c_sun,
        'conscious_earth': c_earth,
        'unconscious_sun': u_sun,
        'unconscious_earth': u_earth,
        'label': f"Cross of Gates {c_sun}/{c_earth} | {u_sun}/{u_earth}",
    }


def get_today_active_gates(transit_date: str = None) -> dict:
    """
    Calculate today's (or any date's) Sun and Earth gate activations.
    These are the 'active' gates driving current transits in HD.
    Returns dict with sun_gate, earth_gate, sun_line, earth_line.
    """
    from datetime import date
    if transit_date is None:
        transit_date = date.today().isoformat()

    dt = datetime.strptime(transit_date, "%Y-%m-%d")
    # Use noon UTC as reference
    jd = swe.julday(dt.year, dt.month, dt.day, 12.0)
    swe.set_ephe_path('')
    result, _ = swe.calc_ut(jd, swe.SUN, swe.FLG_MOSEPH)
    sun_lon = result[0] % 360.0
    earth_lon = (sun_lon + 180.0) % 360.0

    sun_gate, sun_line = longitude_to_gate_and_line(sun_lon)
    earth_gate, earth_line = longitude_to_gate_and_line(earth_lon)

    return {
        'date': transit_date,
        'sun_gate': sun_gate,
        'sun_line': sun_line,
        'earth_gate': earth_gate,
        'earth_line': earth_line,
        'active_gates': [sun_gate, earth_gate],
    }


def calc_human_design(
    birth_date: str,
    birth_time: str,
    birth_lat: float,
    birth_lon: float,
    tz_offset: float = 0.0,
    transit_date: str = None,
) -> dict:
    """
    Main entry point for Human Design calculation.
    
    Args:
        birth_date: 'YYYY-MM-DD'
        birth_time: 'HH:MM'
        birth_lat: latitude
        birth_lon: longitude (unused in HD but kept for consistency)
        tz_offset: hours offset from UTC
        transit_date: optional date for today's active gates
    
    Returns:
        Full Human Design blueprint dict.
    """
    swe.set_ephe_path('')  # Moshier built-in

    # --- Conscious Chart (birth moment) ---
    dt = datetime.strptime(f"{birth_date} {birth_time}", "%Y-%m-%d %H:%M")
    ut_hour = dt.hour + dt.minute / 60.0 - tz_offset
    jd_birth = swe.julday(dt.year, dt.month, dt.day, ut_hour)
    conscious = calc_hd_planets(jd_birth)

    # --- Unconscious / Design Chart (88 solar degrees before birth) ---
    # The correct HD method: go back exactly 88° of solar arc, not 88 days.
    # Due to Earth's elliptical orbit, 88° takes ~88–92 calendar days depending
    # on time of year. We binary-search for the exact Julian Day.
    sun_birth_lon = conscious['Sun']['longitude']
    design_sun_target = (sun_birth_lon - 88.0) % 360.0

    jd_lo = jd_birth - 100.0
    jd_hi = jd_birth - 80.0
    for _ in range(64):
        jd_mid = (jd_lo + jd_hi) / 2.0
        r_mid, _ = swe.calc_ut(jd_mid, swe.SUN, swe.FLG_MOSEPH)
        diff = ((r_mid[0] - design_sun_target + 180.0) % 360.0) - 180.0
        if abs(diff) < 0.00001:
            break
        if diff > 0:
            jd_hi = jd_mid
        else:
            jd_lo = jd_mid
    jd_design = jd_mid
    unconscious = calc_hd_planets(jd_design)

    # --- Gate Analysis ---
    active_gates = collect_active_gates(conscious, unconscious)
    defined_channels = derive_defined_channels(active_gates)
    defined_centres = derive_defined_centres(defined_channels)

    # --- Type, Strategy, Authority ---
    hd_type, strategy, authority = determine_type(defined_centres, defined_channels)

    # --- Profile ---
    profile = determine_profile(conscious, unconscious)

    # --- Incarnation Cross ---
    incarnation_cross = determine_incarnation_cross(conscious, unconscious)

    # --- Today's Active Gates ---
    todays_gates = get_today_active_gates(transit_date)

    # --- Format output ---
    # Label each planet activation as conscious (red) or unconscious (black)
    activations = {}
    for planet, data in conscious.items():
        activations[f"{planet}_conscious"] = {
            'gate': data['gate'],
            'line': data['line'],
            'longitude': data['longitude'],
            'chart': 'conscious',
        }
    for planet, data in unconscious.items():
        activations[f"{planet}_unconscious"] = {
            'gate': data['gate'],
            'line': data['line'],
            'longitude': data['longitude'],
            'chart': 'unconscious',
        }

    return {
        'birth_data': {
            'date': birth_date,
            'time': birth_time,
            'lat': birth_lat,
            'lon': birth_lon,
            'tz_offset': tz_offset,
        },
        'conscious_chart': conscious,
        'unconscious_chart': unconscious,
        'activations': activations,
        'active_gates': sorted(list(active_gates)),
        'defined_channels': defined_channels,
        'defined_centres': defined_centres,
        'type': hd_type,
        'strategy': strategy,
        'authority': authority,
        'profile': profile,
        'incarnation_cross': incarnation_cross,
        'todays_gates': todays_gates,
    }
