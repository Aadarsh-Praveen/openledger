# Phase 2 — dbt Modeling: Staging → Intermediate → Star Schema

**Week:** 2
**Estimated effort:** 8–12 hours
**Goal:** A dimensional model over bronze that answers real questions about agency
performance and geographic equity, with the censoring and lag problems handled
explicitly rather than discovered later.

## Carried forward from Phase 1

| Fact | Value | Consequence for Phase 2 |
|---|---|---|
| Bronze row count | 7,533,132 (as of run 6) | Reconciliation target for staging |
| Grain guarantee | Upsert on `unique_key` | Uniqueness is by construction — still test it |
| `created_date` timezone | Naive, **inferred** Eastern local | Inferential evidence only; must be stated as an assumption everywhere |
| `:updated_at` | UTC, offset-aware | The only unambiguous timestamp |
| Publish lag | 2.53 d floor, 6.71 d median, 92 d tail | Recent closures are systematically under-observed |
| Still-open share | 23.37% of last-30-day rows | Resolution time is right-censored |
| Authoritative schema | 48 columns + `:updated_at` | From metadata endpoint, not row samples |
| Vocabulary drift | complaint_type 189→196, agency 14→16 | Dimensions must absorb new members without breaking |
| Yearly volume | ~305k/month, flat | Seasonality is compositional, not volumetric |
| DuckDB Iceberg read | Needs table root + `unsafe_enable_version_guessing` | May complicate the dbt source definition |

---

## Three problems this phase must solve deliberately

### 1. Right-censoring in resolution time

23.37% of recent requests are still open. Computing median `resolution_hours` over
closed rows only **systematically understates it**, because slow-resolving requests
are disproportionately still open and therefore excluded. The bias worsens the more
recent the period, which is exactly where a dashboard draws the eye.

The mart must make this visible, not average it away. At minimum: every
resolution-time metric carries a companion `closure_rate` and `censored_count` at the
same grain, so a low median accompanied by a low closure rate reads as incomplete
rather than fast.

### 2. Publish lag compounding the censoring

A request closed three days ago may not yet be *published* as closed. So recent
periods are biased downward twice: once by genuine open requests, once by closures
not yet visible. Define an **observation cutoff** — a date before which closure data
is considered settled — and expose it as a documented model configuration. The C1.1
median lag of 6.71 days is the starting evidence; justify whatever you choose.

### 3. Timezone assumption

`created_date` is naive and *inferred* to be Eastern local from a call-volume trough.
That is reasonable inferential evidence, not a documented guarantee. Every model
touching a date boundary depends on it. Make it a single documented constant, not an
assumption scattered across models, so it can be corrected in one place if wrong.

---

## HUMAN-ONLY tasks

### H2.1 — Approve the DuckDB↔Iceberg read strategy
C2.1 will report whether dbt-duckdb can read bronze directly. If it can't cleanly, the
fallback changes the project's shape slightly. Approve the choice before modeling starts.

### H2.2 — Approve the observation cutoff
The censoring cutoff is an analytical judgment, not an engineering one. Review the
proposal from C2.3 and approve.

---

## CLAUDE CODE tasks

### C2.1 — Verify the dbt→DuckDB→Iceberg read path (before writing any model)

Phase 0 established DuckDB needs the table root plus `unsafe_enable_version_guessing`.
Whether that survives inside dbt-duckdb's connection handling is untested.

1. Install dbt-core + dbt-duckdb. Pin from actual resolution; record in `docs/versions.md`.
2. Test reading bronze from dbt-duckdb: does the iceberg extension load, does the
   version-guessing setting persist across the connection, can a model select from it?
3. If direct reads fail or are unreliable, evaluate the fallback: a scheduled
   PyIceberg → Parquet export that dbt reads as an external source. Report the
   tradeoff — the fallback is simpler and faster but adds a materialization step and
   loses the "dbt reads Iceberg directly" claim.
4. **Measure read performance either way** on a full-table scan and on a partition-
   pruned scan. If direct Iceberg reads are dramatically slower than Parquet, that is
   itself a finding worth recording.

**Report and stop for H2.1 approval.** Do not begin modeling on an unverified read path.

### C2.2 — dbt project scaffold
Standard layout under `dbt/`: `models/staging/`, `models/intermediate/`, `models/marts/`,
plus `macros/`, `tests/`, `seeds/`. Profile targets local DuckDB.

Define the timezone constant and the observation cutoff as **project variables** in
`dbt_project.yml`, not hardcoded in models. Both are assumptions that may need revising.

### C2.3 — Propose the observation cutoff
Using the C1.1 lag distribution (2.53 d floor, 6.71 d median, 92 d tail), propose a
cutoff and justify it. State plainly what the 92-day tail means: no cutoff makes
recent data fully settled, so the choice trades completeness against recency.

Report for H2.2 approval.

### C2.4 — Staging layer (`stg_service_requests`)
One staging model over bronze. Cast types, normalize casing on categoricals, parse
timestamps under the documented timezone rule, and rename to consistent snake_case.

Resolve three ambiguities from the schema:
- **`latitude`/`longitude` vs `location_lon`/`location_lat`** — verify they agree,
  pick one as canonical, document why, and record any disagreement rate.
- **Coordinate validity** — a boolean flag rather than dropping rows. The
  out-of-bounds defect is 0% but missing coords reach 2.5%, so the useful distinction
  is present-vs-absent.
- **`resolution_action_updated_date`** — decide whether it belongs in staging or is
  bronze-only.

No business logic here. Staging is shape, not meaning.

### C2.5 — Intermediate layer
Where the hard thinking goes:
- `int_request_resolution` — computes `resolution_hours`, `is_closed`,
  `is_censored`, and `is_settled` (created before the observation cutoff). A row that
  is open, or closed but unsettled, must be distinguishable from a row that is
  genuinely resolved.
- `int_request_geography` — normalizes borough, community district, ZIP; resolves the
  `borough` vs `park_borough` distinction; flags geocoding completeness.

**Do not compute a resolution time for censored rows.** Null is correct. A zero or an
imputed value would silently corrupt every downstream aggregate.

### C2.6 — Dimensions
Type 1, keyed on a surrogate. Each must absorb new members without breaking, since
vocabularies demonstrably drift.

- `dim_agency` — grain: one row per agency. 16 members currently.
- `dim_complaint_type` — grain: one row per (complaint_type, descriptor). Note the
  hierarchy; note that descriptor is frequently null.
- `dim_location` — grain: one row per distinct location key. Decide the key
  deliberately: borough + community district is stable, ZIP is dirtier, lat/long is
  too granular for a dimension.
- `dim_date` — grain: one row per calendar day, spanning the backfill window plus
  headroom. Include month, quarter, year, day-of-week, and a season attribute, since
  the interesting analysis is compositional.

### C2.7 — Fact table (`fct_service_requests`)
Grain: **one row per `unique_key`.** State the grain in the model description; it is
the single most important piece of documentation in the project.

Measures: `resolution_hours` (null when censored), `is_closed`, `is_censored`,
`is_settled`, `has_valid_coordinates`, plus foreign keys to all four dimensions.

Do not pre-aggregate. The semantic layer in Phase 4 does that.

### C2.8 — Answer the questions
Prove the model works by answering, in a scratch analysis (not a mart):

1. **Agency SLA:** median and p90 resolution hours by agency, **for settled requests
   only**, with closure rate and censored count alongside.
2. **Geographic equity:** for the top 5 complaint types, does resolution time differ
   materially by borough? Note the ~2% missing-coordinate exclusion.
3. **Compositional seasonality:** share of complaints by type, by month. Volume is
   flat, so this is where the signal lives — heat/hot-water share is the expected
   winter mover.
4. **The bulk-closure hypothesis:** for 2024 rows with status Closed and null
   `closed_date` (0.85%), do they cluster on a common `:updated_at` or
   `resolution_description`? If yes, that is bulk administrative closure — a real
   finding for Phase 3. Report either way.
5. **The geocoding-lag hypothesis:** break `missing_coords` down by month within 2026.
   Rising toward recent months means geocoding lag, not quality degradation. Flat
   means a genuine shift. These have opposite implications; do not conflate them.

Record all five in `docs/findings.md` (new file). These become the dashboard's content.

### C2.9 — Tests
Enough to be meaningful, not exhaustive. Minimum:
- `unique` and `not_null` on `unique_key` in the fact table — uniqueness is
  guaranteed by bronze's upsert, so a failure here means something is badly wrong.
- `relationships` from fact to each dimension.
- `not_null` on all dimension surrogate keys.
- A test asserting `resolution_hours` is null wherever `is_censored` is true.
- A test asserting `closed_date >= created_date` where both are present, tolerating
  the known ~0.02% defect rate — this test **should** fail at a small rate. Configure
  the threshold to match the measured rate and document why it isn't zero.
- Row-count reconciliation between staging and bronze.

Model contracts on the marts only. Full contract coverage everywhere is Phase 3.

### C2.10 — Journal, metrics, commit
`docs/decisions.md`: the read-path decision, the cutoff justification, the
coordinate-source choice, the location dimension key, and anything that surprised you.
`docs/metrics.md`: row counts per layer, test counts and results, build duration.

One commit: `Phase 2: dbt staging, intermediate, and star schema marts`.

---

## STOP GATE 2

| # | Criterion | Evidence required |
|---|---|---|
| 1 | Read path verified and approved | C2.1 findings; H2.1 approval; performance measured |
| 2 | Observation cutoff approved | C2.3 proposal; H2.2 approval; recorded as a project var |
| 3 | Staging reconciles to bronze | Row counts match, or the delta is explained |
| 4 | Coordinate source resolved | Which chosen, disagreement rate between the two |
| 5 | Censoring handled correctly | `resolution_hours` null for every censored row, test passing |
| 6 | Star schema built | Fact + 4 dims, grain stated in each description |
| 7 | Fact grain verified | `unique` test on `unique_key` passing |
| 8 | Referential integrity | All `relationships` tests passing, zero orphans |
| 9 | Tests pass at expected rates | Count by category; the closed≥created test failing at ~0.02% as designed |
| 10 | All five questions answered | `docs/findings.md` with real numbers |
| 11 | Bulk-closure hypothesis resolved | Confirmed or refuted with evidence |
| 12 | Geocoding-lag hypothesis resolved | Monthly 2026 breakdown |
| 13 | Build duration and row counts recorded | `docs/metrics.md` |
| 14 | One atomic commit | Message names Phase 2 |

**Load-bearing: 1, 5, 7.** A broken read path, mishandled censoring, or a violated
grain each invalidate everything downstream.

Criterion 9 is unusual: a test that passes at 100% would mean the known defect
vanished, which would itself need explaining.

---

## What Phase 3 will do (context only — do not start)

Data quality as a first-class layer: model contracts across all models, dbt unit tests
on the resolution and censoring logic, Soda Core distributional checks, and the DQ
scorecard mart built around what C1.9 and C2.8 actually found.
