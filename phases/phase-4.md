# Phase 4 — Semantic Layer in Open-Source MetricFlow

**Week:** 3 (front)
**Estimated effort:** 6–9 hours
**Goal:** Metrics defined once, with the censoring and settlement rules baked into the
definitions so no consumer can compute a resolution-time metric that silently includes
unsettled or bulk-closed requests.

## Why this phase matters for this project specifically

The semantic layer is usually pitched as "single definition of a metric so two
dashboards agree." True, but for OpenLedger it does something sharper: it is where the
hard-won correctness from Phases 2–3 gets *enforced against consumers*.

Three ways to compute median resolution time are wrong, and you've documented all
three: including censored rows (nulls or zeros corrupt it), including unsettled rows
(publish lag biases it down), and including the DHS undated-closure backlog (measures
an administrative artifact, not performance). A raw SQL user hits all three by default.
A metric defined once, correctly, means every consumer gets the right answer without
knowing the traps exist. That is the actual argument for a semantic layer, and this
project can demonstrate it with real stakes rather than a toy revenue example.

## The verification that gates everything

**MetricFlow's open-source status and DuckDB support are load-bearing and must be
confirmed empirically before any metric is written.** The plan asserts MetricFlow is
Apache 2.0 (Coalesce 2025) and runs on dbt-core against DuckDB with no dbt Cloud. If
any part of that is wrong on the installed versions, the phase changes shape. C4.1
proves it or surfaces the fallback before time is spent on metric definitions.

---

## Carried forward

| Fact | Consequence for metrics |
|---|---|
| `resolution_hours` null when censored | Aggregations must handle null correctly, not coerce |
| 45-day settlement cutoff, ~93% complete | A resolution metric must filter to settled requests |
| DHS undated backlog, 17,356 rows, bounded | Excludable via `is_undated_closure`; SLA delta +17.65pp |
| Closure rate must accompany latency | Every resolution metric needs a companion coverage metric |
| Volume flat, composition varies | Share-of-volume metrics matter more than volume metrics |
| MetricFlow targets DuckDB, not Athena | Semantic layer runs on the local DuckDB marts |
| `dim_date`, 4 dimensions exist | Semantic models bind to these |

---

## HUMAN-ONLY tasks

### H4.1 — Approve the MetricFlow verification outcome
C4.1 confirms the toolchain works or reports the fallback. Approve the path before
metric definitions are written.

### H4.2 — Approve the metric definitions
The metric list encodes analytical decisions — which filters are baked in, what
"resolution time" officially means for this project. Review and approve before they
become the canonical definitions.

---

## CLAUDE CODE tasks

### C4.1 — Verify MetricFlow on dbt-core + DuckDB (before anything else)

1. Install MetricFlow / dbt's semantic interfaces. Pin from actual resolution; record
   in `docs/versions.md`. Confirm the license on the installed package is Apache 2.0.
2. Confirm it operates against **dbt-core** with no dbt Cloud account or credentials.
3. Define one trivial semantic model and one trivial metric, and query it end to end
   via `dbt sl query` / `mf query` against the DuckDB marts. Capture the generated SQL.
4. Confirm the generated SQL runs against DuckDB and returns correct numbers matching a
   hand-written query.
5. If any step fails — MetricFlow gated behind Cloud, no DuckDB adapter, version
   conflict with dbt-duckdb 1.11 — **stop and report.** The documented fallback is
   Cube (MIT, self-hosted, DuckDB connector), which gives a live metrics API but costs
   the "dbt MetricFlow" resume line. Do not silently switch; report and let H4.1 decide.

**Stop for H4.1.**

### C4.2 — Semantic models over the marts
Define semantic models binding to `fct_service_requests` and the four dimensions.
Declare entities (the join keys), dimensions (categorical and time), and measures
(the aggregatable columns). The fact's grain — one row per `unique_key` — is what makes
the measures well-defined; state it.

Time dimension binds to `created_date` under the documented Eastern-local rule. If
MetricFlow needs a primary time dimension, `created_date` is it.

### C4.3 — The metrics, with correctness baked in

Define these. The filters are the point — they are what a raw SQL user would omit.

**Resolution-time metrics (settled + closed only, backlog excluded):**
- `median_resolution_hours` — filtered to `is_settled AND is_closed AND NOT is_undated_closure`
- `p90_resolution_hours` — same filter
- These must be *incapable* of including censored, unsettled, or backlog rows. That
  invariant is the deliverable.

**Coverage metrics (the companions that keep latency honest):**
- `closure_rate` — closed / total at a given grain
- `settlement_rate` — settled / total, so a recent period reads as provisional
- `censored_count` — count of open requests

**Volume and composition:**
- `request_count` — total requests
- `complaint_type_share` — share of volume by type, the metric the seasonality finding needs

**A metric that exposes the trap:**
- `naive_median_resolution_hours` — deliberately WITHOUT the settlement/backlog filters,
  defined once so the dashboard can show the wrong number next to the right one and
  quantify the gap. This is pedagogy: the delta between naive and correct is the
  argument for the whole layer. Label it unmistakably as the incorrect version.

### C4.4 — Prove the invariant
Query `median_resolution_hours` several ways — by agency, by borough, by month — and
confirm no result path can pull in a censored, unsettled, or backlog row. Then show
`naive_median_resolution_hours` for the same cuts and record the delta.

The DHS case is the sharpest demonstration: naive vs correct for DHS should differ by
roughly the +17.65pp closure-rate story translated into resolution hours. State it.

### C4.5 — Metric-vs-handwritten reconciliation
For each metric, run the MetricFlow version and an independent hand-written DuckDB query
at the same grain. They must match. Any discrepancy is either a metric definition bug or
a misunderstanding of the generated SQL — resolve it, don't paper over it. Record the
reconciliation.

### C4.6 — Document the metrics as an interface
A consumer (including Phase 5's dashboard) should be able to read what each metric
means, what filters it enforces, and what grains it supports without reading SQL. Emit
or write this — the semantic layer's value is that metrics are legible, so prove it.

### C4.7 — Journal, metrics, commit
`docs/decisions.md`: the MetricFlow verification result, why each filter is baked into
which metric, the naive-vs-correct design choice.
`docs/metrics.md`: metric count, reconciliation results, the naive-vs-correct deltas.
`docs/findings.md`: the naive-vs-correct resolution-time gap, especially for DHS.

One commit: `Phase 4: MetricFlow semantic layer with censoring rules enforced`.

---

## STOP GATE 4

| # | Criterion | Evidence required |
|---|---|---|
| 1 | MetricFlow verified on dbt-core + DuckDB | C4.1; Apache 2.0 confirmed; no Cloud; generated SQL captured |
| 2 | Toolchain path approved | H4.1 recorded |
| 3 | Semantic models bind to marts | Fact + 4 dims, entities/dimensions/measures declared |
| 4 | Metrics defined | Count; the resolution metrics carry the settlement/backlog filters |
| 5 | Metric definitions approved | H4.2 recorded |
| 6 | **Correctness invariant proven** | No query path pulls a censored/unsettled/backlog row into a resolution metric |
| 7 | Naive-vs-correct gap quantified | Delta by agency/borough/month; DHS stated |
| 8 | Every metric reconciles to hand-written SQL | Per-metric match; any discrepancy resolved |
| 9 | Metrics documented as an interface | Readable without reading SQL |
| 10 | Journal, metrics, findings updated | Real numbers |
| 11 | One atomic commit | Message names Phase 4 |

**Load-bearing: 1, 6, 8.** If MetricFlow doesn't work as assumed, the phase pivots. If
the invariant isn't provable, the layer isn't enforcing anything. If metrics don't
reconcile, they're wrong.

---

## What Phase 5 will do (context only — do not start)

Evidence.dev dashboard on DuckDB, consuming these metrics: agency SLA leaderboard with
closure-rate context, geographic equity, compositional seasonality, and a data-quality
panel surfacing the detectors and the naive-vs-correct gap. Deployed to Vercel as a
free, always-on public URL.
