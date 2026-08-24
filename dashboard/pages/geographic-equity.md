---
title: Geographic Equity
---

```sql geo_equity
select * from openledger.geo_equity
order by complaint_type, median_resolution_hours desc
```

```sql missing_coords
select * from openledger.geo_equity_missing_coords
```

# Geographic Equity

For the ten highest-volume complaint types, median resolution hours by borough
(settled, closed requests, backlog excluded — same filter as every other resolution
figure on this site). **Does the same complaint get resolved faster in some boroughs
than others?**

<DataTable data={geo_equity} rows=60 search=true>
    <Column id=complaint_type title="Complaint type"/>
    <Column id=borough title="Borough"/>
    <Column id=request_count title="Requests" fmt="#,##0"/>
    <Column id=median_resolution_hours title="Median hrs"/>
</DataTable>

<Alert status="info">
<b>{missing_coords[0].missing_coord_pct}%</b> of all requests have no usable
latitude/longitude and are excluded from this page entirely — not zero, not imputed,
just left out. That rate is stable across the dataset's 24-month history and does not
trend with time (see <a href="/data-quality">Data quality</a>); it is a property of
how certain request channels report location, not a data-quality regression.
</Alert>
