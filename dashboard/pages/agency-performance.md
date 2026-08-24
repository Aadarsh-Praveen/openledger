---
title: Agency Performance
---

```sql agency_performance
select * from openledger.agency_performance
order by request_count desc
```

```sql dhs
select * from openledger.agency_performance where agency = 'DHS'
```

# Agency Performance

Median and 90th-percentile resolution hours by agency, computed only for **settled,
closed** requests with the undated-closure backlog excluded — the same filter is baked
into the underlying metric definition itself, not applied ad hoc per chart (see
[Data quality](/data-quality) for why that distinction matters). Closure rate sits
beside every latency figure on purpose: a fast-looking median next to a low closure
rate means "fast for the ones that finish," not "fast."

<DataTable data={agency_performance} rows=16>
    <Column id=agency title="Agency"/>
    <Column id=request_count title="Requests" fmt="#,##0"/>
    <Column id=median_resolution_hours title="Median hrs"/>
    <Column id=p90_resolution_hours title="P90 hrs"/>
    <Column id=closure_rate_pct title="Closure rate" fmt="#,##0.0\%"/>
    <Column id=settlement_rate_pct title="Settlement rate" fmt="#,##0.0\%"/>
</DataTable>

## The DHS naive-vs-correct closure rate — the sharpest correctness story here

DHS carries a **17,356-row historical backlog** (created 2024-08-19 through
2025-05-06, and — checked directly, not assumed — flat at exactly zero new rows since)
of requests marked closed with **no resolution date recorded**. Left in the
denominator uncredited as closed, DHS's closure rate reads as meaningfully worse than
every comparable agency. Excluded from the population being measured — the
administratively correct read, since these rows measure a historical data artifact,
not response performance — the real number is nearly on par with the rest of the
agencies shown above.

<BigValue
    data={dhs}
    value=naive_closure_rate_pct
    title="DHS closure rate — backlog left in (wrong)"
    fmt="#,##0.00\%"
/>

<BigValue
    data={dhs}
    value=closure_rate_excl_backlog_pct
    title="DHS closure rate — backlog excluded (correct)"
    fmt="#,##0.00\%"
/>

<Alert status="warning">
The gap above is <b>+17.6 percentage points</b> — real, measured, and reproduced two
independent ways (a hand-written SQL query in Phase 3, and this project's MetricFlow
semantic layer in Phase 4). It is <b>not</b> a resolution-time story: the same backlog
rows have no closed_date, so they silently drop out of any resolution-hours
computation on their own, filtered or not — this closure-rate pair is the only place
the backlog's effect is actually visible. Full derivation on the
<a href="/data-quality">Data quality</a> page.
</Alert>
