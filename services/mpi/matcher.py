"""
Deterministic patient matching algorithm.

Score weights:
  MRN exact match   → 1.0  (immediate certain match)
  firstname         → 0.25  (Jaro-Winkler similarity)
  lastname          → 0.35  (Jaro-Winkler similarity)
  birthdate exact   → 0.30
  sex exact         → 0.10
  max without MRN   → 0.99 (never reaches 1.0 to distinguish from MRN match)

Threshold for duplicate flag: 0.70
"""
import re
from typing import List, Tuple

import jellyfish


def _norm(s) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _name_similarity(a: str, b: str) -> float:
    """Jaro-Winkler similarity on normalised name strings."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return round(jellyfish.jaro_winkler_similarity(a, b), 4)


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
    threshold: float = 0.70
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
