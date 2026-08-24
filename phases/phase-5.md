# Phase 5 — Evidence.dev Dashboard, Deployed Public

**Week:** 3 (back)
**Estimated effort:** 8–12 hours
**Goal:** A free, always-on public URL a stranger can open and understand in ninety
seconds — surfacing the agency-SLA, geographic-equity, seasonality, and data-quality
findings, with the naive-vs-correct gap made visible.

## What's different about this phase

Phases 1–4 were correctness work with binary gates. This one is presentation, and the
gate is softer but real: **can someone who has never seen this project look at it and
immediately grasp what it shows and why it's trustworthy?** A technically perfect
dashboard that reads as noise fails. A clear one that surfaces the three real findings
succeeds.

The "live demo or it didn't happen" rule is the hard constraint. The deliverable is a
URL, not a screenshot. It must be public, free, always-on, and it must still be up the
week a recruiter looks at it — which is precisely why Evidence.dev → Vercel was chosen
over a hosted Metabase that costs money to keep alive.

## The architecture decision to confirm first

Evidence runs queries **at build time** and ships a static site. That means the
deployed dashboard queries a DuckDB file baked into the build, not a live warehouse.
The public site cannot re-query on demand — it shows the data as of the last build.

This is correct for a portfolio (free, always-on, can't fall over), but it has a
consequence C5.1 must resolve: **how does the DuckDB data get into the build?** The
marts live in a local DuckDB file that isn't in git (it's 596MB). Options exist; pick
one deliberately.

---

## Carried forward

| Fact | Consequence for the dashboard |
|---|---|
| 9 working metrics in MetricFlow | The dashboard's numbers should trace to these |
| `complaint_type_share` is client-side | Seasonality panel computes share in-query, not via MetricFlow |
| naive-vs-correct pairs exist | Two "trap" panels with real gaps to show |
| DHS closure 80.97% vs 98.60% | The single most legible correctness story |
| 5 operational detectors + scorecard | The data-quality panel's content |
| Settlement ~93% at 45 days | Recent periods must be annotated provisional |
| Volume flat, composition varies | Lead seasonality with share, not volume |
| Marts in local DuckDB, 596MB, not in git | Build-time data delivery must be solved (C5.1) |
| Vercel deploy experience | Known ground; deploy hook pattern available |

---

## HUMAN-ONLY tasks

### H5.1 — Vercel account and deploy
Deploying to Vercel requires an account and the connect-repo step. Claude Code prepares
everything; you do the account-level actions and confirm the public URL loads for an
anonymous visitor (test in a private window — a URL that only loads while you're logged
in is not a public demo).

### H5.2 — The ninety-second test
Before the phase closes, open the live URL as if you'd never seen it. Can you tell,
without scrolling far or reading closely, what this project is and what it found? If
not, say what's unclear — that feedback is the real acceptance criterion, not a
checklist.

---

## CLAUDE CODE tasks

### C5.1 — Resolve build-time data delivery (before building any page)
The static build needs data. Determine and implement the approach:

1. **A pruned DuckDB file committed to the repo or fetched at build time.** The full
   596MB is too large for git. But the dashboard needs only aggregated results, not
   7.5M raw rows — so build a small DuckDB (or set of Parquet files) containing just
   the mart-level aggregates and scorecard the dashboard reads. Report its size.
2. Evidence connects to that pruned source. Confirm the connection works locally before
   any page is written.
3. Confirm the pruned data reconciles to the full marts — the dashboard must not show
   numbers that disagree with the warehouse. Spot-check the headline metrics.

Report the approach and size. **This gates everything; a dashboard with no data path is
nothing.**

### C5.2 — Evidence project scaffold
Install Evidence, scaffold under `dashboard/`. Pin versions in `docs/versions.md`.
Confirm `npm run dev` renders locally against the pruned source.

### C5.3 — The pages

Structure for a stranger, not for you. Lead with the finding, support with the chart.

**Page 1 — Overview / "what is this."** One screen that answers: what dataset, what
question, what was found. A short prose framing (this is a governed Iceberg lakehouse
over NYC 311, asking how equitably and quickly the city resolves service requests), the
top-line numbers (total requests, agencies, date range, overall closure and resolution),
and a one-line pointer to the three findings below. This page is what the ninety-second
test judges.

**Page 2 — Agency performance.** Median and p90 resolution hours by agency, **for
settled closed requests, backlog excluded**, with closure rate beside every latency so
a fast-looking agency with low closure reads as incomplete, not fast. This is where the
DHS naive-vs-correct closure-rate gap gets its own callout: 80.97% vs 98.60%, with the
one-sentence explanation (a bounded, non-growing administrative backlog).

**Page 3 — Geographic equity.** For the top complaint types, resolution time by borough.
Note the ~2% missing-coordinate exclusion honestly. The question the page answers out
loud: does the same complaint get resolved faster in some boroughs than others?

**Page 4 — Seasonality by composition.** Complaint-type share by month (client-side per
C4/Phase 4), showing heat/hot-water's winter rise against flat total volume. The framing
line: volume barely moves, but what New Yorkers complain about shifts hard by season.

**Page 5 — Data quality.** The scorecard and detectors made legible: the settlement
completeness curve, the vocabulary-drift notifications, the mass-touch events, and the
naive-vs-correct settlement gap. The DHS persistent-condition row shown as acknowledged,
not failed. This page is the differentiator — most analytics dashboards have no
data-quality view at all.

### C5.4 — The naive-vs-correct panels
On the relevant pages, show the wrong number next to the right one, labeled clearly, with
the gap stated. This is the semantic layer's value made visual. Do not bury it — it's the
thing that shows judgment rather than just plumbing.

### C5.5 — Honesty annotations
Every metric that's provisional says so. Recent-period resolution metrics carry a
"provisional, N% settled" note. The missing-coordinate exclusion is stated where it
applies. These annotations are a feature — they signal the author knows the data's
limits — not a disclaimer to minimize.

### C5.6 — Reconciliation before deploy
The dashboard's headline numbers must match the marts. Pick the top figure on each page
and confirm it against a hand-written query on the full warehouse. A dashboard that
disagrees with its own source is worse than no dashboard. Record the checks.

### C5.7 — Build and deploy
Build the static site. Prepare the Vercel deploy (config, build command, the repo
connection Claude Code can't click). Hand off to H5.1 for the account steps.

After deploy: confirm the public URL loads anonymously, all pages render, no broken
charts, no console errors that break content.

### C5.8 — The refresh story
Document how the dashboard updates: the pruned-data rebuild plus a Vercel redeploy,
ideally wired to a trigger (a deploy hook Phase 6's cron can fire). Full automation is
Phase 6; here, document and manually prove the path works once.

### C5.9 — Journal, metrics, commit
`docs/decisions.md`: the data-delivery approach, why static-build fits a portfolio, any
Evidence limitations hit.
`docs/metrics.md`: pruned data size, build time, page count, reconciliation results.
README stub gets the live URL (full README is Phase 7).

One commit: `Phase 5: Evidence.dev dashboard deployed to Vercel`.

---

## STOP GATE 5

| # | Criterion | Evidence required |
|---|---|---|
| 1 | Build-time data path works | Approach, pruned size, connection confirmed |
| 2 | Pruned data reconciles to marts | Headline figures spot-checked against full warehouse |
| 3 | All five pages render locally | `npm run dev` clean |
| 4 | Naive-vs-correct shown | The gap visible and labeled on the relevant pages |
| 5 | Provisional metrics annotated | Recent periods marked, exclusions stated |
| 6 | Headline numbers reconcile | Per-page top figure matches a hand-written query |
| 7 | **Deployed and public** | Anonymous load confirmed in a private window |
| 8 | All pages render live | No broken charts or content-breaking console errors |
| 9 | Ninety-second test passed | H5.2 feedback addressed |
| 10 | Refresh path documented and proven once | The rebuild-plus-redeploy works manually |
| 11 | Journal, metrics updated; URL in README stub | Real numbers; clickable link |
| 12 | One atomic commit | Message names Phase 5 |

**Load-bearing: 1, 6, 7.** No data path is no dashboard. Numbers that don't reconcile
make it worse than nothing. Not-public fails the entire premise.

Criterion 9 is subjective and that's intentional — the human judgment of whether a
stranger understands it is the real test, not a box to tick.

---

## What Phase 6 will do (context only — do not start)

Orchestration: GitHub Actions cron running the incremental ingest, dbt build, quality
suite, and dashboard redeploy on schedule — with the scheduling constraints already
found (publish-cycle timing, the Soda/dbt lock, partial-parse clearing, DBT_TARGET) all
accounted for. Source freshness and the staleness signal wired in.
