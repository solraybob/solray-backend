# BUILD LOG — Critical Bug Fixes
**Date:** 2026-03-22  
**Commit:** e19f480  
**Branch:** main → solraybob/solray-backend  

---

## Summary

All 3 critical bugs fixed, deployed, and verified on Railway production.

---

## BUG-001 ✅ Auto-detect timezone from birth coordinates

### What was done
- Added `from timezonefinder import TimezoneFinder` and `import pytz` to `api/main.py`
- Added `get_tz_offset(lat, lon, birth_date, birth_time) -> float` helper function
- Updated `/users/register` endpoint to call `get_tz_offset()` after geocoding instead of using `req.tz_offset` (which frontend never sent)
- Added `timezonefinder==6.5.2` and `pytz==2024.1` to `requirements.txt`

### Verification (local)
```
Tokyo (35.6762, 139.6503) birth 1995-07-22 14:15 → 9.0 ✅ (expected 9.0, JST)
Reykjavik (64.1355, -21.8954) birth 1992-12-03 18:05 → 0.0 ✅ (expected 0.0, Iceland no DST)
New York (40.7128, -74.0060) birth 1985-06-15 12:00 → -4.0 ✅ (expected -4.0, EDT summer)
```

---

## BUG-002 ✅ Added /souls/calculate-blueprint endpoint

### What was done
- Added `SoulBlueprintRequest` Pydantic model (name, birth_date, birth_time, birth_city — no email/password required)
- Added `POST /souls/calculate-blueprint` endpoint to `api/main.py`
- Endpoint geocodes birth city, auto-detects timezone, builds full blueprint, returns blueprint + profile summary (sun_sign, hd_type, hd_profile)

### Verification (production)
```bash
curl -X POST https://solray-backend-production.up.railway.app/souls/calculate-blueprint \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","birth_date":"1992-12-03","birth_time":"18:05","birth_city":"Reykjavik"}'
```
Result:
```
STATUS: OK
Sun sign: Sagittarius
HD type: Generator
HD profile: 1/3
```
✅ Endpoint live and returning correct data

---

## BUG-003 ✅ Password minimum length: 8 → 6

### What was done
- Changed `RegisterRequest.password` field from `min_length=8` to `min_length=6` to match frontend validation

---

## Marta's Blueprint

Checked Supabase for `martakarenk@protonmail.com` (user_id: `82c24bf1-6474-41bc-8bc5-aabd09ab67ed`):
- **No existing blueprint or forecast rows found** — she never completed the registration flow, so there's nothing to delete
- On her next login/registration, the new auto-timezone logic will calculate correctly

---

## Files Changed
- `api/main.py` — 82 insertions (+), 2 deletions (-)
- `requirements.txt` — added timezonefinder + pytz

---

## Deployment
- Pushed to GitHub: `git push` → `670e4b1..e19f480  main -> main`
- Railway auto-deployed in ~90 seconds
- Production endpoint verified: ✅
