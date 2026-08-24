---
title: Overview
---

```sql overview
select * from openledger.overview
```

# OpenLedger

A governed **Apache Iceberg lakehouse** over NYC's 311 Service Request data — built to
answer one question: **how equitably and quickly does the city resolve service
requests?** Incremental ingestion, a dimensional model, layered data-quality checks,
and a semantic layer with correctness rules baked in sit behind every number on this
site — not just this dashboard.

<BigValue
    data={overview}
    value=request_count
    title="Total requests"
    fmt="#,##0"
/>

<BigValue
    data={overview}
    value=agency_count
    title="Agencies"
/>

<BigValue
    data={overview}
    value=closure_rate_pct
    title="Overall closure rate"
    fmt="#,##0.0\%"
/>

<BigValue
    data={overview}
    value=median_resolution_hours
    title="Median resolution, hrs (settled, closed, backlog excl.)"
    fmt="#,##0"
/>

Data spans **{overview[0].date_min}** through **{overview[0].date_max}** — a full
24-month backfill plus ongoing incremental updates, watermarked and MERGE'd into
Iceberg, never a full re-pull.

## Three things this project found

1. **[Agency performance](/agency-performance)** — DHS's raw closure rate reads as
   80.97%, meaningfully below every other large agency. The real number, once a
   bounded 17,356-row historical backlog with no computable resolution date is
   excluded rather than silently counted against it, is 98.60%. Same underlying work,
   a ~18-point swing in how it's reported.
2. **[Seasonality by composition](/seasonality)** — total request volume barely moves
   month to month. What New Yorkers complain about shifts hard by season: heat/hot
   water complaints alone swing from ~1% to over 20% of monthly volume every winter.
3. **[Data quality](/data-quality)** — this project ships a full data-quality scorecard
   and five operational anomaly detectors most analytics dashboards never show at all,
   including a naive-vs-correct comparison that makes the cost of skipping this layer
   visible, not just claimed.

<Alert status="info">
Every resolution-time figure on this site is computed only for <b>settled</b> (old
enough that the current open/closed status can be trusted) and <b>closed</b> requests,
with a documented administrative backlog excluded — see
<a href="/data-quality">Data quality</a> for what that means and why.
</Alert>
