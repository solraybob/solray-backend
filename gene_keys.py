"""
gene_keys.py — Gene Keys Engine for Solray AI

Maps Human Design gate activations to Gene Keys and returns
Shadow / Gift / Siddhi triads for each key.

Data source: Richard Rudd's "Gene Keys" (2009, 2013). 
All 64 Gene Keys with their complete Shadow/Gift/Siddhi spectrum.
"""

# ---------------------------------------------------------------------------
# Complete Gene Keys Dataset (all 64 keys)
# ---------------------------------------------------------------------------

GENE_KEYS = {
    1:  {'shadow': 'Entropy',        'gift': 'Freshness',       'siddhi': 'Beauty'},
    2:  {'shadow': 'Dislocation',    'gift': 'Orientation',     'siddhi': 'Unity'},
    3:  {'shadow': 'Chaos',          'gift': 'Innovation',      'siddhi': 'Innocence'},
    4:  {'shadow': 'Intolerance',    'gift': 'Understanding',   'siddhi': 'Forgiveness'},
    5:  {'shadow': 'Impatience',     'gift': 'Patience',        'siddhi': 'Timelessness'},
    6:  {'shadow': 'Conflict',       'gift': 'Diplomacy',       'siddhi': 'Peace'},
    7:  {'shadow': 'Division',       'gift': 'Guidance',        'siddhi': 'Virtue'},
    8:  {'shadow': 'Mediocrity',     'gift': 'Style',           'siddhi': 'Exquisiteness'},
    9:  {'shadow': 'Inertia',        'gift': 'Determination',   'siddhi': 'Invincibility'},
    10: {'shadow': 'Self-Obsession', 'gift': 'Naturalness',     'siddhi': 'Being'},
    11: {'shadow': 'Obscurity',      'gift': 'Idealism',        'siddhi': 'Light'},
    12: {'shadow': 'Vanity',         'gift': 'Discrimination',  'siddhi': 'Purity'},
    13: {'shadow': 'Discord',        'gift': 'Discernment',     'siddhi': 'Empathy'},
    14: {'shadow': 'Compromise',     'gift': 'Competence',      'siddhi': 'Bounteousness'},
    15: {'shadow': 'Dullness',       'gift': 'Magnetism',       'siddhi': 'Florescence'},
    16: {'shadow': 'Indifference',   'gift': 'Versatility',     'siddhi': 'Mastery'},
    17: {'shadow': 'Opinion',        'gift': 'Far-Sightedness', 'siddhi': 'Omniscience'},
    18: {'shadow': 'Judgment',       'gift': 'Integrity',       'siddhi': 'Perfection'},
    19: {'shadow': 'Co-Dependence',  'gift': 'Sensitivity',     'siddhi': 'Sacrifice'},
    20: {'shadow': 'Superficiality', 'gift': 'Self-Assurance',  'siddhi': 'Presence'},
    21: {'shadow': 'Control',        'gift': 'Authority',       'siddhi': 'Valour'},
    22: {'shadow': 'Dishonour',      'gift': 'Graciousness',    'siddhi': 'Grace'},
    23: {'shadow': 'Complexity',     'gift': 'Simplicity',      'siddhi': 'Quintessence'},
    24: {'shadow': 'Addiction',      'gift': 'Invention',       'siddhi': 'Silence'},
    25: {'shadow': 'Constriction',   'gift': 'Acceptance',      'siddhi': 'Universal Love'},
    26: {'shadow': 'Pride',          'gift': 'Artfulness',      'siddhi': 'Invisibility'},
    27: {'shadow': 'Selfishness',    'gift': 'Altruism',        'siddhi': 'Selflessness'},
    28: {'shadow': 'Purposelessness','gift': 'Totality',        'siddhi': 'Immortality'},
    29: {'shadow': 'Half-Heartedness','gift': 'Commitment',     'siddhi': 'Devotion'},
    30: {'shadow': 'Desire',         'gift': 'Lightness',       'siddhi': 'Rapture'},
    31: {'shadow': 'Arrogance',      'gift': 'Leadership',      'siddhi': 'Humility'},
    32: {'shadow': 'Failure',        'gift': 'Preservation',    'siddhi': 'Veneration'},
    33: {'shadow': 'Forgetting',     'gift': 'Mindfulness',     'siddhi': 'Revelation'},
    34: {'shadow': 'Force',          'gift': 'Strength',        'siddhi': 'Majesty'},
    35: {'shadow': 'Hunger',         'gift': 'Adventure',       'siddhi': 'Boundlessness'},
    36: {'shadow': 'Turbulence',     'gift': 'Humanity',        'siddhi': 'Compassion'},
    37: {'shadow': 'Weakness',       'gift': 'Equality',        'siddhi': 'Tenderness'},
    38: {'shadow': 'Struggle',       'gift': 'Perseverance',    'siddhi': 'Honour'},
    39: {'shadow': 'Provocation',    'gift': 'Dynamism',        'siddhi': 'Liberation'},
    40: {'shadow': 'Exhaustion',     'gift': 'Resolve',         'siddhi': 'Divine Will'},
    41: {'shadow': 'Fantasy',        'gift': 'Anticipation',    'siddhi': 'Emanation'},
    42: {'shadow': 'Expectation',    'gift': 'Detachment',      'siddhi': 'Celebration'},
    43: {'shadow': 'Deafness',       'gift': 'Insight',         'siddhi': 'Epiphany'},
    44: {'shadow': 'Interference',   'gift': 'Teamwork',        'siddhi': 'Synarchy'},
    45: {'shadow': 'Dominance',      'gift': 'Synthesis',       'siddhi': 'Communion'},
    46: {'shadow': 'Seriousness',    'gift': 'Delight',         'siddhi': 'Ecstasy'},
    47: {'shadow': 'Oppression',     'gift': 'Transmutation',   'siddhi': 'Transfiguration'},
    48: {'shadow': 'Inadequacy',     'gift': 'Resourcefulness', 'siddhi': 'Wisdom'},
    49: {'shadow': 'Reaction',       'gift': 'Revolution',      'siddhi': 'Rebirth'},
    50: {'shadow': 'Corruption',     'gift': 'Equilibrium',     'siddhi': 'Harmony'},
    51: {'shadow': 'Agitation',      'gift': 'Initiative',      'siddhi': 'Awakening'},
    52: {'shadow': 'Stress',         'gift': 'Restraint',       'siddhi': 'Stillness'},
    53: {'shadow': 'Immaturity',     'gift': 'Expansion',       'siddhi': 'Superabundance'},
    54: {'shadow': 'Greed',          'gift': 'Aspiration',      'siddhi': 'Ascension'},
    55: {'shadow': 'Victimization',  'gift': 'Freedom',         'siddhi': 'Freedom'},
    56: {'shadow': 'Distraction',    'gift': 'Enrichment',      'siddhi': 'Intoxication'},
    57: {'shadow': 'Unease',         'gift': 'Intuition',       'siddhi': 'Clarity'},
    58: {'shadow': 'Dissatisfaction','gift': 'Vitality',        'siddhi': 'Bliss'},
    59: {'shadow': 'Dishonesty',     'gift': 'Intimacy',        'siddhi': 'Transparency'},
    60: {'shadow': 'Limitation',     'gift': 'Realism',         'siddhi': 'Justice'},
    61: {'shadow': 'Psychosis',      'gift': 'Inspiration',     'siddhi': 'Sanctity'},
    62: {'shadow': 'Intellectualism','gift': 'Precision',       'siddhi': 'Impeccability'},
    63: {'shadow': 'Doubt',          'gift': 'Inquiry',         'siddhi': 'Truth'},
    64: {'shadow': 'Confusion',      'gift': 'Imagination',     'siddhi': 'Illumination'},
}

# Validate completeness
assert len(GENE_KEYS) == 64, f"Gene Keys dataset incomplete: {len(GENE_KEYS)} entries"


def get_gene_key(gate: int) -> dict:
    """
    Get Gene Key data for a given gate number.
    Gates are 1:1 mapped to Gene Keys (gate 1 = Gene Key 1, etc.).
    """
    if gate < 1 or gate > 64:
        raise ValueError(f"Gate must be 1–64, got {gate}")
    gk = GENE_KEYS[gate]
    return {
        'gate': gate,
        'gene_key': gate,
        'shadow': gk['shadow'],
        'gift': gk['gift'],
        'siddhi': gk['siddhi'],
    }


def get_gene_keys_for_gates(active_gates: list) -> dict:
    """
    Get Gene Key data for all given gate activations.
    Returns dict of gate_number → gene_key_data.
    """
    result = {}
    for gate in active_gates:
        if 1 <= gate <= 64:
            result[gate] = get_gene_key(gate)
    return result


def get_todays_gene_keys(sun_gate: int, earth_gate: int) -> dict:
    """
    Get the Gene Keys active today based on Sun and Earth gate positions.
    These are the daily evolutionary themes.
    """
    sun_gk = get_gene_key(sun_gate)
    earth_gk = get_gene_key(earth_gate)

    return {
        'sun_gate': sun_gate,
        'earth_gate': earth_gate,
        'sun_gene_key': sun_gk,
        'earth_gene_key': earth_gk,
        'daily_theme': {
            'shadow': f"{sun_gk['shadow']} (Sun) | {earth_gk['shadow']} (Earth)",
            'gift': f"{sun_gk['gift']} (Sun) | {earth_gk['gift']} (Earth)",
            'siddhi': f"{sun_gk['siddhi']} (Sun) | {earth_gk['siddhi']} (Earth)",
        }
    }


def get_hologenetic_profile(conscious: dict, unconscious: dict) -> dict:
    """
    Compute the Gene Keys Hologenetic Profile spheres from birth chart data.

    Activation Sequence (4 core spheres — Richard Rudd's system):
      Life's Work  = Conscious (Personality) Sun gate
      Evolution    = Conscious (Personality) Earth gate
      Radiance     = Unconscious (Design) Sun gate
      Purpose      = Unconscious (Design) Earth gate

    Venus Sequence (3 relational spheres):
      Attraction   = Conscious Venus gate
      IQ           = Conscious South Node gate
      EQ           = Conscious Moon gate

    Reference: Richard Rudd, "Gene Keys" (2013), Hologenetic Profile system.
    """
    def _sphere(planet: str, chart: dict):
        gate = chart.get(planet, {}).get('gate') if chart else None
        if not gate or gate not in GENE_KEYS:
            return None
        gk = GENE_KEYS[gate]
        return {'gate': gate, 'shadow': gk['shadow'], 'gift': gk['gift'], 'siddhi': gk['siddhi']}

    return {
        # Activation Sequence
        'lifes_work':  _sphere('Sun',       conscious),
        'evolution':   _sphere('Earth',     conscious),
        'radiance':    _sphere('Sun',       unconscious),   # Design Sun, NOT Conscious Moon
        'purpose':     _sphere('Earth',     unconscious),   # Design Earth
        # Venus Sequence
        'attraction':  _sphere('Venus',     conscious),
        'iq':          _sphere('SouthNode', conscious),
        'eq':          _sphere('Moon',      conscious),
    }


def get_full_gene_keys_profile(
    active_gates: list,
    todays_gates: dict = None,
    conscious: dict = None,
    unconscious: dict = None,
) -> dict:
    """
    Main entry point for Gene Keys engine.

    Args:
        active_gates:  list of active gate numbers from Human Design chart
        todays_gates:  dict from human_design.get_today_active_gates()
        conscious:     conscious_chart dict from Human Design calculation
        unconscious:   unconscious_chart dict from Human Design calculation

    Returns:
        Full Gene Keys profile with Hologenetic Profile spheres,
        natal activations, and today's active keys.
    """
    natal_gene_keys = get_gene_keys_for_gates(active_gates)

    result = {
        'natal_gene_keys': natal_gene_keys,
        'natal_active_count': len(natal_gene_keys),
    }

    # Hologenetic Profile spheres (requires conscious/unconscious chart data)
    if conscious and unconscious:
        result['hologenetic_profile'] = get_hologenetic_profile(conscious, unconscious)
        # Flatten the 4 Activation Sequence spheres to top-level for easy access
        hp = result['hologenetic_profile']
        for key in ('lifes_work', 'evolution', 'radiance', 'purpose', 'attraction', 'iq', 'eq'):
            if hp.get(key):
                result[key] = hp[key]

    if todays_gates:
        today_sun = todays_gates.get('sun_gate')
        today_earth = todays_gates.get('earth_gate')
        if today_sun and today_earth:
            result['todays_gene_keys'] = get_todays_gene_keys(today_sun, today_earth)
            # Check if today's gates coincide with natal activations
            result['resonance'] = []
            if today_sun in natal_gene_keys:
                result['resonance'].append({
                    'type': 'Sun resonance',
                    'gate': today_sun,
                    'message': f"Today's Sun gate {today_sun} activates your natal Gene Key {today_sun}: {GENE_KEYS[today_sun]['gift']}"
                })
            if today_earth in natal_gene_keys:
                result['resonance'].append({
                    'type': 'Earth resonance',
                    'gate': today_earth,
                    'message': f"Today's Earth gate {today_earth} activates your natal Gene Key {today_earth}: {GENE_KEYS[today_earth]['gift']}"
                })

    return result
