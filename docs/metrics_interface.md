# OpenLedger metrics — consumer interface

C4.6. What each metric means, what it enforces, and what it supports —
readable without opening `dbt/models/marts/_semantic_models.yml` or
`_metrics.yml`. Approved at H4.2 (2026-08-23); full derivation, every
filter's reasoning, and the reconciliation proof are in
`docs/decisions.md`.

**Querying**: `DBT_TARGET=prod DBT_PROFILES_DIR=. mf query --metrics
<name>[,<name>...] --group-by <dimension>[,...]`, run from `dbt/`. This
is the one sanctioned invocation (`docs/versions.md`) — Phase 5 and
Phase 6 must reuse it unchanged.

**Every metric below supports the same group-by set**: `agency`,
`agency_name` (via `agency_key__`), `complaint_type`, `descriptor` (via
`complaint_type_key__`), `community_board`, `borough` (via
`location_key__`), `calendar_year`, `calendar_quarter`, `month_name`,
`day_name`, `is_weekend`, `season` (via `date_key__`), and time
granularities of `created_date` itself (`metric_time__day`,
`__month`, `__year`, etc.) — every semantic model joins to the fact
through its own entity key, so any metric can be sliced by any of these
without special-casing. Run `mf list metrics` for the exact, current
dimension list per metric.

---

## Resolution-time metrics

### `median_resolution_hours` / `p90_resolution_hours`
Hours from `created_date` to `closed_date` — median and 90th percentile.

**Enforces**: `is_settled AND is_closed AND NOT is_undated_closure`.
Structurally impossible to include a censored, unsettled, or DHS-backlog
row at any grain — the filter is fused inside the aggregate's own input
expression, not a separate clause MetricFlow could reposition (proven by
reading generated SQL across 3 different group-by shapes; `docs/decisions.md`, C4.4).

**Measured**: 8.00h median, 401.00h p90 (dataset-wide, 2026-08-23).

### `naive_median_resolution_hours` — WRONG ON PURPOSE
Same duration, computed directly from raw `created_date`/`closed_date`,
with none of the above filters.

**Exposes**: the settlement/censoring trap specifically — closed-but-
not-yet-settled rows survivorship-bias the number down. **Does not**
expose the DHS backlog trap (undated-closure rows self-exclude from any
resolution-hours computation via null arithmetic, filtered or not — see
`naive_closure_rate` for that trap instead).

**Measured gap**: 7.00h naive vs. 8.00h correct — a real ~12.5% relative
understatement, dataset-wide.

**Never use for a real number.** Exists only to display next to
`median_resolution_hours` to make the gap visible.

---

## Coverage metrics

### `closure_rate`
`closed / total`, at whatever grain is queried — closed as a share of
**all** requests (open or closed, settled or not).

**Measured**: 96.43% dataset-wide.

### `settlement_rate`
`settled / total`. Low for a recent period **by design** — that's the
signal a resolution-time metric for the same period is provisional, not
a defect in this metric.

**Measured**: 94.10% dataset-wide.

### `censored_count`
Count where `is_censored` (genuinely open, or closed too recently to
trust as final). **Measured**: 627,228 dataset-wide.

---

## The backlog-trap pair (H4.2, replacing `complaint_type_share`'s slot in the original plan)

### `naive_closure_rate` — WRONG ON PURPOSE
`(closed AND settled) / settled` — closed as a share of the **settled**
population, with the DHS-style undated-closure backlog left in that
population, simply uncredited as closed.

### `closure_rate_excl_backlog` — THE CORRECTED PAIR
Same numerator, but the denominator **excludes** the undated-closure
backlog entirely — `(closed AND settled) / (settled AND NOT
undated-closure)`. This is the metric a DHS-specific SLA figure should
use.

**Measured, DHS specifically**: `naive_closure_rate` **80.97%** vs.
`closure_rate_excl_backlog` **98.60%** — a real +17.63pp gap (matches
C3.7's independently-published 80.97%/98.61% finding almost exactly; the
0.01pp difference is rounding). Dataset-wide the gap is much smaller
(97.42% vs. 97.66%) because only DHS (and a handful of DSNY rows) carry
this pattern — the gap is agency-specific, not a general data quality
issue.

**A verification note, not a caveat to hide**: an earlier version of this
pair used `closed_count` (all closed rows, unconditional) as the
numerator and produced rates **above 100%** for several agencies — an
impossible value that would have shipped silently if not checked against
hand-written SQL before release. `closed_count` alone is not a subset of
the settled population (a row can be closed before it's settled); the
numerator had to be `closed AND settled` specifically. Full detail:
`docs/decisions.md`, C4.3/H4.2.

---

## Volume

### `request_count`
Total requests, no filter. Also Phase 5's building block for
**complaint-type share of volume**: this MetricFlow version has no
working "percent of parent total" metric type (checked directly against
the installed package — confirmed empirically too, not just reasoned
about: a `ratio` metric of `request_count` over itself, grouped by
complaint type, returns exactly `1.0` for every group). Removed rather
than shipped broken. **Phase 5 computes the share itself**: query
`request_count` grouped by `complaint_type`, query it again with no
group-by for the dataset total, divide in the dashboard/query layer.

---

## Internal plumbing (not analytical deliverables — exist only as ratio inputs)

`closed_count`, `closed_and_settled_count`, `settled_count`,
`settled_count_excl_backlog` back the ratio metrics above and have no
independent meaning worth consuming directly. Listed in `mf list
metrics` like any other metric (MetricFlow has no "private metric"
concept), but not part of this interface.
