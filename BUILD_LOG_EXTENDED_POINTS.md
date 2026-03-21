# BUILD LOG — Extended Chart Points
**Date:** 2026-03-21  
**Commit:** `106aa06`  
**Branch:** main  
**Status:** ✅ ALL ENGINES PASSED

---

## What Was Added

### Extended Bodies (asteroids + Black Moon Lilith)
All returned under `natal_chart['extended_points']` and `transits['extended_transit_points']`.

| Key | Name | Method | Notes |
|-----|------|--------|-------|
| `Chiron` | Chiron | SWIEPH (SE1 files) | Fixed: was failing, now uses `~/ephe/seas_18.se1` |
| `Ceres` | Ceres | SWIEPH (SE1 files) | New |
| `Pallas` | Pallas | SWIEPH (SE1 files) | New |
| `Juno` | Juno | SWIEPH (SE1 files) | New |
| `Vesta` | Vesta | SWIEPH (SE1 files) | New |
| `BlackMoonLilith` | Black Moon Lilith (Mean) | Moshier (built-in) | `swe.MEAN_APOG` — no files needed |

### Mathematical Points
| Key | Name | Formula |
|-----|------|---------|
| `Earth` | Earth | Sun longitude + 180° |
| `PartOfFortune` | Part of Fortune | Day: ASC + Moon − Sun / Night: ASC + Sun − Moon |
| `Vertex` | Vertex | `swe.houses()` ascmc[3] — prime vertical / ecliptic intersection (west) |
| `EastPoint` | East Point (Equatorial ASC) | `swe.houses()` ascmc[4] — ARMC + 90° projected onto ecliptic |

### Sign Rulership Corrections
| Sign | Old (classical) | New (corrected) |
|------|----------------|----------------|
| Virgo | Mercury | **Ceres** |
| Taurus | Venus | **Earth** |

All other signs unchanged. Venus still calculated as a planet; Mercury still calculated as a planet. They are not rulers of Taurus/Virgo.

---

## Test Results (1990-06-15 14:30 London, BST)

### Natal Chart Extended Points
```
Chiron          Cancer          16.07°  House 10
Ceres           Cancer          27.90°  House 10
Pallas          Gemini          16.01°  House  9
Juno            Scorpio         10.32°  House  2  ℞
Vesta           Taurus           4.83°  House  7
BlackMoonLilith Scorpio         24.96°  House  2
Earth           Sagittarius     24.19°  House  3
Part of Fortune Cancer           3.24°  House  9  [day chart]
Vertex          Aries           25.90°  House  7
East Point      Libra           17.30°  House  1
```

### Transit Extended Points (2026-03-21)
```
Chiron          Aries           25.10°
Ceres           Taurus           2.40°
Pallas          Pisces          18.36°
Juno            Capricorn       27.77°
Vesta           Pisces           5.28°
BlackMoonLilith Sagittarius     10.16°
Earth           Libra            0.88°
Part of Fortune Leo             28.14°
Vertex          Sagittarius     11.53°
East Point      Gemini          28.98°
```

### Aspects
- 139 total aspects (up from 80 — extended points now participate in aspect calculations)

---

## Ephemeris Files

Swiss Ephemeris SE1 asteroid files required for Chiron/Ceres/Pallas/Juno/Vesta:
- **Location:** `~/ephe/seas_18.se1`
- **Source:** https://github.com/aloistr/swisseph/tree/master/ephe
- **Env var override:** `SWISSEPH_PATH`
- Chiron has Moshier fallback if SE1 files are missing (slightly less accurate, same result for most dates)
- On Railway/Docker: add `~/ephe/seas_18.se1` to the image or mount it as a volume

---

## API Shape (backwards compatible)

```python
natal_chart = get_natal_chart(...)

# Existing (unchanged)
natal_chart['planets']       # Sun, Moon, Mercury, Venus, Mars, Jupiter, Saturn,
                              # Uranus, Neptune, Pluto, NorthNode, Chiron

# New
natal_chart['extended_points']  # Chiron, Ceres, Pallas, Juno, Vesta,
                                 # BlackMoonLilith, Earth, PartOfFortune, Vertex, EastPoint

natal_chart['armc']          # ARMC longitude (new)

transits = get_transits_and_aspects(natal_chart, date)
transits['transit_planets']           # unchanged
transits['extended_transit_points']  # new: same keys as extended_points
transits['aspects']                  # now includes extended points on both sides
```

Each extended point dict:
```python
{
  'name': str,           # display name
  'absolute_degree': float,  # 0–359.99
  'sign': str,           # zodiac sign
  'degree': float,       # 0–29.99
  'house': int,          # 1–12
  'retrograde': bool,    # False for mathematical points
  # optional:
  'chart_type': str,     # 'day' or 'night' (PartOfFortune only)
  'error': str,          # present only if calculation failed
}
```

---

## Files Changed
- `astrology.py` — main engine update
- `test_engines.py` — extended test coverage

## Git
- Commit: `106aa06`
- Push: ✅ `main` → `solraybob/solray-backend`
