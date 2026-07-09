# Task Planning — how work is tracked in OpenHIS

This directory is the single source of truth for **what is being worked on,
what is planned, and what went wrong**. No external tracker: everything is
markdown, versioned with the code, reviewable in PRs.

**Start here → [INDEX.md](INDEX.md)** — the live board. It is the ONLY place
where task status lives. Plan files define tasks; the index tracks them.

---

## Artefacts

| Artefact | Role | Lifecycle |
|---|---|---|
| [`INDEX.md`](INDEX.md) | **The board.** Epics, task statuses (todo / in progress / blocked / done), defect registry summary, archive index. | Updated in the same PR as the work it reflects. |
| `PLAN-<yyyy-mm>-<slug>.md` | **Task definitions** for a work campaign, grouped under one or more epics. No status inside — the index owns status. | Created when a campaign starts; moved to `archive/` when its epic(s) are done or abandoned. |
| `test-defect-report-<date>.md` | **Defect registry.** Every confirmed defect gets a `DEF-NNN` ID; e2e tests reference these IDs in `xfail` markers. Full forensics live here; the index carries only the status summary. | Append-only; defects are marked fixed/closed with a date, never deleted. |
| `archive/` | Superseded or completed plans, kept for traceability. Indexed at the bottom of `INDEX.md`. | Write-once. |

`REMEDIATION_PLAN.md` (T-01…T-35, 2026-04 audit) and `4_TODO_list.md`
(OBJ 1–8) predate this convention and remain as **backlogs**: reservoirs of
defined-but-unscheduled work. Scheduling a backlog item = referencing its ID
from an active plan task and adding it to the index.

## IDs

| Prefix | Meaning | Defined in |
|---|---|---|
| `EP-NN` | Epic — a coherent outcome, groups tasks | `INDEX.md` |
| `S-NN` / `V-NN` / `R-NN` | Salvage / Validation / Release tasks | active plan |
| `T-NN` | Audit remediation tasks | `REMEDIATION_PLAN.md` |
| `OBJ-N.n` | Product backlog objectives | `4_TODO_list.md` |
| `DEF-NNN` | Defects | defect registry |

## Statuses

| Status | Meaning | Notation in the index |
|---|---|---|
| `TODO` | Defined, not started | `TODO` |
| `WIP` | In progress — a branch exists | `WIP` (+ branch name) |
| `BLOCKED` | Cannot proceed | `BLOCKED — <why / on which ID>` |
| `DONE` | Merged to `master`, acceptance checks passed | `DONE <yyyy-mm-dd>` |
| `ARCHIVED` | Epic/plan closed or abandoned; file moved to `archive/` | `ARCHIVED <yyyy-mm-dd>` |

Rules: a task is `DONE` only when merged AND its acceptance criteria pass —
"works on my branch" is `WIP`. A `BLOCKED` entry must say what unblocks it.
An epic is `DONE` when all its tasks are; it is then archived together with
its plan file.

## Task anatomy (in plan files)

```
### <ID>: <imperative title>
**Epic:** EP-NN · **Priority:** P0|P1|P2 · **Depends on:** <IDs or —>
**Branch:** <type>/<id>-<slug>
**Files:** paths to touch
**Plan:** concrete steps
**Acceptance:** verifiable checks (commands + expected output)
```

## Per-task workflow

1. Pick the highest-priority `TODO` task in the index whose dependencies are `DONE`.
2. Flip it to `WIP` in the index (may be committed with the work itself).
3. One branch per task, named as in the task block, off `master`. Do what the
   task says — nothing else; adjacent messes get their own task.
4. Run the acceptance checks. `pytest tests/unit tests/integration` must pass.
5. One PR per branch; reference the task ID and any `DEF-NNN`/`T-NN` in the
   body. **The same PR flips the index entry to `DONE`.**
