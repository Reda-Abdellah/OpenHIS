# ADR 0006 — MPI Match Threshold 0.75, Diacritic Transliteration, Phonetic Floor

- Status: Accepted
- Date: 2026-06-12
- Relates to: ADR 0003 (MPI as identity spine), T-16 (matcher quality remediation)
- Code: [`services/mpi/matcher.py`](../../services/mpi/matcher.py)
- Evidence: [`docs/benchmarks/mpi-matching.md`](../benchmarks/mpi-matching.md)
  (executable source: `tests/benchmarks/test_mpi_matching_bench.py`)

## Context

The MPI duplicate-detection matcher (`services/mpi/matcher.py`) scores patient
pairs with hand-tuned weights — firstname 0.25, lastname 0.35, birthdate 0.30,
sex 0.10, capped at 0.99 without an MRN match — and flags candidates at or
above `MATCH_THRESHOLD`. Three problems surfaced under T-16:

1. **The 0.70 threshold flooded the review queue.** Birthdate + sex alone are
   worth 0.40, so two strangers sharing a birthdate and sex needed only a
   vaguely similar firstname to cross 0.70. The labelled benchmark corpus
   measured **26 false positives at 0.70** — 11 of them in the
   `shared-dob` category built from exactly this failure mode.
2. **Diacritics were dropped, not transliterated.** The original `_norm`
   deleted any non-`[a-z0-9]` character, so `"René"` became `"ren"` and
   accented spellings of the same name were penalised by edit distance for
   a difference that is purely orthographic.
3. **Phonetically identical spellings relied on edit distance alone.**
   Catherine/Katherine-style variants have no signal beyond Jaro-Winkler,
   which degrades quickly for transliteration variants of Arabic names
   (Youssef/Yusuf, Mostafa/Mustapha) that are routine in the target
   deployments.

## Decision

1. **Raise the default duplicate-flag threshold from 0.70 to 0.75**, kept
   overridable per deployment via the `MPI_MATCH_THRESHOLD` env var
   (`matcher.py` reads it at import time; `find_candidates` uses it as the
   default `threshold`).
2. **Transliterate diacritics before stripping non-alphanumerics.** `_norm`
   applies `unicodedata.normalize("NFKD", s)` and removes combining marks, so
   `é→e`, `ü→u`, `ç→c`, `ñ→n` and `"René"` normalises to `"rene"` — accented
   spellings of the same name now score **1.0** name-similarity.
3. **Add a Metaphone phonetic floor.** When two normalised names have equal
   Metaphone codes but a Jaro-Winkler similarity below `PHONETIC_FLOOR =
   0.85`, the similarity is floored at 0.85 instead of trusting edit distance
   alone (`_name_similarity`). Phonetics are a bonus signal only — a
   `jellyfish.metaphone` failure never fails the match.

### Why 0.75 (measured, not guessed)

The benchmark corpus (200 labelled pairs, methodology in
[`docs/benchmarks/mpi-matching.md`](../benchmarks/mpi-matching.md)) measured,
as of 2026-06-12:

| Threshold | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| 0.70 | 100 | 26 | 0 | 74 | 0.7937 | 1.0000 | 0.8850 |
| **0.75** | **97** | **15** | **3** | **85** | **0.8661** | **0.9700** | **0.9151** |
| 0.80 | 95 | 15 | 5 | 85 | 0.8636 | 0.9500 | 0.9048 |

- Moving 0.70 → 0.75 trades 3 recall points (records **missing a birthdate**,
  whose exact-name score tops out at 0.70) for 11 fewer shared-dob false
  positives. F1 improves from 0.8850 to 0.9151.
- 0.80 buys nothing: the 15 FPs remaining at 0.75 are the deliberate
  twin-sibling probes (same lastname + birthdate + sex), which survive 0.80
  anyway, while recall drops another 2 points. Beating the twins requires a
  matcher change (penalising firstname *dissimilarity*), not a higher
  threshold.

CI enforces this decision: `tests/benchmarks/test_mpi_matching_bench.py`
asserts precision ≥ 0.84 and recall ≥ 0.95 at the configured threshold *and*
asserts the default is still 0.75, so silent drift fails the build.

### Back-compatibility

Existing MPI deployments whose review-queue workflows were calibrated against
0.70 can pin the old behaviour without a code change:

```bash
MPI_MATCH_THRESHOLD=0.70
```

New deployments get 0.75 by default. Any per-call override remains available
through the `threshold` parameter of `find_candidates`.

## Consequences

- Fewer false positives in the duplicate-review queue: at 0.75 the
  `shared-dob` trap category is FP-free on the benchmark corpus.
- A small, *known* recall cost: pairs with a missing birthdate and exact
  names score exactly 0.70 and are no longer flagged. Upstream data
  completeness matters more than fuzzy-matching cleverness here.
- Accented and transliterated spellings (René/Rene, Müller/Muller,
  Youssef/Yusuf) are matched robustly; the phonetic floor pins
  Metaphone-equal pairs at ≥ 0.85 name-similarity.
- Known residual weaknesses, documented in the benchmark's Limitations
  section: twin siblings are structurally indistinguishable to the current
  weights, and letters with **no NFKD decomposition** (ø, æ, ß, Ł) are still
  dropped by `_norm` rather than transliterated.

## Verification

- `tests/unit/mpi/test_matcher.py` — pure-logic tests incl. diacritic
  transliteration (René/Rene = 1.0), the phonetic floor and the
  `MPI_MATCH_THRESHOLD` override.
- `tests/benchmarks/test_mpi_matching_bench.py` — 200-pair labelled corpus,
  CI regression floors at the operating threshold (wired in
  `.github/workflows/ci.yml`).
