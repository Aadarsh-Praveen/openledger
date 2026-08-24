---
title: Seasonality
---

```sql seasonality
select * from openledger.seasonality
order by month, complaint_type
```

```sql heat_hot_water
select month, pct_share_of_month
from openledger.seasonality
where complaint_type = 'HEAT/HOT WATER'
order by month
```

```sql monthly_volume
select distinct month, month_total_requests
from openledger.seasonality
order by month
```

# Seasonality by Composition

**Total request volume barely moves month to month. What New Yorkers complain about
shifts hard by season.** Leading with composition, not raw volume, because volume is
close to flat across the full 24-month history — a chart of it would show almost
nothing. Share of monthly volume, by complaint type, shows a real, large, recurring
signal instead.

<LineChart
    data={seasonality}
    x=month
    y=pct_share_of_month
    series=complaint_type
    title="Share of monthly request volume, top 10 complaint types"
    yAxisTitle="% of month's requests"
/>

## The clearest single example: HEAT/HOT WATER

<LineChart
    data={heat_hot_water}
    x=month
    y=pct_share_of_month
    title="HEAT/HOT WATER share of monthly volume"
    yAxisTitle="% of month's requests"
/>

Heat/hot-water complaints swing from roughly 1% of monthly volume in summer to over
20% at the winter peak — a genuine, twice-recurring seasonal pattern (this project's
own composition-drift anomaly detector is specifically calibrated to recognize this
exact swing as expected, not flag it as an anomaly every year it happens).

<LineChart
    data={monthly_volume}
    x=month
    y=month_total_requests
    title="Total monthly request volume (for contrast — note how flat this is)"
/>

<Alert status="info">
This page excludes the dataset's first and last calendar months (Aug 2024 — the
backfill's own partial start — and the current in-progress month) from every chart.
Both are genuinely partial, not real dips; including them would visually manufacture
a "volume crashes at both ends" story that the underlying data doesn't support. Found
while reconciling this page's headline figure against the full warehouse (C5.6) — the
naive month-over-month range looked far less flat than it actually is until the two
partial months were identified and excluded.
</Alert>
