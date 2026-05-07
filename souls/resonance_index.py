"""
souls/resonance_index.py — the Solray Resonance Index.

Transparent, deterministic, multi-axis 0-100 number describing the
ENERGETIC OVERLAP between two charts. Not a verdict on the relationship.
Not a soulmate score. The four-lens reading is what describes WHAT is
happening between two people; this index is the front door.

Five axes, weighted:
  30%  Resonance         shared HD gates as % of unique gates between them
  25%  Energetic loop    channels they form together (count * 7, cap 100)
  20%  Type pairing      HD type-combo matrix (Generator+Projector etc.)
  15%  Astrological      Sun/Moon/Venus/Mars/Asc element-family overlap
  10%  Gene Key align    same gate filling the same sphere on both sides

Everything is auditable: the same two charts produce the same number every
time, and every sub-axis can be explained from the chart data without
hidden state.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# Sign / element helpers
# ---------------------------------------------------------------------------

_FIRE  = {"Aries", "Leo", "Sagittarius"}
_EARTH = {"Taurus", "Virgo", "Capricorn"}
_AIR   = {"Gemini", "Libra", "Aquarius"}
_WATER = {"Cancer", "Scorpio", "Pisces"}


def _element(sign: str | None) -> str | None:
    if not sign:
        return None
    if sign in _FIRE:  return "fire"
    if sign in _EARTH: return "earth"
    if sign in _AIR:   return "air"
    if sign in _WATER: return "water"
    return None


def _harmonious(elem_a: str | None, elem_b: str | None) -> bool:
    """Harmonious element pairs:
       same element, fire/air, earth/water.
       Friction pairs return False.
    """
    if not elem_a or not elem_b:
        return False
    if elem_a == elem_b:
        return True
    pair = frozenset((elem_a, elem_b))
    return pair in (frozenset({"fire", "air"}), frozenset({"earth", "water"}))


# ---------------------------------------------------------------------------
# Sub-axis calculators (each returns 0-100)
# ---------------------------------------------------------------------------

def _axis_resonance(bp_a: dict, bp_b: dict) -> Tuple[float, dict]:
    gates_a = set(bp_a.get("human_design", {}).get("active_gates", []) or [])
    gates_b = set(bp_b.get("human_design", {}).get("active_gates", []) or [])
    if not gates_a or not gates_b:
        return 0.0, {"shared": 0, "union": 0}
    shared = gates_a & gates_b
    union = gates_a | gates_b
    score = (len(shared) / len(union)) * 100 if union else 0.0
    return round(score, 1), {"shared": len(shared), "union": len(union)}


def _axis_energetic_loop(bp_a: dict, bp_b: dict) -> Tuple[float, dict]:
    """Channels that the pair forms TOGETHER (a gate from A pairs with the
    other half from B), regardless of whether either had it solo. We use
    a static table of HD channels (gate pairs) to detect this.
    """
    gates_a = set(bp_a.get("human_design", {}).get("active_gates", []) or [])
    gates_b = set(bp_b.get("human_design", {}).get("active_gates", []) or [])
    combined = gates_a | gates_b

    # Standard 36 HD channel definitions (as gate pairs, unordered).
    HD_CHANNELS = [
        (1, 8), (2, 14), (3, 60), (4, 63), (5, 15), (6, 59), (7, 31),
        (9, 52), (10, 20), (10, 34), (10, 57), (11, 56), (12, 22),
        (13, 33), (16, 48), (17, 62), (18, 58), (19, 49), (20, 34),
        (20, 57), (21, 45), (23, 43), (24, 61), (25, 51), (26, 44),
        (27, 50), (28, 38), (29, 46), (30, 41), (32, 54), (34, 57),
        (35, 36), (37, 40), (39, 55), (42, 53), (47, 64),
    ]

    completed_together = 0
    completed_pairs = []
    for ga, gb in HD_CHANNELS:
        a_has_ga = ga in gates_a
        a_has_gb = gb in gates_a
        b_has_ga = ga in gates_b
        b_has_gb = gb in gates_b
        # Channel is "completed together" iff: both gates are present in the
        # combined pool AND neither person already had both gates solo. The
        # earlier version counted any channel where each side had at least
        # one of the two gates, which double-counted channels one person
        # already closed alone (Codex audit catch).
        both_present = (a_has_ga or b_has_ga) and (a_has_gb or b_has_gb)
        a_alone_complete = a_has_ga and a_has_gb
        b_alone_complete = b_has_ga and b_has_gb
        if both_present and not a_alone_complete and not b_alone_complete:
            completed_together += 1
            completed_pairs.append([ga, gb])

    score = min(completed_together * 7, 100)
    return round(float(score), 1), {
        "completed_together": completed_together,
        "channels": completed_pairs,
    }


# HD type-combo matrix. Generator+Projector is the "classic" pairing
# (Projector guides, Generator provides fuel). Two Manifestors is famously
# friction-prone (both initiating). These weights are heuristic, not
# scripture; they reflect mainstream HD literature.
_TYPE_MATRIX = {
    frozenset({"Generator",            "Generator"}):            80,
    frozenset({"Generator",            "Manifesting Generator"}): 80,
    frozenset({"Generator",            "Projector"}):            90,
    frozenset({"Generator",            "Manifestor"}):           70,
    frozenset({"Generator",            "Reflector"}):            72,
    frozenset({"Manifesting Generator","Manifesting Generator"}): 75,
    frozenset({"Manifesting Generator","Projector"}):            88,
    frozenset({"Manifesting Generator","Manifestor"}):           70,
    frozenset({"Manifesting Generator","Reflector"}):            70,
    frozenset({"Projector",            "Projector"}):            62,
    frozenset({"Projector",            "Manifestor"}):           75,
    frozenset({"Projector",            "Reflector"}):            68,
    frozenset({"Manifestor",           "Manifestor"}):           50,
    frozenset({"Manifestor",           "Reflector"}):            60,
    frozenset({"Reflector",            "Reflector"}):            55,
}


def _axis_type_pairing(bp_a: dict, bp_b: dict) -> Tuple[float, dict]:
    type_a = bp_a.get("human_design", {}).get("type", "")
    type_b = bp_b.get("human_design", {}).get("type", "")
    if not type_a or not type_b:
        return 60.0, {"types": [type_a, type_b], "fallback": True}
    score = _TYPE_MATRIX.get(frozenset({type_a, type_b}), 65)
    return float(score), {"types": [type_a, type_b]}


def _axis_astrological(bp_a: dict, bp_b: dict) -> Tuple[float, dict]:
    """Element-family overlap on five cross-checks:
        A.Sun  vs B.Sun
        A.Moon vs B.Moon
        A.Sun  vs B.Moon
        A.Venus vs B.Mars
        A.Asc  vs B.Sun
    Score = (harmonious_count / 5) * 100.
    """
    def planet(bp: dict, name: str) -> str | None:
        s = (bp.get("summary") or {}).get(f"{name.lower()}_sign")
        if s:
            return s
        p = (bp.get("astrology") or {}).get("natal", {}).get("planets", {}).get(name)
        if isinstance(p, dict):
            return p.get("sign")
        return None

    def asc(bp: dict) -> str | None:
        return (
            (bp.get("summary") or {}).get("asc_sign")
            or (bp.get("astrology") or {}).get("natal", {}).get("ascendant", {}).get("sign")
        )

    sun_a, sun_b = planet(bp_a, "Sun"),   planet(bp_b, "Sun")
    moon_a, moon_b = planet(bp_a, "Moon"), planet(bp_b, "Moon")
    venus_a, venus_b = planet(bp_a, "Venus"), planet(bp_b, "Venus")
    mars_a, mars_b = planet(bp_a, "Mars"), planet(bp_b, "Mars")
    asc_a, asc_b = asc(bp_a), asc(bp_b)

    # Each cross-pair check is BIDIRECTIONAL so the score is independent of
    # which side requests it (Codex audit catch). For asymmetric pairs
    # (Sun-Moon, Venus-Mars, Asc-Sun) we treat the check as harmonious if
    # either direction is harmonious; this keeps the count out of /5 and
    # makes the index identical for both members of the pair.
    def both_harmonious(s1: str | None, s2: str | None) -> bool:
        return _harmonious(_element(s1), _element(s2))

    def asym_pair_harmonious(a1: str | None, b1: str | None, a2: str | None, b2: str | None) -> bool:
        # (a1,b1) is one direction (e.g. A.Sun, B.Moon)
        # (a2,b2) is the reverse        (e.g. A.Moon, B.Sun)
        return both_harmonious(a1, b1) or both_harmonious(a2, b2)

    checks: List[Tuple[str, bool]] = [
        ("Sun-Sun",    both_harmonious(sun_a, sun_b)),
        ("Moon-Moon",  both_harmonious(moon_a, moon_b)),
        ("Sun-Moon",   asym_pair_harmonious(sun_a, moon_b, moon_a, sun_b)),
        ("Venus-Mars", asym_pair_harmonious(venus_a, mars_b, mars_a, venus_b)),
        ("Asc-Sun",    asym_pair_harmonious(asc_a, sun_b, sun_a, asc_b)),
    ]
    harmonious = [label for label, ok in checks if ok]
    score = (len(harmonious) / 5.0) * 100
    return round(score, 1), {"harmonious": harmonious, "checked": len(checks)}


def _axis_gene_keys(bp_a: dict, bp_b: dict) -> Tuple[float, dict]:
    """Same gate filling the same sphere (life's work, evolution, etc.)
    on both sides. 10% weight because Gene Keys overlap is genuinely
    rare and shouldn't dominate the index.
    """
    def spheres(bp: dict) -> Dict[str, int]:
        gk = bp.get("gene_keys", {}) or {}
        out: Dict[str, int] = {}
        for k in ("lifes_work", "evolution", "radiance", "purpose", "attraction", "iq", "eq"):
            entry = gk.get(k)
            if isinstance(entry, dict) and entry.get("gate"):
                try:
                    out[k] = int(entry["gate"])
                except Exception:
                    pass
        return out

    a, b = spheres(bp_a), spheres(bp_b)
    if not a or not b:
        return 50.0, {"shared_spheres": [], "fallback": True}

    shared = []
    for k in a:
        if k in b and a[k] == b[k]:
            shared.append({"sphere": k, "gate": a[k]})
    total = len(set(a.keys()) | set(b.keys()))
    score = (len(shared) / total) * 100 if total else 0.0
    return round(score, 1), {"shared_spheres": shared, "total": total}


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

WEIGHTS = {
    "resonance":         0.30,
    "energetic_loop":    0.25,
    "type_pairing":      0.20,
    "astrological":      0.15,
    "gene_keys":         0.10,
}


def compute_resonance_index(bp_a: dict, bp_b: dict) -> Dict[str, Any]:
    """Return the full Resonance Index payload:

        {
          "overall": 73,
          "axes": {
            "resonance":      {"score": 42.1, "detail": {...}},
            "energetic_loop": {"score": 70.0, "detail": {...}},
            "type_pairing":   {"score": 90.0, "detail": {...}},
            "astrological":   {"score": 80.0, "detail": {...}},
            "gene_keys":      {"score": 14.3, "detail": {...}},
          },
          "weights": {"resonance": 0.30, ...},
          "version": 1,
        }

    All sub-scores 0-100. Overall 0-100, weighted average.
    """
    res, res_d = _axis_resonance(bp_a, bp_b)
    eng, eng_d = _axis_energetic_loop(bp_a, bp_b)
    typ, typ_d = _axis_type_pairing(bp_a, bp_b)
    ast, ast_d = _axis_astrological(bp_a, bp_b)
    gks, gks_d = _axis_gene_keys(bp_a, bp_b)

    overall = (
        res * WEIGHTS["resonance"]
        + eng * WEIGHTS["energetic_loop"]
        + typ * WEIGHTS["type_pairing"]
        + ast * WEIGHTS["astrological"]
        + gks * WEIGHTS["gene_keys"]
    )

    return {
        "overall": round(overall, 1),
        "axes": {
            "resonance":      {"score": res, "detail": res_d, "weight": WEIGHTS["resonance"]},
            "energetic_loop": {"score": eng, "detail": eng_d, "weight": WEIGHTS["energetic_loop"]},
            "type_pairing":   {"score": typ, "detail": typ_d, "weight": WEIGHTS["type_pairing"]},
            "astrological":   {"score": ast, "detail": ast_d, "weight": WEIGHTS["astrological"]},
            "gene_keys":      {"score": gks, "detail": gks_d, "weight": WEIGHTS["gene_keys"]},
        },
        "weights": dict(WEIGHTS),
        "version": 1,
    }
