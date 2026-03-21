# BUILD_LOG.md — Solray AI Phase 1: Calculation Engines

**Date:** 2026-03-20  
**Status:** ✅ ALL ENGINES PASSING  

---

## What Was Built

### Files Created

| File | Purpose | Lines |
|------|---------|-------|
| `astrology.py` | Astrology engine (natal chart, transits, aspects) | ~310 |
| `human_design.py` | Human Design engine (BodyGraph, type, gates, centres) | ~410 |
| `gene_keys.py` | Gene Keys engine (64-key dataset, shadow/gift/siddhi) | ~195 |
| `engines.py` | Main orchestrator (unified blueprint + daily forecast) | ~120 |
| `test_engines.py` | Full test suite with readable output | ~370 |

---

## Engine Summaries

### 1. Astrology Engine (`astrology.py`)
- Uses **pyswisseph with Moshier built-in ephemeris** (no external `.se1` files needed)
- Calculates natal chart: Sun, Moon, Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune, Pluto, North Node + Chiron (graceful fallback)
- Placidus house system — all 12 cusps + Ascendant + MC
- Planet-to-house assignment handles 0°/360° boundary correctly
- Transits for any given date/time
- Aspects: conjunction (8°), opposition (8°), trine (7°), square (7°), sextile (6°) with orb
- Geocoding via **geopy Nominatim** + 20-city hardcoded fallback

### 2. Human Design Engine (`human_design.py`)
- Calculates **conscious chart** (birth moment) and **unconscious/design chart** (88 days prior)
- Uses the **standard 64-gate HD mandala wheel** (Jovian Archive sequence) — 5.625°/gate, 6 lines per gate
- Earth = Sun + 180°, South Node = North Node + 180°
- Derives **defined channels** (both gates present in active set)
- Derives **defined centres** (9 centres: Head, Ajna, Throat, G, Heart, Sacral, SolarPlexus, Spleen, Root)
- **Type determination logic**: Generator / Manifesting Generator / Projector / Manifestor / Reflector
  - Motor-to-Throat connectivity analysis included
- **Profile** from conscious Sun line / conscious Earth line
- **Incarnation Cross** from 4 activation gates (Cross naming requires 192-entry lookup — flagged for Phase 2)
- Today's active Sun/Earth gates for daily HD transits

### 3. Gene Keys Engine (`gene_keys.py`)
- **Complete 64-entry dataset** with accurate Shadow / Gift / Siddhi for each key
- 1:1 mapping from HD gate → Gene Key number
- Natal profile: all activated gene keys with full triads
- Daily resonance detection: flags when today's Sun/Earth gate overlaps with natal activations

### 4. Orchestrator (`engines.py`)
- `build_blueprint()`: runs all 3 engines, returns unified dict
- `get_daily_forecast()`: extracts focused forecast (transits, aspects, daily HD gates, active Gene Keys)
- Summary card field for quick UI rendering

---

## Test Results (1990-06-15 14:30 London BST)

```
Sun:       Gemini 24.19°  House 9
Moon:      Pisces 16.19°  House 5
Mercury:   Gemini  5.80°  House 8
Venus:     Taurus 18.85°  House 8
Mars:      Aries  11.09°  House 6
Jupiter:   Cancer 15.90°  House 10
Saturn:    Capricorn 24.03°  House 4 ℞
Uranus:    Capricorn  8.16°  House 3 ℞
Neptune:   Capricorn 13.72°  House 3 ℞
Pluto:     Scorpio 15.40°  House 2 ℞
NorthNode: Aquarius  8.12°  House 4
Ascendant: Libra 11.24°
MC:        Cancer 14.69°

HD Type:     Generator
Strategy:    Wait to respond
Authority:   Sacral
Profile:     6/6
Cross:       Cross of Gates 42/32 | 60/56
Defined:     G, Heart, Sacral, Spleen, Root (5 of 9)
Channels:    2–14 (The Beat), 26–44 (Surrender), 27–50 (Preservation), 32–54 (Transformation)
Active Gates: 20 gates

Today (2026-03-20):
  Sun Gate 60: Limitation → Realism → Justice
  Earth Gate 56: Distraction → Enrichment → Intoxication
  (Both resonate with natal activations)
```

---

## Known Issues / Phase 2 TODOs

1. **Chiron requires external ephemeris files** (`seas_18.se1`). Currently returns `null` data gracefully. To fix: install Swiss Ephemeris data files via `pip install pyswisseph` with ephe files, or download from https://www.astro.com/swisseph/. All other bodies work fine with the built-in Moshier ephemeris.

2. **Incarnation Cross naming** — only the 4 gate numbers are returned. Full cross names (e.g. "Right Angle Cross of Eden") require a 192-entry lookup table. Not implemented in Phase 1.

3. **Timezone handling** — caller must pass `tz_offset` manually. Phase 2 should integrate `pytz`/`timezonefinder` for automatic DST-aware timezone lookup from lat/lon.

4. **HD Design date** — uses exactly 88 calendar days before birth. The technically correct method is 88 solar degrees before birth (Sun position). A binary search refinement is coded but commented; for Phase 2, enable the refinement for maximum accuracy.

5. **Retrograde flags in HD** — Human Design traditionally does not use retrograde information. It's calculated but not surfaced in HD output. Available in astrology output.

6. **Aspect orbs** — currently fixed standard orbs. Phase 2 could make these configurable per-planet (e.g. larger orbs for Sun/Moon).

---

## Dependencies

```
pyswisseph==2.10.3.2
geopy==2.4.1
geographiclib==2.1
Python 3.9.6
```

All installed to user site-packages (`~/Library/Python/3.9/`).

---

## Phase 2 Recommendations

- Add Chiron support (download Swiss Ephemeris files)
- Full Incarnation Cross name lookup (192 crosses)
- Auto-timezone from coordinates (pytz + timezonefinder)
- REST API wrapper (FastAPI)
- Persistent storage for user blueprints
- Narrative text generation layer on top of the raw data
