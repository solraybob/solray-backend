# BUILD LOG: Deterministic Energy Score Calculator

**Date:** 2026-03-22  
**Status:** ✅ Complete

---

## What Was Built

### `energy_calculator.py` (new file)
A fully deterministic, mathematical energy score calculator. No AI involved.

**Algorithm:**
- Base score: 5.0 for all 4 dimensions (mental, emotional, physical, intuitive)
- For each transit aspect: delta = `planet_influence[dim] × aspect_modifier × orb_weight × 2`
- Orb weight: `(max_orb - orb) / max_orb` — tighter orbs have more impact
- Conjunctions: modifier depends on transit planet's nature (benefic +0.8, malefic -0.6, neutral +0.2)
- Final score: `clamp(round(5.0 + sum_of_deltas), 1, 10)`

**Functions:**
- `calculate_energy_scores(transit_aspects, natal_planets) -> dict` — main entrypoint
- `get_max_orb(aspect_name) -> float` — returns standard orb limit per aspect type

---

## Integration Changes

### `ai/forecast.py`
- Added `from energy_calculator import calculate_energy_scores` import
- In `generate_daily_forecast()`: calls `calculate_energy_scores()` with transit aspects + natal planets
- System prompt now instructs Claude: "The energy scores have been pre-calculated algorithmically. Use these exact values: Mental X/10, Emotional X/10, Physical X/10, Intuitive X/10. Do not change them."
- Removed AI energy normalisation logic — always overwrites with deterministic scores
- `energy_levels` (legacy 0-100 field) is derived from deterministic scores (`v * 10`)

### `api/main.py`
- Added `from energy_calculator import calculate_energy_scores` import
- After building `final_forecast`, injects calculated `energy` and `energy_levels` fields before caching
- This ensures cached forecasts always have deterministic scores, even if AI generation fails

---

## Test Results

**Birth:** 1989-09-05 14:38 Reykjavik  
**Run date:** 2026-03-22  

### Transit aspects found (10 total):
```
Ceres quintile natal Chiron (orb 0.02°)
Vesta semi_square natal PartOfFortune (orb 0.03°)
EastPoint quincunx natal Neptune (orb 0.05°)
Juno sesquiquadrate natal Sun (orb 0.05°)
Juno semi_square natal Earth (orb 0.05°)
NorthNode quincunx natal Mercury (orb 0.07°)
Mercury quincunx natal Mercury (orb 0.11°)
PartOfFortune conjunction natal Sun (orb 0.13°)
PartOfFortune opposition natal Earth (orb 0.13°)
Uranus semi_square natal Pallas (orb 0.14°)
```

### Calculated energy scores:
| Dimension  | Delta  | Raw  | Final |
|------------|--------|------|-------|
| Mental     | -1.75  | 3.25 | **3/10** |
| Emotional  | -0.67  | 4.33 | **4/10** |
| Physical   | -0.42  | 4.58 | **5/10** |
| Intuitive  | -1.18  | 3.82 | **4/10** |

**Observation:** Today's transits are predominantly draining aspects (quincunx, semi_square, sesquiquadrate, opposition) with no strong benefic influences. The scores reflect a somewhat challenging day energetically — notably lower mental and intuitive energy.

Previously the AI was estimating scores freeform from the prompt context, which could vary run to run. Now these values are locked.

---

## GitHub
Committed and pushed: `670e4b1`  
Message: `Add deterministic energy score calculator from transit aspects`  
Branch: `main`
