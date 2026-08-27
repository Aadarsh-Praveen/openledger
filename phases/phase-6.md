# Phase 6 — Orchestration: GitHub Actions Pipeline

**Week:** 3 (close) / 4 (open)
**Estimated effort:** 6–10 hours
**Goal:** The full loop — ingest → build → quality → dashboard refresh — running on a
schedule in CI, with every scheduling constraint you already discovered wired in
correctly, and staleness detectable rather than silent.

## What this phase is really about

Little new code. The difficulty is entirely in the constraints Phases 1–5 surfaced,
which now all have to hold simultaneously in an unattended environment. A pipeline that
works when you run it by hand and fails silently at 3am is worse than no pipeline. The
measure of this phase is whether it fails **loudly and correctly** when something is
wrong, not whether it succeeds on the happy path.

## The constraints that must all hold at once

Each of these is documented already. The phase fails if any is missed.

| Constraint | Source | Consequence if ignored |
|---|---|---|
| Schedule **after** the ~02:00–02:03 UTC publish cycle | C1.1b | Runs before the cycle short-circuit as no-ops; look like a broken watermark |
| Soda and dbt **cannot overlap** on the DuckDB file | C3.1 | Exclusive lock; hard-fails in 0.065s, not a queue |
| Clear `target/partial_parse.msgpack` before builds | Phase 3×2, Phase 4 | Stale manifest → correct-looking wrong answers |
| `DBT_TARGET=prod DBT_PROFILES_DIR=.` for every `mf`/`dbt` call | C4.1 | Silent wrong-DuckDB-file → catalog-not-found |
| Watermark-advance ≠ run-success | C7-era + short-circuit work | A frozen watermark reports success forever |
| Pruned file bytes differ even when data doesn't | C5.8 | Cannot trigger redeploy on file hash; must trigger on data change |
| CI runs UTC by default | C3.3 is_settled bug | Any session-timezone assumption re-breaks in CI |
| Athena/CI has no local warehouse | — | The Iceberg warehouse and DuckDB marts live on the runner's filesystem per-run, or must be persisted |

## The architectural question this phase must answer first

**Where does state live between runs?** The pipeline is stateful — the watermark, the
checkpoint, the Iceberg warehouse, the DuckDB marts. GitHub Actions runners are
ephemeral: each run starts clean. So C6.1 must decide how state persists:

- Warehouse + watermark + checkpoint committed to the repo? (The 731MB warehouse is far
  too large; the 2.26MB pruned file is already committed.)
- Persisted to S3 or a GitHub artifact/cache between runs?
- Or does the scheduled job only run the **incremental + dashboard refresh** against
  state pulled from somewhere durable, with the full backfill remaining a local
  operation?

This is the load-bearing decision. Get it wrong and every scheduled run either starts
from zero or can't find its own state. Decide it before writing any workflow YAML.

---

## HUMAN-ONLY tasks

### H6.1 — Approve the state-persistence design
C6.1's decision determines the whole phase's shape and may incur small cost (S3
storage). Approve before workflows are built.

### H6.2 — GitHub secrets
The Socrata token (and any AWS credentials if state goes to S3) must be set as GitHub
Actions secrets. Claude Code cannot do this — it requires the repo settings UI. Claude
Code lists exactly what secrets are needed; you set them.

### H6.3 — Watch the first scheduled run
The first real cron fire is the moment truth arrives. Be available to read it. A green
checkmark is not proof — proof is the watermark advancing, the dashboard re-rendering,
and the staleness check passing.

---

## CLAUDE CODE tasks

### C6.1 — Decide and document state persistence (before any YAML)
Evaluate the options above against cost (must stay near-zero), the ephemeral-runner
constraint, and the fact that the full 731MB warehouse cannot live in git. Recommend an
approach. **Stop for H6.1.**

If the recommendation is "scheduled runs do incremental-only against S3-persisted
state," note that this partially overlaps Phase 7's S3 work — flag the sequencing so the
two phases don't build the same bucket twice.

### C6.2 — The staleness signal (design before wiring)
`dbt source freshness` checks whether bronze is fresh, but the deeper signal is whether
the **watermark advanced**. Design a check that distinguishes:
- Run succeeded, watermark advanced, new data absorbed → healthy
- Run succeeded, watermark unchanged, publish cycle genuinely produced nothing → benign
- Run succeeded, watermark unchanged, but data *should* exist → **alarm**

The third is the dangerous one and the hardest to detect. Use the measured cadence
(~11,905 rows/day steady state, the mass-touch pattern) to decide when "no advance" is
suspicious rather than normal. Report the logic before implementing.

### C6.3 — The ingest workflow
A scheduled workflow that:
- Fires **after** the publish cycle (choose the UTC cron time and justify it against the
  02:00–02:03 window plus settling margin)
- Restores state per C6.1
- Runs the incremental ingest with the existing short-circuit and checkpoint logic
- Persists updated state
- Runs the staleness check and **fails loudly** if it trips
- Never runs a full backfill — that stays a documented manual operation

### C6.4 — The build + quality workflow
After ingest, in strict sequence (never parallel, per the lock constraint):
1. Clear `partial_parse.msgpack`
2. `dbt build` with `DBT_TARGET`/`DBT_PROFILES_DIR` set correctly
3. `dbt source freshness`
4. Soda scan — **only after dbt fully releases the file**; assert compatibility first
5. The five operational detectors run and land in the scorecard

Any failure fails the workflow. Record durations — if the quality suite dominates, that
is a real operational fact.

### C6.5 — The dashboard refresh
- Rebuild the pruned DuckDB source from the updated marts (`build_dashboard_source.py`)
- Trigger the Vercel redeploy — via **data-change detection, not file hash** (C5.8: the
  file bytes differ every rebuild regardless of content). Decide the trigger: a
  commit-and-push of the pruned file, or a Vercel deploy hook fired only when a
  meaningful metric changed. Justify which.
- Confirm the deploy actually happened; a fire-and-forget with no verification can fail
  silently.

### C6.6 — Failure behavior and notification
Every workflow must **surface failure**, not swallow it. At minimum a failed run shows
red in the Actions tab with a readable reason. Consider a notification (GitHub can email
on failure natively; no extra service needed). The staleness alarm specifically must be
distinguishable from an ordinary infra failure.

Define the alert-on-N-consecutive-short-circuits threshold from Phase 3's reasoning, and
wire it.

### C6.7 — Prove it end to end
Do not wait for the natural cron. Trigger the workflows manually (`workflow_dispatch`)
and show a full green run: state restored, incremental ran, quality passed, dashboard
refreshed, staleness check passed. Then **deliberately break something** — feed a stale
watermark, or skip the partial-parse clear — and show the pipeline catches it and fails
loudly. A pipeline never seen to fail correctly is untested.

### C6.8 — Journal, metrics, commit
`docs/decisions.md`: the state-persistence decision, the cron timing justification, the
staleness logic, the redeploy-trigger choice, anything that behaved differently in CI
than locally (there will be something — CI always differs).
`docs/metrics.md`: per-stage durations in CI, full-run wall clock.

One commit: `Phase 6: GitHub Actions orchestration`.

---

## STOP GATE 6

| # | Criterion | Evidence required |
|---|---|---|
| 1 | State persistence works | Design approved; a run restores state and finds it |
| 2 | Cron fires after publish cycle | UTC time justified against the 02:00–02:03 window |
| 3 | Secrets configured | H6.2 done; no secret in any committed file or log |
| 4 | Ingest runs in CI, delta-only | Manual dispatch shows incremental, not backfill |
| 5 | Sequence respected | dbt and Soda never overlap; partial-parse cleared; DBT_TARGET set |
| 6 | Quality suite runs green in CI | All tests + detectors, durations recorded |
| 7 | Dashboard refreshes | Redeploy triggered by data change, verified to have happened |
| 8 | **Staleness alarm works** | Deliberately tripped, shown to fail loudly |
| 9 | **Pipeline fails correctly** | A deliberate break caught and surfaced red |
| 10 | Full run proven via dispatch | One green end-to-end run shown |
| 11 | First scheduled run watched | H6.3; watermark advanced, dashboard re-rendered |
| 12 | Journal, metrics updated | CI durations, all decisions recorded |
| 13 | One atomic commit | Message names Phase 6 |

**Load-bearing: 5, 8, 9.** The sequence constraints are what make it correct; the
staleness alarm and the fail-loudly behavior are what make it trustworthy unattended. A
pipeline that only demonstrates success has demonstrated nothing about the 3am case.

---

## What Phase 7 will do (context only — do not start)

The S3 + Glue + Athena Iceberg mirror producing the résumé phrasing, the
DuckDB-vs-Athena benchmark (latency, bytes scanned, dollars), the OPTIMIZE/VACUUM
maintenance demo, and the full README with the architecture diagram and the accumulated
engineering findings. Under $5 total AWS spend, workgroup scan-limit guardrailed. If
C6.1 put state in S3, coordinate so Phase 7 reuses that bucket rather than creating a
parallel one.
