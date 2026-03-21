"""
test_engines.py — Test Suite for Solray AI Phase 1 Engines

Test person: born 1990-06-15 at 14:30 in London (BST = UTC+1)
Coordinates: 51.5074° N, -0.1278° E
"""

import sys
import json
from datetime import date

# Test configuration
TEST_BIRTH_DATE = "1990-06-15"
TEST_BIRTH_TIME = "14:30"
TEST_BIRTH_CITY = "London"
TEST_BIRTH_LAT  = 51.5074
TEST_BIRTH_LON  = -0.1278
TEST_TZ_OFFSET  = 1.0  # BST (British Summer Time = UTC+1)
TODAY = date.today().isoformat()

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


def separator(title=""):
    print("\n" + "=" * 60)
    if title:
        print(f"  {title}")
        print("=" * 60)


def check(condition: bool, message: str):
    icon = PASS if condition else FAIL
    print(f"  {icon}  {message}")
    return condition


# ============================================================
# Test 1: Astrology Engine
# ============================================================

def test_astrology():
    separator("TEST 1: ASTROLOGY ENGINE")
    from astrology import get_natal_chart, get_transits_and_aspects

    print("\n  Computing natal chart for 1990-06-15 14:30 London BST...")
    chart = get_natal_chart(
        birth_date=TEST_BIRTH_DATE,
        birth_time=TEST_BIRTH_TIME,
        birth_city=TEST_BIRTH_CITY,
        birth_lat=TEST_BIRTH_LAT,
        birth_lon=TEST_BIRTH_LON,
        tz_offset=TEST_TZ_OFFSET,
    )

    planets = chart['planets']
    asc = chart['ascendant']
    houses = chart['house_cusps']

    # --- Sanity checks ---
    all_pass = True

    # Sun should be in Gemini (June 15)
    all_pass &= check(planets['Sun']['sign'] == 'Gemini',
        f"Sun in Gemini (got: {planets['Sun']['sign']})")

    # Moon should be in any valid sign
    moon_sign = planets['Moon']['sign']
    valid_signs = ['Aries','Taurus','Gemini','Cancer','Leo','Virgo',
                   'Libra','Scorpio','Sagittarius','Capricorn','Aquarius','Pisces']
    all_pass &= check(moon_sign in valid_signs,
        f"Moon in valid sign (got: {moon_sign})")

    # All planets present
    for planet in ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter',
                   'Saturn', 'Uranus', 'Neptune', 'Pluto', 'NorthNode', 'Chiron']:
        present = planet in planets
        all_pass &= check(present, f"Planet present: {planet}")

    # Houses assigned (1–12) — skip planets that failed to compute (e.g. Chiron without ephe files)
    for planet_name, p_data in planets.items():
        if p_data.get('longitude') is None:
            print(f"    ⚠️   {planet_name}: no ephemeris data (requires external .se1 files)")
            continue
        house = p_data.get('house')
        valid = house is not None and 1 <= house <= 12
        all_pass &= check(valid, f"{planet_name} in valid house (got: {house})")

    # Ascendant sign exists
    all_pass &= check(asc['sign'] in [
        'Aries','Taurus','Gemini','Cancer','Leo','Virgo',
        'Libra','Scorpio','Sagittarius','Capricorn','Aquarius','Pisces'],
        f"Ascendant sign valid: {asc['sign']}")

    # 12 house cusps
    all_pass &= check(len(houses) == 12, f"12 house cusps present (got: {len(houses)})")

    # --- Print natal chart ---
    print("\n  NATAL CHART:")
    print(f"  Ascendant: {asc['sign']} {asc['degree']:.2f}°")
    print(f"  MC: {chart['mc']['sign']} {chart['mc']['degree']:.2f}°")
    print()
    for name, p in sorted(planets.items(), key=lambda x: (x[1]['longitude'] or 0)):
        if p.get('longitude') is None:
            print(f"    {name:<12} {'[no data]':<14} (requires external ephemeris)")
            continue
        retro = " ℞" if p['retrograde'] else ""
        house_str = f"House {p['house']:2d}" if p['house'] else "House  ?"
        print(f"    {name:<12} {p['sign']:<14} {p['degree']:6.2f}°  {house_str}{retro}")

    # --- Transits ---
    print(f"\n  TRANSITS for {TODAY}:")
    transits = get_transits_and_aspects(chart, TODAY)
    t_planets = transits['transit_planets']
    for name, p in sorted(t_planets.items(), key=lambda x: (x[1]['longitude'] or 0)):
        if p.get('longitude') is None:
            print(f"    {name:<12} [no data]")
            continue
        retro = " ℞" if p['retrograde'] else ""
        print(f"    {name:<12} {p['sign']:<14} {p['degree']:6.2f}°{retro}")

    # --- Aspects ---
    aspects = transits['aspects']
    all_pass &= check(len(aspects) > 0, f"Aspects found: {len(aspects)}")
    print(f"\n  ASPECTS (top 10 tightest):")
    for asp in aspects[:10]:
        print(f"    Transit {asp['transit_planet']:<10} {asp['aspect']:<12} Natal {asp['natal_planet']:<10} "
              f"orb {asp['orb']:.2f}° (H{asp['natal_house']})")

    return all_pass


# ============================================================
# Test 2: Human Design Engine
# ============================================================

def test_human_design():
    separator("TEST 2: HUMAN DESIGN ENGINE")
    from human_design import calc_human_design, longitude_to_gate_and_line, GATE_TO_CENTRE

    print("\n  Computing Human Design chart...")
    hd = calc_human_design(
        birth_date=TEST_BIRTH_DATE,
        birth_time=TEST_BIRTH_TIME,
        birth_lat=TEST_BIRTH_LAT,
        birth_lon=TEST_BIRTH_LON,
        tz_offset=TEST_TZ_OFFSET,
        transit_date=TODAY,
    )

    all_pass = True

    # --- Type validation ---
    valid_types = {'Generator', 'Manifesting Generator', 'Projector', 'Manifestor', 'Reflector'}
    all_pass &= check(hd['type'] in valid_types, f"HD Type valid: {hd['type']}")

    # --- Profile format ---
    profile_parts = hd['profile'].split('/')
    all_pass &= check(
        len(profile_parts) == 2 and
        all(p.isdigit() and 1 <= int(p) <= 6 for p in profile_parts),
        f"Profile format valid: {hd['profile']}"
    )

    # --- Active gates ---
    gates = hd['active_gates']
    all_pass &= check(len(gates) > 0, f"Active gates: {len(gates)} gates defined")
    all_pass &= check(all(1 <= g <= 64 for g in gates), "All active gates in valid range (1–64)")

    # --- Centres ---
    from human_design import CENTRES
    all_pass &= check(len(hd['defined_centres']) == 9, f"9 centres present (got: {len(hd['defined_centres'])})")

    # --- Print Human Design data ---
    print(f"\n  TYPE:      {hd['type']}")
    print(f"  STRATEGY:  {hd['strategy']}")
    print(f"  AUTHORITY: {hd['authority']}")
    print(f"  PROFILE:   {hd['profile']}")
    print(f"  INCARNATION CROSS: {hd['incarnation_cross']['label']}")

    print(f"\n  DEFINED CENTRES:")
    for centre, defined in hd['defined_centres'].items():
        status = "Defined  ●" if defined else "Undefined ○"
        print(f"    {centre:<15} {status}")

    print(f"\n  DEFINED CHANNELS ({len(hd['defined_channels'])}):")
    for ch in hd['defined_channels']:
        print(f"    Gate {ch['gate_a']:2d}–{ch['gate_b']:2d}  {ch['name']}")

    print(f"\n  ACTIVE GATES: {sorted(hd['active_gates'])}")

    print(f"\n  CONSCIOUS CHART (Personality / Black):")
    for planet, data in sorted(hd['conscious_chart'].items()):
        print(f"    {planet:<12} Gate {data['gate']:2d}.{data['line']}  ({data['longitude']:.2f}°)")

    print(f"\n  UNCONSCIOUS CHART (Design / Red):")
    for planet, data in sorted(hd['unconscious_chart'].items()):
        print(f"    {planet:<12} Gate {data['gate']:2d}.{data['line']}  ({data['longitude']:.2f}°)")

    print(f"\n  TODAY'S GATES ({TODAY}):")
    tg = hd['todays_gates']
    print(f"    Sun Gate:   {tg['sun_gate']}.{tg['sun_line']}")
    print(f"    Earth Gate: {tg['earth_gate']}.{tg['earth_line']}")

    # Validate gate-to-centre mapping is consistent
    for gate in hd['active_gates']:
        centre = GATE_TO_CENTRE.get(gate)
        all_pass &= check(centre is not None, f"Gate {gate} has centre mapping: {centre}")

    return all_pass


# ============================================================
# Test 3: Gene Keys Engine
# ============================================================

def test_gene_keys():
    separator("TEST 3: GENE KEYS ENGINE")
    from gene_keys import GENE_KEYS, get_gene_key, get_full_gene_keys_profile
    from human_design import calc_human_design, get_today_active_gates

    # Validate dataset completeness
    all_pass = True
    all_pass &= check(len(GENE_KEYS) == 64, f"All 64 Gene Keys present (got: {len(GENE_KEYS)})")

    for gate_num in range(1, 65):
        gk = GENE_KEYS[gate_num]
        all_pass &= check(
            all(k in gk for k in ['shadow', 'gift', 'siddhi']),
            f"Gate {gate_num}: shadow/gift/siddhi all present"
        )

    # Get HD chart for test person
    hd = calc_human_design(
        birth_date=TEST_BIRTH_DATE,
        birth_time=TEST_BIRTH_TIME,
        birth_lat=TEST_BIRTH_LAT,
        birth_lon=TEST_BIRTH_LON,
        tz_offset=TEST_TZ_OFFSET,
        transit_date=TODAY,
    )

    profile = get_full_gene_keys_profile(
        active_gates=hd['active_gates'],
        todays_gates=hd['todays_gates'],
    )

    all_pass &= check(len(profile['natal_gene_keys']) > 0,
        f"Natal Gene Keys returned: {len(profile['natal_gene_keys'])}")

    print(f"\n  NATAL GENE KEYS ACTIVATIONS:")
    for gate_num, gk_data in sorted(profile['natal_gene_keys'].items()):
        print(f"    GK {gate_num:2d}: Shadow={gk_data['shadow']:<22} "
              f"Gift={gk_data['gift']:<22} Siddhi={gk_data['siddhi']}")

    print(f"\n  TODAY'S ACTIVE GENE KEYS ({TODAY}):")
    if 'todays_gene_keys' in profile:
        tgk = profile['todays_gene_keys']
        s_gk = tgk['sun_gene_key']
        e_gk = tgk['earth_gene_key']
        print(f"    Sun  Gate {tgk['sun_gate']:2d}: {s_gk['shadow']} → {s_gk['gift']} → {s_gk['siddhi']}")
        print(f"    Earth Gate {tgk['earth_gate']:2d}: {e_gk['shadow']} → {e_gk['gift']} → {e_gk['siddhi']}")

    if profile.get('resonance'):
        print(f"\n  RESONANCE:")
        for r in profile['resonance']:
            print(f"    {r['type']}: {r['message']}")
    else:
        print(f"\n  RESONANCE: No natal resonance with today's gates")

    return all_pass


# ============================================================
# Test 4: Full Orchestrator
# ============================================================

def test_orchestrator():
    separator("TEST 4: FULL ORCHESTRATOR (engines.py)")
    from engines import build_blueprint, get_daily_forecast

    print("\n  Building full blueprint...")
    bp = build_blueprint(
        birth_date=TEST_BIRTH_DATE,
        birth_time=TEST_BIRTH_TIME,
        birth_city=TEST_BIRTH_CITY,
        birth_lat=TEST_BIRTH_LAT,
        birth_lon=TEST_BIRTH_LON,
        tz_offset=TEST_TZ_OFFSET,
        transit_date=TODAY,
    )

    all_pass = True

    required_keys = ['meta', 'astrology', 'human_design', 'gene_keys', 'summary']
    for key in required_keys:
        all_pass &= check(key in bp, f"Blueprint has key: {key}")

    summary = bp['summary']
    print(f"\n  SUMMARY CARD:")
    print(f"    Sun Sign:       {summary['sun_sign']}")
    print(f"    Moon Sign:      {summary['moon_sign']}")
    print(f"    Ascendant:      {summary['ascendant']}")
    print(f"    HD Type:        {summary['hd_type']}")
    print(f"    HD Strategy:    {summary['hd_strategy']}")
    print(f"    HD Authority:   {summary['hd_authority']}")
    print(f"    Profile:        {summary['hd_profile']}")
    print(f"    Cross:          {summary['incarnation_cross']}")
    print(f"    Active Gates:   {summary['active_gates_count']}")
    print(f"    Defined Centres: {', '.join(summary['defined_centres'])}")
    print(f"    Channels:       {summary['defined_channels_count']}")
    print(f"    Today Sun Gate: {summary['todays_sun_gate']}")
    print(f"    Today Earth Gate:{summary['todays_earth_gate']}")
    print(f"    Active Aspects: {summary['active_aspects_count']}")

    # Daily forecast
    print(f"\n  DAILY FORECAST for {TODAY}:")
    forecast = get_daily_forecast(
        birth_date=TEST_BIRTH_DATE,
        birth_time=TEST_BIRTH_TIME,
        birth_city=TEST_BIRTH_CITY,
        birth_lat=TEST_BIRTH_LAT,
        birth_lon=TEST_BIRTH_LON,
        tz_offset=TEST_TZ_OFFSET,
        forecast_date=TODAY,
    )
    all_pass &= check('aspects' in forecast, "Forecast contains aspects")
    all_pass &= check('hd_daily_gates' in forecast, "Forecast contains HD daily gates")

    return all_pass


# ============================================================
# Main
# ============================================================

def main():
    print("\n" + "=" * 60)
    print("  SOLRAY AI — PHASE 1 ENGINE TEST SUITE")
    print("=" * 60)
    print(f"  Test subject: born {TEST_BIRTH_DATE} {TEST_BIRTH_TIME} in {TEST_BIRTH_CITY}")
    print(f"  Coordinates:  {TEST_BIRTH_LAT}N, {TEST_BIRTH_LON}E  (tz: UTC+{TEST_TZ_OFFSET})")
    print(f"  Today:        {TODAY}")

    results = {}

    try:
        results['astrology'] = test_astrology()
    except Exception as e:
        print(f"\n  {FAIL} Astrology engine ERROR: {e}")
        import traceback; traceback.print_exc()
        results['astrology'] = False

    try:
        results['human_design'] = test_human_design()
    except Exception as e:
        print(f"\n  {FAIL} Human Design engine ERROR: {e}")
        import traceback; traceback.print_exc()
        results['human_design'] = False

    try:
        results['gene_keys'] = test_gene_keys()
    except Exception as e:
        print(f"\n  {FAIL} Gene Keys engine ERROR: {e}")
        import traceback; traceback.print_exc()
        results['gene_keys'] = False

    try:
        results['orchestrator'] = test_orchestrator()
    except Exception as e:
        print(f"\n  {FAIL} Orchestrator ERROR: {e}")
        import traceback; traceback.print_exc()
        results['orchestrator'] = False

    # Final summary
    separator("FINAL RESULTS")
    all_passed = True
    for engine, passed in results.items():
        icon = PASS if passed else FAIL
        print(f"  {icon}  {engine.replace('_', ' ').title()}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print(f"  {PASS} ALL ENGINES PASSED")
    else:
        print(f"  {FAIL} SOME ENGINES FAILED — check output above")
    print()

    return 0 if all_passed else 1


if __name__ == '__main__':
    sys.exit(main())
