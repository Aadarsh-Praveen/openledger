# Findings

Analytical findings produced during Phase 2 modeling. Real numbers only —
see `docs/decisions.md` for the methodology and journal entries behind each.

## Observation cutoff: measured settlement/completeness curve

**Question:** what fraction of a creation cohort's eventual closures are
*observable* (i.e., `:updated_at` has already advanced to reflect the
closure) N days after creation? This is a different quantity from C1.1's
closed_date-to-`:updated_at` lag measurement — it combines "time to close"
and "time for the closure to become visible" into one end-to-end,
per-cohort number, measured directly rather than inferred from C1.1's
aggregate lag statistics.

**Method:** for a fully-settled cohort (old enough that essentially all its
eventual closures have both happened and been observed), `date_diff('day',
created_date, updated_at)` for every currently-closed row tells us, per row,
how many days after creation its closure became observable — no historical
API snapshots needed, since bronze's current `updated_at` already carries
this directly for any row whose *last* touch was the genuine closure event.

**A real methodological problem was found and worked around, not
papered over:** cohorts from 2024/2025 turned out to be contaminated by the
December 2025 dataset restructuring (documented in Phase 1's `docs/decisions.md`),
which bulk-touched `:updated_at` for effectively the entire pre-existing
dataset — for any row whose genuine closure predates that migration,
`updated_at` now reflects the *migration's* timestamp, not the original
observability moment, making `date_diff` measurements on those cohorts
meaningless (an initial run showed exactly this: 0.00% completeness at every
threshold up to 90 days, an implausible result that led directly to this
discovery). **Cohorts were instead selected from 2026 (Jan-Apr) — old enough
now (Aug 2026) to clear the 92-day tail, but created *after* the Dec 2025
migration, so this specific contamination doesn't apply.**

**A second, smaller contamination was found and excluded:** the January 2026
cohort showed 91,010 of 338,952 closed rows (26.9%) sharing one exact
`updated_at` timestamp, `2026-08-20`, distinct from the broader Aug 19-21
recurring republish pattern seen elsewhere in this project. This looks like
a one-off, targeted bulk re-touch of ~7-month-old cases (a plausible
administrative backlog sweep), not a data quality defect in the underlying
closures — but it inflates January's measured "days to observable" for
those rows, so **January is reported for completeness but excluded from the
cutoff conclusion below.** Worth a closer look as its own finding in Phase 3
(same shape as the bulk-closure hypothesis C2.8 investigates, but a distinct
event). Feb/Mar/Apr 2026 show only 0.10-0.24% of rows touched by any
recent-spike window — negligible, not excluded.

### Completeness curve (% of a cohort's eventual closures observable by day N)

| Cohort | Closed rows | N=7d | N=14d | N=30d | N=45d | N=60d | N=90d |
|---|---|---|---|---|---|---|---|
| 2026-01 (contaminated, reported not used) | 338,952 | 39.86% | 50.36% | 61.15% | 64.96% | 66.88% | 69.66% |
| **2026-02** | 321,336 | 45.32% | 67.03% | **85.45%** | 92.06% | 94.22% | 97.29% |
| **2026-03** | 329,049 | 52.06% | 70.44% | **85.23%** | 92.98% | 95.50% | 98.27% |
| **2026-04** | 289,393 | 55.78% | 71.45% | **87.25%** | 93.92% | 96.31% | 98.97% |
| **Clean average** | | 51.1% | 69.6% | **86.0%** | 92.99% | 95.34% | 98.18% |

**At 30 days: ~86% completeness — materially below the ~90% threshold.**
Per the pre-agreed decision rule, this calls for a revision, not accepting
30 days as-is.

**At 45 days: ~93% completeness — comfortably clears 90%,** with real
margin (not a marginal pass), across all three clean cohorts consistently
(92.06%, 92.98%, 93.92% — a tight, credible range, not noisy).

**Revised proposal: `observation_cutoff_days = 45`**, replacing the
originally-approved 30. Still a deliberate, disclosed trade against the
92-day tail (98% at 90 days, not 100% — some residual risk remains at any
finite cutoff), but now backed by direct measurement rather than inference
from a different quantity (C1.1's lag distribution), and it clears the
~90% bar the human review specified rather than landing below it.

**`is_settled` keys on `created_date`, confirmed correct.** Keying on
`closed_date` instead would exclude exactly the slow-resolving and
still-open requests the cutoff exists to account for: an open request has
no `closed_date` at all (so a closed_date-keyed filter could never mark it
settled regardless of true age), while a request that *just* closed would
get `closed_date ≈ now` and pass a recency check instantly — the least
trustworthy reading, treated as the most trustworthy. Keying on
`created_date` correctly treats "old enough since creation" as the trust
signal, independent of current status — this is what makes a long-open row
a legitimate, confident censoring case rather than a permanently-untrusted one.

## C2.8 — Analytical questions

All figures below are from the full prod build (7,533,132 rows,
`fct_service_requests`), queried directly against
`dbt/target/openledger_prod.duckdb`. "Settled" means `is_settled = true`
(created ≥45 days ago, per the cutoff above); resolution-hour statistics
are computed only over settled *and* closed rows (`resolution_hours` is
`null` for anything censored, by construction in `int_request_resolution`).

### 1. Agency SLA performance

Median and p90 `resolution_hours`, closure rate, and censored count by
agency, restricted to settled requests, for agencies with ≥1,000 settled
requests:

| Agency | Settled | Closed | Closure rate | Censored | Median hrs | p90 hrs |
|---|---:|---:|---:|---:|---:|---:|
| NYPD | 3,198,846 | 3,198,616 | 100.0% | 230 | 1.0 | 7.0 |
| HPD | 1,554,978 | 1,526,440 | 98.2% | 28,538 | 84.0 | 892.0 |
| DSNY | 645,047 | 638,994 | 99.1% | 6,053 | 33.0 | 173.0 |
| DOT | 449,344 | 429,729 | 95.6% | 19,615 | 47.0 | 406.0 |
| DEP | 390,064 | 384,281 | 98.5% | 5,783 | 23.0 | 239.0 |
| DPR | 220,245 | 178,360 | 81.0% | 41,885 | 184.0 | 3,894.0 |
| DOB | 201,360 | 190,082 | 94.4% | 11,278 | 224.0 | 3,920.0 |
| DOHMH | 157,654 | 149,206 | 94.6% | 8,448 | 50.0 | 1,441.0 |
| DHS | 96,911 | 78,482 | 81.0% | 18,429 | 8.0 | 261.0 |
| TLC | 71,134 | 50,120 | 70.5% | 21,014 | 997.0 | 4,755.0 |
| EDC | 37,164 | 18,448 | 49.6% | 18,716 | 4,506.0 | 8,634.0 |
| DCWP | 35,743 | 35,648 | 99.7% | 95 | 122.0 | 786.0 |

**Reading:** NYPD and DCWP close at essentially 100%, and NYPD resolves in
~1 hour at the median — consistent with dispatch-style enforcement rather
than a remediation workflow. HPD (heat/hot-water, housing conditions) has
volume (1.55M settled) and a much heavier tail: 84h median but an 892h
(~37-day) p90, i.e. a long-tail of genuinely hard-to-fix housing
violations behind a reasonable typical case. The two outliers are EDC
(49.6% closure rate, 4,506h/~188-day median for the ones that do close) and
TLC (70.5% closure, 997h/~42-day median) — both look less like slow SLAs
and more like a large share of requests routed to those agencies never
getting formally closed in this system at all; worth a Phase 3 data-quality
check on whether EDC/TLC statuses terminate in a different way (e.g.
`Closed` in a downstream system that never syncs back) rather than treating
this as a literal 50%-of-requests-abandoned finding.

### 2. Geographic equity for top-5 complaint types

By volume: ILLEGAL PARKING (1,158,330), NOISE - RESIDENTIAL (882,532),
HEAT/HOT WATER (651,467), BLOCKED DRIVEWAY (363,167), NOISE -
STREET/SIDEWALK (351,849). Median/p90 `resolution_hours` by borough,
settled requests only (excludes the 0.08% of rows with an unspecified
borough and notes, but does not need to exclude, the 1.72% with missing
lat/long — borough is a separate staged field, not derived from
coordinates):

| Complaint type | Borough | N settled | Median hrs | p90 hrs |
|---|---|---:|---:|---:|
| HEAT/HOT WATER | Bronx | 226,966 | 36.0 | 71.0 |
| HEAT/HOT WATER | Brooklyn | 172,699 | 46.0 | 96.0 |
| HEAT/HOT WATER | Manhattan | 154,793 | 44.0 | 89.0 |
| HEAT/HOT WATER | Queens | 84,759 | 45.0 | 95.0 |
| HEAT/HOT WATER | Staten Island | 6,866 | 37.0 | 77.0 |
| NOISE - RESIDENTIAL | Bronx | 353,826 | 2.0 | 12.0 |
| NOISE - RESIDENTIAL | Brooklyn | 198,819 | 1.0 | 5.0 |
| NOISE - RESIDENTIAL | Manhattan | 125,409 | 1.0 | 4.0 |
| NOISE - RESIDENTIAL | Queens | 142,863 | 1.0 | 6.0 |
| NOISE - RESIDENTIAL | Staten Island | 19,741 | 1.0 | 4.0 |
| ILLEGAL PARKING / BLOCKED DRIVEWAY / NOISE - STREET/SIDEWALK | (all boroughs) | — | 1–2 | 3–10 |

(Full figures for the three fast-turnaround complaint types are in the
query history; they cluster tightly at 1–2h median / 3–10h p90 everywhere
and show no material cross-borough spread, so they're summarized rather
than tabulated in full.)

**Reading — two real, differently-shaped equity findings:**
- **HEAT/HOT WATER**: the Bronx and Staten Island resolve materially faster
  (36–37h median) than Brooklyn/Manhattan/Queens (44–46h) — roughly a
  20–28% gap at the median that holds at p90 too (71–77h vs 89–96h). This
  is the opposite direction from a naive "under-resourced boroughs get
  worse service" prior, and worth flagging rather than smoothing over.
- **NOISE - RESIDENTIAL**: medians are close across boroughs (1–2h), but
  the Bronx's p90 (12h) is 2–3x every other borough's (4–6h) — a
  same-typical-case, worse-tail pattern, which a median-only SLA dashboard
  would completely hide.
- Fast-turnaround complaint types (parking/driveway/street noise) show no
  material geographic spread — the equity signal is concentrated in the
  complaint types with a real remediation workflow (heat, residential
  noise investigations), not in dispatch-style enforcement categories.

### 3. Compositional seasonality

Monthly share of total volume, HEAT/HOT WATER vs. a flat-volume control
(ILLEGAL PARKING):

| Month | HEAT/HOT WATER share | ILLEGAL PARKING share |
|---|---:|---:|
| Jan | 21.12% | 13.18% |
| Feb | 16.90% | 15.26% |
| Mar | 8.99% | 17.29% |
| Apr | 6.33% | 17.19% |
| May | 2.76% | 17.21% |
| Jun | 1.34% | 16.33% |
| Jul | 1.11% | 14.58% |
| Aug | 1.01% | 15.33% |
| Sep | 1.14% | 16.43% |
| Oct | 8.01% | 14.61% |
| Nov | 14.59% | 14.56% |
| Dec | 19.63% | 13.02% |

**Reading:** confirms the expected winter mover directly — HEAT/HOT WATER
swings from ~1% of monthly volume in summer to ~19–21% in Dec/Jan, a
~20x compositional range driven entirely by seasonality, not by any
underlying change in total complaint volume (already established flat in
Phase 1, C1.9). ILLEGAL PARKING, used here as a control, stays in a narrow
13–17% band all year — confirming the swing is HEAT/HOT WATER-specific
and not an artifact of total-volume seasonality that would move every
complaint type's share in lockstep.

### 4. Bulk-closure hypothesis (2024 `Closed` + null `closed_date`)

Phase 1 flagged that 0.85% of 2024 rows are `status = 'Closed'` with a
`null closed_date` (confirmed again here: 11,372 of 1,340,307 2024 rows,
0.85%). Do they cluster?

- **`:updated_at`**: yes, completely — **all 11,372 rows share the exact
  same `:updated_at` timestamp date, 2025-12-26.** This is a single bulk
  administrative event, not scattered data-entry gaps.
- **Agency**: 11,366 of 11,372 (99.9%) belong to **DHS**; the remaining 6
  are DSNY.
- **`resolution_description`**: dominated by two DHS mobile-outreach
  templates — *"The mobile outreach response team went to the location
  provided but could not find the individual that you reported"* (8,188
  rows) and *"...offered services to the individual, but the individual
  did not accept assistance"* (2,543 rows) — together 94% of the cluster.

**Reading:** this is not a data-quality defect to fix so much as a
documented one-off: DHS appears to have closed out a large backlog of
homeless-outreach requests in a single administrative sweep on
2025-12-26, using template resolution text and without backfilling a
`closed_date` to match the true closure time. It inflates DHS's apparent
`is_censored` count for that cohort (an outreach attempt that "closes" via
this sweep looks open/censored under this model until the sweep date) but
is a genuine, explainable artifact of DHS's workflow, not corruption.

### 5. Geocoding-lag hypothesis (missing coordinates by month)

Overall missing-coordinate rate is 1.72% across the whole fact table.
Breaking down by creation month from late 2025 onward:

| Created month | Total | Missing coords | % missing |
|---|---:|---:|---:|
| 2025-10 | 336,612 | 3,825 | 1.14% |
| 2025-11 | 304,905 | 2,860 | 0.94% |
| 2025-12 | 332,103 | 3,215 | 0.97% |
| 2026-01 | 348,511 | 4,421 | 1.27% |
| 2026-02 | 334,690 | 5,934 | 1.77% |
| **2026-03** | 342,387 | 18,114 | **5.29%** |
| 2026-04 | 302,190 | 9,871 | 3.27% |
| 2026-05 | 331,978 | 7,794 | 2.35% |
| 2026-06 | 334,833 | 6,906 | 2.06% |
| 2026-07 | 342,935 | 6,764 | 1.97% |
| 2026-08 | 200,262 | 3,988 | 1.99% |

**Not a simple monotonic rise** — it spikes sharply at March 2026 (5.29%,
a 3x jump from February) and then decays back toward ~2% by mid-summer.
That shape rules out "recent months haven't been geocoded yet, older
months are all caught up" (a genuine lag artifact would put the *current*
month highest, not a month 5 months in the past). Investigated further:

- The spike is **not a single-day event** — daily missing-coordinate rates
  through March 2026 sit in a consistent 5.4–7.7% band all month, not
  concentrated on a few days.
- It **is concentrated in one complaint type**: STREET CONDITION accounts
  for 13,339 of March 2026's 18,114 missing-coordinate rows (74%).
- STREET CONDITION's own volume spikes ~5–6x in March 2026 (28,690 rows
  vs. ~5,000/month baseline in Oct–Dec 2025) — consistent with NYC's
  well-known spring pothole surge (freeze-thaw cycle damage reported after
  winter) — and STREET CONDITION *chronically* runs a much higher
  missing-coordinate rate than the dataset overall (21–46% across every
  month checked, vs. 1.72% overall), because these complaints are commonly
  reported by street segment/intersection rather than a geocodable point.

**Reading:** this is a real, explainable finding but not "geocoding lag" in
the sense the hypothesis proposed. It's a **volume-driven composition
effect**: a seasonal spring surge in a complaint type (STREET CONDITION)
that has an intrinsically high missing-coordinate rate temporarily pulls
up the dataset-wide missing-coordinate percentage, and the rate falls back
as STREET CONDITION's share of volume normalizes through the summer. The
underlying per-complaint-type geocoding behavior looks stable; what
changes month to month is the mix.
