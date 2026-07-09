# MPI Matching Benchmark

Accuracy benchmark for the deterministic patient matcher
(`services/mpi/matcher.py`), measured over a labelled synthetic corpus.
The executable source of truth is
[`tests/benchmarks/test_mpi_matching_bench.py`](../../tests/benchmarks/test_mpi_matching_bench.py);
this document records the methodology and the measured baseline. Keep both in
sync when either changes.

```bash
# Run the benchmark and see the report table
pytest tests/benchmarks -q -s
```

The suite needs no database, no Docker and no network; it scores 200 pairs in
well under a second.

## Why this exists

The matcher's score weights (firstname 0.25, lastname 0.35, birthdate 0.30,
sex 0.10, capped at 0.99 without an MRN match) and the operating threshold
(`MATCH_THRESHOLD = 0.75`, raised from 0.70 in T-16) were tuned by hand.
This benchmark turns that tuning into numbers — precision / recall / F1 at
0.70, 0.75 and 0.80 — and gates CI with a regression floor at 0.75 so a
future matcher change that silently degrades matching fails a test instead of
flooding the duplicate-review queue (or worse, merging strangers).

The benchmark only **measures** the matcher; it never tunes it. All calls go
through `matcher.compute_match_score` — never through `jellyfish` directly.

## Corpus methodology

200 hand-labelled pairs, 100 positives (`same_person=True`) and 100 negatives.
Every record carries exactly the fields `compute_match_score` consumes —
`firstname`, `lastname`, `birthdate`, `sex` — with `id=None` and `mrn=None` on
all records, because any shared id/MRN short-circuits the score to 1.0 and
would bypass the fuzzy logic under test.

### Positives (100)

| Category | n | Design | Measured score range |
|---|---|---|---|
| `diacritics` | 30 | French/accented spelling variants — René/Rene, Hélène/Helene, François/Francois, Aïcha/Aicha, Béchir/Bechir, Müller/Muller, Nguyễn/Nguyen — with all other fields identical. `_norm` transliterates diacritics, so these are exact after normalisation. | 0.99 (all) |
| `translit` | 35 | Arabic-name transliteration variants: Mohamed/Mohammed/Muhammad/Mohammad, Fatima/Fatma, Hocine/Houcine, Mustapha/Mostafa, Youssef/Yusuf, Meriem/Maryam; particle spacing/hyphenation Benali/Ben Ali/Ben-Ali, El Amrani/Elamrani, Al Idrissi/El Idrissi, Bou Azizi/Bouazizi, Abd El Kader/Abdelkader. Space/hyphen variants normalise to identical strings; spelling variants ride Jaro-Winkler or the Metaphone floor (0.85). One name field varies per pair. | 0.9167 – 0.99 |
| `typo` | 25 | Single-character edits and transpositions: Laurent/Laurnet, Dupont/Dupond, Catherine/Katherine, Antoine/Antione, Élise/Elsie, Leclerc/Leclercq. | 0.975 – 0.99 |
| `partial` | 10 | Same person with one weak field. 4 pairs missing `sex` (score 0.90); 3 pairs missing `birthdate` (score exactly 0.70 → false negatives at 0.75/0.80 — the recall ceiling); 3 nickname-grade firstnames with missing sex (Madeleine/Léna 0.7518, Joséphine/Fifine 0.7750, Élisabeth/Babette 0.8058 — the first two are FN at 0.80). | 0.70 – 0.90 |

### Negatives (100)

| Category | n | Design | Measured score range |
|---|---|---|---|
| `easy` | 60 | Different firstname, lastname, birthdate and sex. | 0.00 – 0.39 |
| `shared-dob` | 25 | Different people who share **birthdate and sex** (worth 0.40 on their own) but have dissimilar names — the false-positive trap that motivated raising the threshold from 0.70 (T-16). Eleven pairs are calibrated into [0.70, 0.75): FPs at the old threshold, clean at 0.75. None reaches 0.75. | 0.40 – 0.7474 |
| `twins` | 15 | **Known-FP probes**: same lastname + birthdate + sex, different firstname (e.g. Yacine/Karim Benali) — the twin-siblings case. Lastname+dob+sex alone score 0.75, so any firstname similarity pushes these past 0.80. All 15 are false positives at every threshold, including 0.80. This is a documented matcher weakness, not a corpus bug; the precision floors budget for all 15. | 0.868 – 0.9625 |

### Labelling judgement calls

- **Hassan/Hossein** (and similar lookalike given names): labelled
  `same_person=False`. Hasan and Hossein/Husayn are distinct given names —
  famously, brothers' names — not transliteration variants of one name, unlike
  Mohamed/Muhammad which is a single name (محمد) romanised differently.
  Measured consequence: Metaphone pins their similarity at the 0.85 floor, so
  with a shared birthdate+sex the pair exceeds 0.75 for *any* lastname pair.
  They therefore live in the `twins` known-FP category (Hassan/Hossein
  Cherigui) rather than `shared-dob`, which must stay FP-free at 0.75.
- **Fatima/Fatma, Meriem/Maryam, Mohamed/Mohammed/Muhammad**: labelled
  `same_person=True` — common romanisation variants of the same Arabic name,
  routinely seen for the same patient across documents.
- **Yasmina/Yamina-style near-identical different names** were deliberately
  kept out of `shared-dob` (they score like twins and would break the
  category's "clean at 0.75" invariant); the twin category covers that
  failure mode explicitly.

## Measured baseline — 2026-06-12

`jellyfish` 1.x, `MPI_MATCH_THRESHOLD` unset (default 0.75).

| Threshold | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| 0.70 | 100 | 26 | 0 | 74 | 0.7937 | 1.0000 | 0.8850 |
| **0.75** | **97** | **15** | **3** | **85** | **0.8661** | **0.9700** | **0.9151** |
| 0.80 | 95 | 15 | 5 | 85 | 0.8636 | 0.9500 | 0.9048 |

Per-category recall (positives) / FP-rate (negatives):

| Category | n | @0.70 | @0.75 | @0.80 |
|---|---|---|---|---|
| diacritics | 30 | 1.00 | 1.00 | 1.00 |
| translit | 35 | 1.00 | 1.00 | 1.00 |
| typo | 25 | 1.00 | 1.00 | 1.00 |
| partial | 10 | 1.00 | 0.70 | 0.50 |
| easy | 60 | 0.00 | 0.00 | 0.00 |
| shared-dob | 25 | 0.44 | 0.00 | 0.00 |
| twins | 15 | 1.00 | 1.00 | 1.00 |

Reading the table:

- **0.75 dominates 0.70 on this corpus**: moving 0.70 → 0.75 trades 3 recall
  points (the missing-birthdate pairs) for 11 fewer shared-dob false
  positives — exactly the T-16 rationale, now measured.
- **0.80 buys nothing**: the only FPs left at 0.75 are the twin probes, which
  survive 0.80 anyway, while recall drops another 2 points. There is no reason
  to raise the threshold further with the current weights.
- **All 15 false positives at the operating threshold are twins** (same
  lastname + dob + sex). Improving on this requires a matcher change (e.g.
  penalising firstname *dissimilarity* instead of only rewarding similarity),
  not a threshold change.
- **All 3 false negatives at 0.75 are records missing a birthdate** — exact
  names + sex alone top out at 0.70. Data completeness upstream matters more
  than fuzzy-matching cleverness here.

## CI regression gate

`test_regression_floor_at_match_threshold` asserts, at the configured
`MATCH_THRESHOLD` (and asserts that it is still 0.75, so a silent env/default
drift also fails):

| Metric @0.75 | Measured | Floor |
|---|---|---|
| Precision | 0.8661 | **>= 0.84** |
| Recall | 0.9700 | **>= 0.95** |

plus monotonicity sanity (`precision@0.80 >= precision@0.70` and
`recall@0.70 >= recall@0.80`), and `test_threshold_ordering_documented`
asserts the shared-dob category is FP-free at 0.75 but not at 0.70.

If a matcher change trips a floor, that is the benchmark working as intended:
either fix the regression, or — if the change is a deliberate trade-off —
re-measure, update the floors *and* this document in the same commit, and say
so in the commit message.

If a matcher improvement makes the `twins` probes stop flagging (per-category
FP-rate drops below 1.00), precision will rise well above the floor:
re-calibrate the floors upward and update the baseline table here.

## Limitations

### Non-NFKD-decomposable letters (ø, æ, ß, Ł) — corpus blind spot

`_norm` (`services/mpi/matcher.py:37-44`) transliterates diacritics by
NFKD-decomposing and stripping *combining marks* — which only works for
letters that decompose into base-letter + mark (é, ü, ç, ñ, ễ, …). Letters
that are **atomic code points with no NFKD decomposition** are not
transliterated; they fall through to the `[^a-z0-9]` strip and are simply
**deleted** from the comparison key (the original F#50 failure mode, for
this letter class). Measured as of 2026-06-12:

| Input | `_norm` output | Letter lost |
|---|---|---|
| `Sørensen` | `srensen` | ø (→ should be `o`) |
| `Strauß` | `strau` | ß (→ should be `ss`) |
| `Sæther` | `sther` | æ (→ should be `ae`) |
| `Łukasz` | `ukasz` | Ł (→ should be `l`) |

These pairs still match *today* — Jaro-Winkler absorbs a single dropped
letter (Sørensen/Sorensen name-similarity 0.9625; Strauß/Strauss 0.9429;
Łukasz/Lukasz 0.9444; full-record scores comfortably above 0.75) — but:

- All 30 `diacritics` corpus pairs use NFKD-decomposable letters, so **the
  benchmark cannot detect a regression in the ø/æ/ß/Ł class**. A future
  `_norm` change that mishandles atomic letters would sail through the CI
  floors.
- The fix (explicit replacements `ø→o`, `æ→ae`, `ß→ss`, `ł→l` *before* the
  combining-mark strip, plus corpus positives in this letter class) is a
  deliberate matcher change: per the rules above, land it together with
  re-measured floors and an updated baseline table — not as a drive-by.

### Twin siblings

Same lastname + birthdate + sex scores 0.75 before any firstname signal —
the 15 `twins` probes are false positives at every threshold (see the
baseline table). This is a weights problem, not a threshold problem; it is
budgeted into the precision floors and documented in ADR-0006.
