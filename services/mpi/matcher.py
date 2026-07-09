"""
Deterministic patient matching algorithm.

Score weights:
  MRN exact match   → 1.0  (immediate certain match)
  firstname         → 0.25  (Jaro-Winkler similarity, phonetic floor)
  lastname          → 0.35  (Jaro-Winkler similarity, phonetic floor)
  birthdate exact   → 0.30
  sex exact         → 0.10
  max without MRN   → 0.99 (never reaches 1.0 to distinguish from MRN match)

Name handling (T-16):
  - Diacritics are transliterated, not dropped ("René" → "rene", not "ren"),
    so accented spellings of the same name score 1.0.
  - Phonetic secondary signal: when two names sound identical (Metaphone)
    but are spelled differently (Catherine/Katherine), the similarity is
    floored at PHONETIC_FLOOR instead of relying on edit distance alone.

Threshold for duplicate flag: 0.75 (override via MPI_MATCH_THRESHOLD).
"""
import os
import re
import unicodedata
from typing import List, Tuple

import jellyfish

# Raised from 0.70 (T-16): at 0.70, two patients sharing only birthdate,
# sex and a vaguely similar firstname could be flagged, flooding the
# review queue with false positives.
MATCH_THRESHOLD = float(os.environ.get("MPI_MATCH_THRESHOLD", "0.75"))

# Similarity floor applied when Metaphone codes agree but spelling differs.
PHONETIC_FLOOR = 0.85


def _norm(s) -> str:
    if not s:
        return ""
    # Transliterate diacritics (é→e, ñ→n) before stripping non-alphanumerics,
    # otherwise accented letters disappear entirely from the comparison key.
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _name_similarity(a: str, b: str) -> float:
    """Jaro-Winkler similarity on normalised names, floored by phonetics."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    score = jellyfish.jaro_winkler_similarity(a, b)
    if score < PHONETIC_FLOOR:
        try:
            if jellyfish.metaphone(a) == jellyfish.metaphone(b):
                score = PHONETIC_FLOOR
        except Exception:
            pass  # phonetics are a bonus signal only — never fail a match on it
    return round(score, 4)


def compute_match_score(a: dict, b: dict) -> float:
    """Return a score in [0.0, 1.0].  1.0 means certain MRN match."""
    if a.get("id") and a.get("id") == b.get("id"):
        return 1.0

    mrn_a = _norm(a.get("mrn", ""))
    mrn_b = _norm(b.get("mrn", ""))
    if mrn_a and mrn_b and mrn_a == mrn_b:
        return 1.0

    score  = _name_similarity(a.get("firstname", ""), b.get("firstname", "")) * 0.25
    score += _name_similarity(a.get("lastname",  ""), b.get("lastname",  "")) * 0.35

    dob_a  = (a.get("birthdate") or "").strip()
    dob_b  = (b.get("birthdate") or "").strip()
    if dob_a and dob_b and dob_a == dob_b:
        score += 0.30

    sex_a = _norm(a.get("sex", ""))
    sex_b = _norm(b.get("sex", ""))
    if sex_a and sex_b and sex_a == sex_b:
        score += 0.10

    return round(min(score, 0.99), 4)


def find_candidates(
    patient: dict,
    pool: List[dict],
    threshold: float = MATCH_THRESHOLD
) -> List[Tuple[dict, float]]:
    """Return (candidate, score) pairs above threshold, excluding self."""
    pid = patient.get("id")
    results = []
    for p in pool:
        if p.get("id") == pid:
            continue
        score = compute_match_score(patient, p)
        if score >= threshold:
            results.append((p, score))
    return sorted(results, key=lambda x: -x[1])
