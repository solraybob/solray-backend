"""
numerology.py — Pythagorean Numerology Calculator for Solray AI

Calculates the four core numerology numbers from birth date and name:
  - Life Path Number (birth date)
  - Expression Number (full name)
  - Soul Urge Number (vowels in name)
  - Personal Year Number (birth month + day + current year)

Master numbers 11, 22, 33 are preserved through a component-reduction
scheme: month, day, and year are reduced individually before summing,
so a 29th-of-the-month (29 -> 11) or a 1993 year (1993 -> 22) keeps its
master status instead of being dissolved by a naive all-digits sum.

Names are normalised before scoring so Icelandic and other European
diacritics resolve to the correct Pythagorean letter: á -> a, ö -> o,
ð -> d, þ -> th, æ -> ae, ø -> o. Without this step an Icelandic name
like "Kristján Ólafsson" would silently drop its accented letters and
produce a different number from "Kristjan Olafsson".
"""

import unicodedata
from datetime import date
from typing import Optional

# ---------------------------------------------------------------------------
# Pythagorean chart: A=1 … Z=8
# ---------------------------------------------------------------------------
_PYTHAGOREAN = {
    'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'F': 6, 'G': 7, 'H': 8, 'I': 9,
    'J': 1, 'K': 2, 'L': 3, 'M': 4, 'N': 5, 'O': 6, 'P': 7, 'Q': 8, 'R': 9,
    'S': 1, 'T': 2, 'U': 3, 'V': 4, 'W': 5, 'X': 6, 'Y': 7, 'Z': 8,
}

_VOWELS = set('AEIOU')
# Y is treated as a vowel for Soul Urge when it functions as one.
# Standard Pythagorean: Y is a vowel when it acts as the only vowel sound.
# For simplicity and consistency, we exclude Y from vowels (most common approach).

_MASTER_NUMBERS = {11, 22, 33}

# Nordic and other letters NFD does not decompose. These are transliterated to
# their closest Pythagorean-scorable Latin equivalent before the chart is
# applied. Thorn (þ) and eth (ð) follow the modern Icelandic convention where
# they transliterate to "th" and "d". Scharfes S -> ss, slashed O -> o,
# slashed L -> l, ash -> ae.
_TRANSLITERATION = {
    'ð': 'd',  'Ð': 'D',
    'þ': 'th', 'Þ': 'Th',
    'æ': 'ae', 'Æ': 'Ae',
    'œ': 'oe', 'Œ': 'Oe',
    'ø': 'o',  'Ø': 'O',
    'ß': 'ss',
    'ł': 'l',  'Ł': 'L',
}


def _normalise_name(name: str) -> str:
    """Fold a name down to its Pythagorean-scorable Latin form.

    Accented vowels and consonants are NFD-decomposed and their combining
    marks are dropped, so "Kristján" becomes "Kristjan". Nordic letters that
    NFD cannot decompose (ð, þ, æ, ø) are transliterated via the table above.
    """
    out = []
    for ch in name:
        if ch in _TRANSLITERATION:
            out.append(_TRANSLITERATION[ch])
            continue
        for d in unicodedata.normalize('NFD', ch):
            if unicodedata.category(d) != 'Mn':  # Mn = non-spacing combining mark
                out.append(d)
    return ''.join(out)

# ---------------------------------------------------------------------------
# Meanings dictionary
# ---------------------------------------------------------------------------
MEANINGS = {
    1:  "Leadership, independence, new beginnings. The pioneer who forges their own path.",
    2:  "Partnership, diplomacy, sensitivity. The peacemaker who thrives through cooperation.",
    3:  "Creativity, self-expression, joy. The artist who communicates with natural charm.",
    4:  "Stability, discipline, hard work. The builder who creates lasting foundations.",
    5:  "Freedom, change, adventure. The free spirit drawn to experience and the senses.",
    6:  "Responsibility, nurturing, harmony. The caregiver who finds purpose in service.",
    7:  "Introspection, analysis, spiritual seeking. The seeker who dives beneath the surface.",
    8:  "Ambition, power, material mastery. The executive who manifests abundance through effort.",
    9:  "Compassion, wisdom, completion. The humanitarian who serves the greater good.",
    11: "Intuition, spiritual insight, illumination. Master number: the visionary channel.",
    22: "Master builder, large-scale vision, practical idealism. Master number: the architect of change.",
    33: "Master teacher, unconditional love, healing. Master number: the compassionate guide.",
}

# Short one-line meanings (used in the app display)
SHORT_MEANINGS = {
    1:  "Leadership & new beginnings",
    2:  "Partnership & diplomacy",
    3:  "Creativity & self-expression",
    4:  "Stability & hard work",
    5:  "Freedom & adventure",
    6:  "Responsibility & nurturing",
    7:  "Introspection & spiritual seeking",
    8:  "Ambition & material mastery",
    9:  "Compassion & completion",
    11: "Intuition & spiritual illumination (master)",
    22: "Master builder & visionary architect (master)",
    33: "Master teacher & unconditional love (master)",
}


# ---------------------------------------------------------------------------
# Core reduction helper
# ---------------------------------------------------------------------------

def _reduce(n: int) -> int:
    """
    Reduce a number to a single digit or master number (11, 22, 33).
    """
    while n > 9 and n not in _MASTER_NUMBERS:
        n = sum(int(d) for d in str(n))
    return n


# ---------------------------------------------------------------------------
# Life Path Number — from birth date
# ---------------------------------------------------------------------------

def life_path(birth_date: str) -> int:
    """
    Calculate Life Path Number from birth date string "YYYY-MM-DD".

    Uses the component-reduction method: each of month, day, year is reduced
    individually (preserving any 11 / 22 / 33 master that appears) before the
    three are summed and reduced again. A naive "sum all eight digits" pass
    would lose masters that live inside a component. For example, 1997-12-29:
    component method gives 3 + 11 + 8 = 22 (master), all-digits gives 40 -> 4.
    """
    parts = birth_date.split('-')  # ['YYYY', 'MM', 'DD']
    year = int(parts[0])
    month = int(parts[1])
    day = int(parts[2])
    total = _reduce(month) + _reduce(day) + _reduce(year)
    return _reduce(total)


# ---------------------------------------------------------------------------
# Expression Number — from full name
# ---------------------------------------------------------------------------

def expression(name: str) -> int:
    """
    Calculate Expression (Destiny) Number from full name at birth.
    Uses Pythagorean chart. Diacritics are folded to base letters, so
    "Kristján" and "Kristjan" score identically. Nordic ð, þ, æ, ø are
    transliterated via _normalise_name before scoring.
    """
    folded = _normalise_name(name)
    total = sum(_PYTHAGOREAN.get(c.upper(), 0) for c in folded if c.isalpha())
    return _reduce(total)


# ---------------------------------------------------------------------------
# Soul Urge Number — from vowels in name
# ---------------------------------------------------------------------------

def soul_urge(name: str) -> int:
    """
    Calculate Soul Urge (Heart's Desire) Number from vowels in full name.
    Vowels: A, E, I, O, U (Y excluded in standard Pythagorean method).
    Diacritics are folded first so á counts as A, ö as O, and so on.
    """
    folded = _normalise_name(name)
    total = sum(_PYTHAGOREAN[c.upper()] for c in folded if c.upper() in _VOWELS)
    return _reduce(total)


# ---------------------------------------------------------------------------
# Personal Year Number — changes annually
# ---------------------------------------------------------------------------

def personal_year(birth_date: str, year: Optional[int] = None) -> int:
    """
    Calculate Personal Year Number for a given year (defaults to current year).

    Formula: reduce(birth_month) + reduce(birth_day) + reduce(year), then
    reduce the total. The component-reduction form is used for the same
    reason as Life Path: to keep an 11 / 22 / 33 master from dissolving when
    it lives inside the month, day, or universal year.
    """
    if year is None:
        year = date.today().year

    parts = birth_date.split('-')  # ['YYYY', 'MM', 'DD']
    month = int(parts[1])
    day = int(parts[2])

    total = _reduce(month) + _reduce(day) + _reduce(year)
    return _reduce(total)


# ---------------------------------------------------------------------------
# Main calculation function
# ---------------------------------------------------------------------------

def calculate_numerology(birth_date: str, birth_name: str) -> dict:
    """
    Calculate core numerology numbers from birth date and name.

    Args:
        birth_date: "YYYY-MM-DD"
        birth_name: full name at birth

    Returns:
        dict with:
          - life_path: int
          - expression: int
          - soul_urge: int
          - personal_year: int
          - current_year: int
          - meanings: dict mapping number → full meaning
          - short_meanings: dict mapping number → one-line meaning
    """
    lp = life_path(birth_date)
    ex = expression(birth_name)
    su = soul_urge(birth_name)
    current_year = date.today().year
    py = personal_year(birth_date, current_year)

    # Collect unique numbers to include in meanings output
    numbers = {lp, ex, su, py}
    return {
        'life_path':     lp,
        'expression':    ex,
        'soul_urge':     su,
        'personal_year': py,
        'current_year':  current_year,
        'meanings': {
            str(n): MEANINGS.get(n, '') for n in numbers
        },
        'short_meanings': {
            str(n): SHORT_MEANINGS.get(n, '') for n in numbers
        },
    }


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    result = calculate_numerology('1989-09-05', 'Kristjan Gilbert')
    print("=== Numerology for Kristjan Gilbert (born Sep 5 1989) ===")
    print(f"Life Path:     {result['life_path']}  — {SHORT_MEANINGS.get(result['life_path'])}")
    print(f"Expression:    {result['expression']}  — {SHORT_MEANINGS.get(result['expression'])}")
    print(f"Soul Urge:     {result['soul_urge']}  — {SHORT_MEANINGS.get(result['soul_urge'])}")
    print(f"Personal Year ({result['current_year']}): {result['personal_year']}  — {SHORT_MEANINGS.get(result['personal_year'])}")
