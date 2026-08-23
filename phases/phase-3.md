# Phase 3 — Data Quality: Conventional Foundation + Operational Signal

**Week:** 2 (back half)
**Estimated effort:** 8–12 hours
**Goal:** Four categories of quality checking proven end to end, plus a scorecard that
tracks what actually varies rather than what reliably reports zero.

## The thesis

Most data-quality tooling checks whether data is *malformed*. This layer also checks
whether the data is *telling you something different than it was yesterday*.

Null checks catch broken pipelines. Composition and drift checks catch broken
**assumptions** — and Phase 2 produced the proof that the second category exists here:
the DHS bulk-closure sweep (11,372 rows, one `:updated_at`, two template resolution
texts) would pass every conventional check ever written, while silently corrupting any
SLA metric that includes it.

Build both layers. The conventional one demonstrates the standard toolkit; the
operational one demonstrates judgment. Neither substitutes for the other.

---

## Carried forward from Phases 1–2

| Finding | Value | Role in Phase 3 |
|---|---|---|
| Out-of-bounds coordinates | Exactly 0.0000%, all 3 years | Check it, expect zero, say so plainly |
| closed < created | 0.0242% stable | Already a WARN test; becomes a tracked rate |
| Missing coordinates | ~1.72% blended; 1.15%→2.52% by year | Explained by composition, not degradation |
| DHS bulk closure | 11,372 rows, one date, 2 templates | The detector's founding case |
| Pothole/STREET CONDITION | ~5–6× March surge, chronically poor geocoding | The composition-drift founding case |
| Vocabulary drift | complaint_type 189→196, agency 14→16 | Monitored signal, not a test failure |
| Settlement completeness | ~93% at 45 days | Tracked; drift below it invalidates the cutoff |
| Soda/DuckDB conflict | `soda-core-duckdb` pins `duckdb<1.1.0` vs 1.5.5 | Must be solved before Soda runs |

---

## The dependency that has to be solved first

`soda-core-duckdb` hard-pins `duckdb<1.1.0`; the project runs 1.5.5. This was logged in
Phase 0 and deferred to here. It is now blocking.

Options, in rough order of preference — evaluate empirically, do not assume:
1. **Separate virtualenv** for Soda, invoked as a subprocess. Nothing requires Soda and
   dbt to share a Python process. Clean boundary, costs a second env.
2. **Check whether the pin still holds** in the current Soda release. It may have moved.
3. **Soda against exported Parquet** rather than the live DuckDB connection.
4. **Drop Soda for `dbt_expectations`**, which covers distributional checks natively and
   avoids the conflict entirely.

Option 4 is a legitimate outcome, not a retreat — but it costs the "Soda Core" line.
Decide on evidence and record the reasoning either way.

---

## HUMAN-ONLY tasks

### H3.1 — Approve the Soda resolution
C3.1 reports which option works. Approve before anything is built on it.

### H3.2 — Approve detector thresholds
C3.5's thresholds must come from measured historical variance, not round numbers. A
detector that fires constantly gets ignored, which is worse than not having it. Review
and approve the proposed thresholds and their false-positive rates.

---

## CLAUDE CODE tasks

### C3.1 — Resolve the Soda dependency conflict
Test the options above in order. Report what actually works, the tradeoff, and a
recommendation. **Stop for H3.1.**

### C3.2 — Model contracts across all models
Phase 2 put contracts on marts only. Extend to staging and intermediate: enforced
column names, types, and constraints at build time.

Deliberately break one contract, show the build fails, restore it. Record the failure
output — a contract that has never been observed failing is untested. Report total
contract count and columns covered.

### C3.3 — dbt unit tests on the logic that matters
Unit tests validate transformation *logic* against static mock inputs, distinct from
data tests. Cover the cases where being wrong is expensive and silent:

- `resolution_hours` when closed normally
- `resolution_hours` when `closed_date` precedes `created_date` (the 0.02% defect)
- `is_censored` for an open request
- `is_settled` at the exact 45-day boundary, and one day either side
- A request closed but not yet settled — must yield null resolution, not a value
- Timezone handling at a day boundary, given `created_date` is naive Eastern

The boundary cases are the point. Off-by-one in `is_settled` would shift every
recent-period metric and produce no error anywhere.

### C3.4 — Distributional checks
Whatever C3.1 resolved to. Checks that watch shape, not nulls:

- Row-count volume per day against the historical band
- `complaint_type` distribution vs the trailing baseline
- `agency` distribution vs the trailing baseline
- Missing-coordinate rate against its measured range
- `resolution_hours` distribution for settled requests
- Closure rate by cohort age

Every threshold derived from measured variance in the 24-month history. State the
observed range and the chosen bound for each.

### C3.5 — The operational detectors

Four detectors, each with a documented founding case or measured baseline.

**a. Bulk-closure detector.** Fires when N+ requests from one agency share a single
`:updated_at` value and a small set of resolution-description templates. Calibrate on
the DHS event: it must catch that. Then run across all 24 months and report **how many
other sweeps exist** — this is the most interesting question in the phase, and nobody
has asked it yet.

**b. Composition-drift detector.** Fires when a complaint type's share of monthly
volume moves beyond its historical band. Calibrate on the STREET CONDITION March
surge. Must distinguish recurring seasonal movement from genuine anomaly — pothole
season recurring every spring should not fire every spring after the first.

**c. Vocabulary-drift monitor.** New `complaint_type`, `descriptor`, or `agency` values
appearing. Not a failure — a notification with the new member, first-seen date, and
volume. Turns the `accepted_values` trap into a monitored signal.

**d. Settlement-completeness tracker.** Recompute the 45-day curve on a rolling basis.
If completeness at 45 days drifts below the measured ~93%, the cutoff is stale and
every recent-period metric is more biased than documented.

**Report false-positive rates.** Run each detector across the full 24 months and count
firings. A detector firing on 20 of 24 months is not a detector. Tune, or report
honestly that it is too noisy to be useful — a documented negative result is
acceptable; an untuned detector shipped as if it worked is not.

**Stop for H3.2** with proposed thresholds and firing rates before wiring detectors
into the scorecard.

### C3.6 — The DQ scorecard mart
`fct_data_quality_checks` — one row per check per run date. Columns: check name,
category (contract / unit / distributional / detector), grain, measured value,
threshold, pass/warn/fail, and run timestamp.

Design it so **the dashboard can show trend, not just current state.** A quality metric
without history is a smoke alarm; with history it is a diagnostic.

Include the checks that report zero. "Out-of-bounds coordinates: 0.0000%, 24 months,
7.5M rows" is a finding, and showing a flat line at zero next to a metric that moves is
more informative than hiding it.

### C3.7 — The DHS exclusion decision
The bulk-closure sweep corrupts DHS's SLA numbers — those 11,372 rows measure an
administrative action, not response performance.

Decide and implement: a flag on the fact table, an exclusion in the metric definition,
or both. Whatever the choice, DHS's SLA must be reportable both with and without the
sweep, and the difference recorded in `docs/findings.md`. **State the delta** — that
number is the argument for why the detector exists.

### C3.8 — Wire quality into the build
Data tests, unit tests, contracts, and distributional checks all runnable in one
command. Detectors may run separately if they are slow — document which and why.

Record durations. If the quality suite takes longer than the build, that is a real
operational fact for Phase 6's schedule.

### C3.9 — Journal, metrics, commit
`docs/decisions.md`: the Soda resolution, every threshold and its derivation, the DHS
exclusion decision, any detector abandoned as too noisy.
`docs/metrics.md`: counts by category, pass rates, detector firing rates and
false-positive rates, suite duration.
`docs/findings.md`: additional sweeps found by detector (a), the DHS SLA delta.

One commit: `Phase 3: data quality contracts, unit tests, and operational scorecard`.

---

## STOP GATE 3

| # | Criterion | Evidence required |
|---|---|---|
| 1 | Soda conflict resolved | Option chosen, tested, H3.1 approved |
| 2 | Contracts across all layers | Count and columns covered; a deliberate failure demonstrated |
| 3 | Unit tests cover boundary cases | Count; the 45-day boundary tested at exactly 45, 44, 46 |
| 4 | Distributional checks running | Count; each threshold traced to measured variance |
| 5 | Bulk-closure detector catches DHS | Confirmed on the founding case |
| 6 | **Other sweeps found or ruled out** | Count across 24 months, with detail on any found |
| 7 | Composition detector catches STREET CONDITION | Confirmed, and does not fire every spring |
| 8 | Vocabulary monitor works | New members listed with first-seen dates |
| 9 | Settlement tracker running | Current completeness at 45 days |
| 10 | **False-positive rates reported** | Firings per detector across 24 months; anything too noisy documented as such |
| 11 | Thresholds approved | H3.2 recorded |
| 12 | Scorecard mart built | Row count, categories, history depth |
| 13 | DHS SLA delta quantified | With and without the sweep, stated |
| 14 | Suite runnable in one command | Command and duration |
| 15 | One atomic commit | Message names Phase 3 |

**Load-bearing: 3, 6, 10.** A wrong `is_settled` boundary corrupts every recent metric
silently. Criteria 6 and 10 are what separate a real detector from a demo — a detector
validated only on the case it was built from is a lookup, and an untuned one is noise.

---

## What Phase 4 will do (context only — do not start)

Semantic layer in open-source MetricFlow against dbt-core: metrics defined once, with
the censoring and settlement rules encoded in the definitions so no consumer can
compute a resolution-time metric that silently includes unsettled requests.
