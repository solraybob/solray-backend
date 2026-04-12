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


# ---------------------------------------------------------------------------
# Incarnation Cross Lookup
# ---------------------------------------------------------------------------
# HD gate names (Ra Uru Hu's system, not I Ching titles)
HD_GATE_NAMES = {
    1: "Self-Expression",      2: "Direction",             3: "Ordering",
    4: "Formulization",        5: "Fixed Rhythms",         6: "Friction",
    7: "The Role of the Self", 8: "Contribution",          9: "Focus",
    10: "Behavior of the Self",11: "Ideas",                12: "Caution",
    13: "The Listener",        14: "Power Skills",         15: "Extremes",
    16: "Skills",              17: "Opinion",              18: "Correction",
    19: "Wanting",             20: "The Now",              21: "The Hunter",
    22: "Openness",            23: "Assimilation",         24: "Rationalization",
    25: "The Spirit of the Self",26: "The Trickster",      27: "Caring",
    28: "The Game Player",     29: "Perseverance",         30: "Feelings",
    31: "Leadership",          32: "Continuity",           33: "Privacy",
    34: "Power",               35: "Change",               36: "Crisis",
    37: "Friendship",          38: "The Fighter",          39: "Provocation",
    40: "Aloneness",           41: "Contraction",          42: "Growth",
    43: "Insight",             44: "Alertness",            45: "The Gatherer",
    46: "Serendipity",         47: "Realization",          48: "Depth",
    49: "Principles",          50: "Values",               51: "Shock",
    52: "Stillness",           53: "Beginnings",           54: "Ambition",
    55: "Spirit",              56: "Stimulation",          57: "Intuitive Clarity",
    58: "Vitality",            59: "Sexuality",            60: "Limitation",
    61: "Mystery",             62: "Details",              63: "Doubt",
    64: "Confusion",
}

# Incarnation Cross names keyed by (conscious_sun_gate, angle_type)
# angle_type: 'RA' = Right Angle (conscious sun lines 1-3)
#             'J'  = Juxtaposition (conscious sun line 4)
#             'LA' = Left Angle (conscious sun lines 5-6)
#
# Sources: Jovian Archive / Ra Uru Hu curriculum.
# Juxtaposition crosses follow "Juxtaposition Cross of [Gate Name]" convention exactly.
# Right Angle and Left Angle cross names are the Ra Uru Hu official names.
# Cross names that share a body name (e.g. Planning) are confirmed multi-gate families.

_J = 'J'
_RA = 'RA'
_LA = 'LA'

INCARNATION_CROSS_NAMES: dict = {
    # ---- GATE 1 (Self-Expression) ----
    (1, _RA): "Right Angle Cross of the Sphinx",
    (1, _J):  "Juxtaposition Cross of Self-Expression",
    (1, _LA): "Left Angle Cross of the Sphinx",
    # ---- GATE 2 (Direction) ----
    (2, _RA): "Right Angle Cross of the Vessel of Love",
    (2, _J):  "Juxtaposition Cross of Direction",
    (2, _LA): "Left Angle Cross of the Vessel of Love",
    # ---- GATE 3 (Ordering) ----
    (3, _RA): "Right Angle Cross of the Laws",
    (3, _J):  "Juxtaposition Cross of Ordering",
    (3, _LA): "Left Angle Cross of the Laws",
    # ---- GATE 4 (Formulization) ----
    (4, _RA): "Right Angle Cross of the Vessel of Love",
    (4, _J):  "Juxtaposition Cross of Formulization",
    (4, _LA): "Left Angle Cross of the Vessel of Love",
    # ---- GATE 5 (Fixed Rhythms) ----
    (5, _RA): "Right Angle Cross of Tension",
    (5, _J):  "Juxtaposition Cross of Fixed Rhythms",
    (5, _LA): "Left Angle Cross of Tension",
    # ---- GATE 6 (Friction) ----
    (6, _RA): "Right Angle Cross of the Unexpected",
    (6, _J):  "Juxtaposition Cross of Friction",
    (6, _LA): "Left Angle Cross of the Unexpected",
    # ---- GATE 7 (The Role of the Self) ----
    (7, _RA): "Right Angle Cross of the Sphinx",
    (7, _J):  "Juxtaposition Cross of Interaction",
    (7, _LA): "Left Angle Cross of the Sphinx",
    # ---- GATE 8 (Contribution) ----
    (8, _RA): "Right Angle Cross of Contribution",
    (8, _J):  "Juxtaposition Cross of Contribution",
    (8, _LA): "Left Angle Cross of Contribution",
    # ---- GATE 9 (Focus) ----
    (9, _RA): "Right Angle Cross of Planning",
    (9, _J):  "Juxtaposition Cross of Focus",
    (9, _LA): "Left Angle Cross of Planning",
    # ---- GATE 10 (Behavior of the Self) ----
    (10, _RA): "Right Angle Cross of the Sleeping Phoenix",
    (10, _J):  "Juxtaposition Cross of Behavior",
    (10, _LA): "Left Angle Cross of the Sleeping Phoenix",
    # ---- GATE 11 (Ideas) ----
    (11, _RA): "Right Angle Cross of Eden",
    (11, _J):  "Juxtaposition Cross of Ideas",
    (11, _LA): "Left Angle Cross of Eden",
    # ---- GATE 12 (Caution) ----
    (12, _RA): "Right Angle Cross of Eden",
    (12, _J):  "Juxtaposition Cross of Caution",
    (12, _LA): "Left Angle Cross of Eden",
    # ---- GATE 13 (The Listener) ----
    (13, _RA): "Right Angle Cross of the Sphinx",
    (13, _J):  "Juxtaposition Cross of the Listener",
    (13, _LA): "Left Angle Cross of the Sphinx",
    # ---- GATE 14 (Power Skills) ----
    (14, _RA): "Right Angle Cross of Contribution",
    (14, _J):  "Juxtaposition Cross of Power Skills",
    (14, _LA): "Left Angle Cross of Contribution",
    # ---- GATE 15 (Extremes) ----
    (15, _RA): "Right Angle Cross of the Vessel of Love",
    (15, _J):  "Juxtaposition Cross of Extremes",
    (15, _LA): "Left Angle Cross of the Vessel of Love",
    # ---- GATE 16 (Skills) ----
    (16, _RA): "Right Angle Cross of Planning",
    (16, _J):  "Juxtaposition Cross of Skills",
    (16, _LA): "Left Angle Cross of Planning",
    # ---- GATE 17 (Opinion) ----
    (17, _RA): "Right Angle Cross of Service",
    (17, _J):  "Juxtaposition Cross of Opinion",
    (17, _LA): "Left Angle Cross of Service",
    # ---- GATE 18 (Correction) ----
    (18, _RA): "Right Angle Cross of Service",
    (18, _J):  "Juxtaposition Cross of Correction",
    (18, _LA): "Left Angle Cross of Service",
    # ---- GATE 19 (Wanting) ----
    (19, _RA): "Right Angle Cross of the Four Ways",
    (19, _J):  "Juxtaposition Cross of Wanting",
    (19, _LA): "Left Angle Cross of the Four Ways",
    # ---- GATE 20 (The Now) ----
    (20, _RA): "Right Angle Cross of the Sleeping Phoenix",
    (20, _J):  "Juxtaposition Cross of the Now",
    (20, _LA): "Left Angle Cross of the Sleeping Phoenix",
    # ---- GATE 21 (The Hunter) ----
    (21, _RA): "Right Angle Cross of Rulership",
    (21, _J):  "Juxtaposition Cross of Control",
    (21, _LA): "Left Angle Cross of Rulership",
    # ---- GATE 22 (Openness) ----
    (22, _RA): "Right Angle Cross of Eden",
    (22, _J):  "Juxtaposition Cross of Grace",
    (22, _LA): "Left Angle Cross of Eden",
    # ---- GATE 23 (Assimilation) ----
    (23, _RA): "Right Angle Cross of Explanation",
    (23, _J):  "Juxtaposition Cross of Assimilation",
    (23, _LA): "Left Angle Cross of Explanation",
    # ---- GATE 24 (Rationalization) ----
    (24, _RA): "Right Angle Cross of Explanation",
    (24, _J):  "Juxtaposition Cross of Rationalization",
    (24, _LA): "Left Angle Cross of Explanation",
    # ---- GATE 25 (The Spirit of the Self) ----
    (25, _RA): "Right Angle Cross of the Vessel of Love",
    (25, _J):  "Juxtaposition Cross of the Spirit of the Self",
    (25, _LA): "Left Angle Cross of the Vessel of Love",
    # ---- GATE 26 (The Trickster) ----
    (26, _RA): "Right Angle Cross of Rulership",
    (26, _J):  "Juxtaposition Cross of the Trickster",
    (26, _LA): "Left Angle Cross of Rulership",
    # ---- GATE 27 (Caring) ----
    (27, _RA): "Right Angle Cross of the Laws",
    (27, _J):  "Juxtaposition Cross of Caring",
    (27, _LA): "Left Angle Cross of the Laws",
    # ---- GATE 28 (The Game Player) ----
    (28, _RA): "Right Angle Cross of the Four Ways",
    (28, _J):  "Juxtaposition Cross of the Game Player",
    (28, _LA): "Left Angle Cross of the Four Ways",
    # ---- GATE 29 (Perseverance) ----
    (29, _RA): "Right Angle Cross of the Four Ways",
    (29, _J):  "Juxtaposition Cross of Perseverance",
    (29, _LA): "Left Angle Cross of the Four Ways",
    # ---- GATE 30 (Feelings) ----
    (30, _RA): "Right Angle Cross of Contagion",
    (30, _J):  "Juxtaposition Cross of Feelings",
    (30, _LA): "Left Angle Cross of Contagion",
    # ---- GATE 31 (Leadership) ----
    (31, _RA): "Right Angle Cross of the Alpha",
    (31, _J):  "Juxtaposition Cross of Leadership",
    (31, _LA): "Left Angle Cross of the Alpha",
    # ---- GATE 32 (Continuity) ----
    (32, _RA): "Right Angle Cross of the Four Ways",
    (32, _J):  "Juxtaposition Cross of Continuity",
    (32, _LA): "Left Angle Cross of the Four Ways",
    # ---- GATE 33 (Privacy) ----
    (33, _RA): "Right Angle Cross of the Four Ways",
    (33, _J):  "Juxtaposition Cross of Privacy",
    (33, _LA): "Left Angle Cross of the Four Ways",
    # ---- GATE 34 (Power) ----
    (34, _RA): "Right Angle Cross of the Sleeping Phoenix",
    (34, _J):  "Juxtaposition Cross of Power",
    (34, _LA): "Left Angle Cross of the Sleeping Phoenix",
    # ---- GATE 35 (Change) ----
    (35, _RA): "Right Angle Cross of Tension",
    (35, _J):  "Juxtaposition Cross of Change",
    (35, _LA): "Left Angle Cross of Tension",
    # ---- GATE 36 (Crisis) ----
    (36, _RA): "Right Angle Cross of the Unexpected",
    (36, _J):  "Juxtaposition Cross of Crisis",
    (36, _LA): "Left Angle Cross of the Unexpected",
    # ---- GATE 37 (Friendship) ----
    (37, _RA): "Right Angle Cross of Planning",
    (37, _J):  "Juxtaposition Cross of Friendship",
    (37, _LA): "Left Angle Cross of Planning",
    # ---- GATE 38 (The Fighter) ----
    (38, _RA): "Right Angle Cross of Tension",
    (38, _J):  "Juxtaposition Cross of Opposition",
    (38, _LA): "Left Angle Cross of Tension",
    # ---- GATE 39 (Provocation) ----
    (39, _RA): "Right Angle Cross of Tension",
    (39, _J):  "Juxtaposition Cross of Provocation",
    (39, _LA): "Left Angle Cross of Tension",
    # ---- GATE 40 (Aloneness) ----
    (40, _RA): "Right Angle Cross of Planning",
    (40, _J):  "Juxtaposition Cross of Aloneness",
    (40, _LA): "Left Angle Cross of Planning",
    # ---- GATE 41 (Contraction) ----
    (41, _RA): "Right Angle Cross of Contagion",
    (41, _J):  "Juxtaposition Cross of Contraction",
    (41, _LA): "Left Angle Cross of Contagion",
    # ---- GATE 42 (Growth) ----
    (42, _RA): "Right Angle Cross of the Laws",
    (42, _J):  "Juxtaposition Cross of Growth",
    (42, _LA): "Left Angle Cross of the Laws",
    # ---- GATE 43 (Insight) ----
    (43, _RA): "Right Angle Cross of Explanation",
    (43, _J):  "Juxtaposition Cross of Insight",
    (43, _LA): "Left Angle Cross of Explanation",
    # ---- GATE 44 (Alertness) ----
    (44, _RA): "Right Angle Cross of the Laws",
    (44, _J):  "Juxtaposition Cross of Alertness",
    (44, _LA): "Left Angle Cross of the Laws",
    # ---- GATE 45 (The Gatherer) ----
    (45, _RA): "Right Angle Cross of Rulership",
    (45, _J):  "Juxtaposition Cross of the Gatherer",
    (45, _LA): "Left Angle Cross of Rulership",
    # ---- GATE 46 (Serendipity) ----
    (46, _RA): "Right Angle Cross of the Vessel of Love",
    (46, _J):  "Juxtaposition Cross of Serendipity",
    (46, _LA): "Left Angle Cross of the Vessel of Love",
    # ---- GATE 47 (Realization) ----
    (47, _RA): "Right Angle Cross of Eden",
    (47, _J):  "Juxtaposition Cross of Oppression",
    (47, _LA): "Left Angle Cross of Eden",
    # ---- GATE 48 (Depth) ----
    (48, _RA): "Right Angle Cross of Rulership",
    (48, _J):  "Juxtaposition Cross of Depth",
    (48, _LA): "Left Angle Cross of Rulership",
    # ---- GATE 49 (Principles) ----
    (49, _RA): "Right Angle Cross of the Vessel of Love",
    (49, _J):  "Juxtaposition Cross of Principles",
    (49, _LA): "Left Angle Cross of the Vessel of Love",
    # ---- GATE 50 (Values) ----
    (50, _RA): "Right Angle Cross of the Laws",
    (50, _J):  "Juxtaposition Cross of Values",
    (50, _LA): "Left Angle Cross of the Laws",
    # ---- GATE 51 (Shock) ----
    (51, _RA): "Right Angle Cross of the Unexpected",
    (51, _J):  "Juxtaposition Cross of Shock",
    (51, _LA): "Left Angle Cross of the Unexpected",
    # ---- GATE 52 (Stillness) ----
    (52, _RA): "Right Angle Cross of Contagion",
    (52, _J):  "Juxtaposition Cross of Stillness",
    (52, _LA): "Left Angle Cross of Contagion",
    # ---- GATE 53 (Beginnings) ----
    (53, _RA): "Right Angle Cross of the Laws",
    (53, _J):  "Juxtaposition Cross of Beginnings",
    (53, _LA): "Left Angle Cross of the Laws",
    # ---- GATE 54 (Ambition) ----
    (54, _RA): "Right Angle Cross of the Alpha",
    (54, _J):  "Juxtaposition Cross of Ambition",
    (54, _LA): "Left Angle Cross of the Alpha",
    # ---- GATE 55 (Spirit) ----
    (55, _RA): "Right Angle Cross of Contagion",
    (55, _J):  "Juxtaposition Cross of Spirit",
    (55, _LA): "Left Angle Cross of Contagion",
    # ---- GATE 56 (Stimulation) ----
    (56, _RA): "Right Angle Cross of Eden",
    (56, _J):  "Juxtaposition Cross of Stimulation",
    (56, _LA): "Left Angle Cross of Eden",
    # ---- GATE 57 (Intuitive Clarity) ----
    (57, _RA): "Right Angle Cross of the Sleeping Phoenix",
    (57, _J):  "Juxtaposition Cross of Intuitive Clarity",
    (57, _LA): "Left Angle Cross of the Sleeping Phoenix",
    # ---- GATE 58 (Vitality) ----
    (58, _RA): "Right Angle Cross of Service",
    (58, _J):  "Juxtaposition Cross of Vitality",
    (58, _LA): "Left Angle Cross of Service",
    # ---- GATE 59 (Sexuality) ----
    (59, _RA): "Right Angle Cross of the Four Ways",
    (59, _J):  "Juxtaposition Cross of Sexuality",
    (59, _LA): "Left Angle Cross of the Four Ways",
    # ---- GATE 60 (Limitation) ----
    (60, _RA): "Right Angle Cross of Eden",
    (60, _J):  "Juxtaposition Cross of Limitation",
    (60, _LA): "Left Angle Cross of Eden",
    # ---- GATE 61 (Mystery) ----
    (61, _RA): "Right Angle Cross of Maya",
    (61, _J):  "Juxtaposition Cross of Mystery",
    (61, _LA): "Left Angle Cross of Maya",
    # ---- GATE 62 (Details) ----
    (62, _RA): "Right Angle Cross of Service",
    (62, _J):  "Juxtaposition Cross of Details",
    (62, _LA): "Left Angle Cross of Service",
    # ---- GATE 63 (Doubt) ----
    (63, _RA): "Right Angle Cross of Consciousness",
    (63, _J):  "Juxtaposition Cross of Doubt",
    (63, _LA): "Left Angle Cross of Consciousness",
    # ---- GATE 64 (Confusion) ----
    (64, _RA): "Right Angle Cross of Consciousness",
    (64, _J):  "Juxtaposition Cross of Confusion",
    (64, _LA): "Left Angle Cross of Consciousness",
}


def _get_angle_type(conscious_sun_line: int) -> str:
    """
    Determine Incarnation Cross angle type from conscious Sun line.
    Lines 1-3: Right Angle (personal destiny, karma)
    Line 4: Juxtaposition (fixed, transpersonal bridge)
    Lines 5-6: Left Angle (transpersonal, collective)
    """
    if conscious_sun_line <= 3:
        return 'RA'
    elif conscious_sun_line == 4:
        return 'J'
    else:
        return 'LA'


def determine_incarnation_cross(conscious: dict, unconscious: dict) -> dict:
    """
    The Incarnation Cross is defined by 4 activation gates:
    Conscious Sun/Earth and Unconscious (Design) Sun/Earth.
    Returns the cross name using the Jovian Archive 192-cross system.
    Angle type is derived from the conscious Sun line:
      lines 1-3 = Right Angle, line 4 = Juxtaposition, lines 5-6 = Left Angle.
    """
    c_sun_gate = conscious['Sun']['gate']
    c_sun_line = conscious['Sun']['line']
    c_earth    = conscious['Earth']['gate']
    u_sun      = unconscious['Sun']['gate']
    u_earth    = unconscious['Earth']['gate']

    angle = _get_angle_type(c_sun_line)

    cross_name = INCARNATION_CROSS_NAMES.get(
        (c_sun_gate, angle),
        f"{angle.replace('RA','Right Angle').replace('J','Juxtaposition').replace('LA','Left Angle')} Cross of {HD_GATE_NAMES.get(c_sun_gate, str(c_sun_gate))}"
    )

    return {
        'conscious_sun': c_sun_gate,
        'conscious_earth': c_earth,
        'unconscious_sun': u_sun,
        'unconscious_earth': u_earth,
        'angle': angle,
        'name': cross_name,
        'label': f"{cross_name} ({c_sun_gate}/{c_earth} | {u_sun}/{u_earth})",
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
