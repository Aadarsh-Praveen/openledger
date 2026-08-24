---
title: Data Quality
---

```sql scorecard
select * from openledger.dq_scorecard
order by category, check_name, grain
```

```sql settlement_completeness
select * from openledger.dq_settlement_completeness
order by cohort_month
```

```sql vocab_new_agencies
select * from openledger.dq_vocabulary_drift
where dimension = 'agency'
order by first_seen_month
```

```sql mass_touch
select * from openledger.dq_mass_touch
order by hour_bucket
```

```sql naive_vs_correct
select * from openledger.dq_resolution_hours_naive_vs_correct
```

# Data Quality

Most analytics dashboards have no data-quality view at all. This one does, because the
correctness work behind every other page on this site is itself a real, checkable
deliverable — not a claim.

## The naive-vs-correct settlement gap

Computing "median resolution hours" the naive way — straight from raw
created/closed dates, no filtering — silently includes requests that closed too
recently to be a trustworthy sample (a survivorship-bias effect: only the fast
closers from a young cohort have closed yet). The correct figure requires a request to
be **settled** (old enough that its current status can be trusted) as well as closed.

<BigValue
    data={naive_vs_correct}
    value=naive_median_resolution_hours
    title="Naive median resolution, hrs (wrong)"
    fmt="#,##0"
/>

<BigValue
    data={naive_vs_correct}
    value=correct_median_resolution_hours
    title="Correct median resolution, hrs"
    fmt="#,##0"
/>

<Alert status="warning">
A real, measured gap — not the same trap as the DHS closure-rate story on
<a href="/agency-performance">Agency Performance</a>. That backlog's rows have no
closed_date at all, so they drop out of any resolution-hours number automatically,
filtered or not. This gap is a genuinely different mechanism (unsettled cohorts, not a
missing-field artifact) — the two traps needed two different metric pairs to both be
demonstrable, and both are shown, on the pages where each one actually shows up.
</Alert>

## Settlement completeness

Percent of a cohort's eventual closures observable 45 days after creation, tracked on
a rolling basis. The 45-day cutoff used everywhere on this site as the definition of
"settled" was set here — at ~93%, comfortably past the 90% bar this project requires
before treating a period as measurable.

<DataTable data={settlement_completeness} rows=10>
    <Column id=cohort_month title="Cohort"/>
    <Column id=closed_rows title="Closed rows" fmt="#,##0"/>
    <Column id=completeness_pct_45d title="Completeness @ 45d" fmt="#,##0.00\%"/>
    <Column id=status title="Status"/>
</DataTable>

## Vocabulary drift — new agencies

A notification, not a pass/fail check: every agency code whose first appearance in the
data is recent enough to be worth a human glance, rather than silently absorbed as a
normal category.

<DataTable data={vocab_new_agencies} rows=10>
    <Column id=value title="Agency"/>
    <Column id=first_seen_month title="First seen"/>
    <Column id=volume_to_date title="Volume to date" fmt="#,##0"/>
</DataTable>

## Mass metadata-touch events

Nights where 13 or more agencies' `:updated_at` timestamps moved together in one hour,
well beyond the normal nightly platform-noise band — the kind of event that would
distort any freshness/staleness signal built naively on `:updated_at` if it weren't
characterized and screened out first.

<DataTable data={mass_touch} rows=10>
    <Column id=hour_bucket title="Night"/>
    <Column id=distinct_agencies title="Agencies touched"/>
    <Column id=total_rows title="Rows touched" fmt="#,##0"/>
</DataTable>

## The full scorecard

Every contract, unit test, distributional check, and anomaly detector this project
runs, in one table — including the checks that report zero, on purpose (a flat line at
zero next to metrics that move is itself informative). DHS's persistent
undated-closure condition shows as **acknowledged**, not failed: a known, dated,
bounded condition, not something silently red forever.

<DataTable data={scorecard} rows=25 search=true>
    <Column id=category title="Category"/>
    <Column id=check_name title="Check"/>
    <Column id=grain title="Grain"/>
    <Column id=measured_value title="Measured"/>
    <Column id=status title="Status"/>
</DataTable>
