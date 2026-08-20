"""
Phase 1 / C1.1b — Distinguish "row-modification timestamp" from "publication batch
timestamp" for :updated_at, per H1.1's approval-with-conditions.

C1.1 found :updated_at clusters into a small number of distinct values relative to
sample size (91 distinct values / 5,000 rows, one value covering 49%), which could
mean either (a) :updated_at tracks genuine per-row changes that happen to cluster in
time, or (b) it advances on every periodic republish regardless of whether a row's
content changed. This script tells them apart using long-settled 2021 rows that have
no plausible reason to have changed recently, then estimates expected
rows-per-incremental-run for the C1.4 delta-only gate criterion, then verifies
resolution_action_updated_date as a $where/$order-capable cross-check field.

Throwaway diagnostic. Run: .venv/bin/python scripts/probe_watermark_republish.py
"""

import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN")
if not APP_TOKEN:
    raise SystemExit("SOCRATA_APP_TOKEN not set — check .env")
HEADERS = {"X-App-Token": APP_TOKEN}


def get(params):
    return requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)


def hr(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=None)


report = {}

# ---------------------------------------------------------------------------
# Steps 1-2 — Long-settled 2021 rows: created AND closed in 2021.
# ---------------------------------------------------------------------------
hr("Step 1-2 — Long-settled 2021 rows (created 2021, closed 2021)")
where_2021 = (
    "created_date between '2021-01-01T00:00:00.000' and '2021-12-31T23:59:59.999' "
    "AND closed_date between '2021-01-01T00:00:00.000' and '2021-12-31T23:59:59.999'"
)
resp = get({"$select": "count(*)", "$where": where_2021})
total_2021_settled = int(resp.json()[0]["count"]) if resp.status_code == 200 else None
print(f"HTTP status: {resp.status_code}")
print(f"Total rows created AND closed in 2021: {total_2021_settled}")

resp = get({
    "$where": where_2021,
    "$select": "unique_key,created_date,closed_date,:updated_at,status",
    "$order": "created_date,unique_key",
    "$limit": 50000,
})
rows = resp.json() if resp.status_code == 200 else []
print(f"Sample pulled: {len(rows)} rows (page-capped at 50,000 if total exceeds that)")

updated_at_years = Counter()
updated_at_year_months = Counter()
for r in rows:
    ua = r.get(":updated_at")
    if not ua:
        continue
    dt = parse_ts(ua)
    updated_at_years[dt.year] += 1
    updated_at_year_months[f"{dt.year}-{dt.month:02d}"] += 1

print(f"\n:updated_at year distribution for {len(rows)} long-settled 2021 rows:")
for year, count in sorted(updated_at_years.items()):
    pct = count / len(rows) * 100 if rows else 0
    print(f"  {year}: {count} ({pct:.2f}%)")

print("\nTop 10 :updated_at year-month buckets:")
for ym, count in updated_at_year_months.most_common(10):
    pct = count / len(rows) * 100 if rows else 0
    print(f"  {ym}: {count} ({pct:.2f}%)")

report["settled_2021_total"] = total_2021_settled
report["settled_2021_sample"] = len(rows)
report["settled_2021_updated_at_years"] = dict(updated_at_years)

# ---------------------------------------------------------------------------
# Step 3-4 — Decision: is :updated_at mostly 2021-era (row-level) or mostly
# recent (batch-republish, regardless of change)?
# ---------------------------------------------------------------------------
hr("Step 3-4 — Decision")
now_year = datetime.now(timezone.utc).year
recent_years = {now_year, now_year - 1}  # "recent" = current year or last year
recent_count = sum(c for y, c in updated_at_years.items() if y in recent_years)
recent_pct = (recent_count / len(rows) * 100) if rows else 0
era_2021_count = updated_at_years.get(2021, 0)
era_2021_pct = (era_2021_count / len(rows) * 100) if rows else 0

print(f"Share of :updated_at values in 2021 (the settlement year): {era_2021_pct:.2f}%")
print(f"Share of :updated_at values in {recent_years} (recent): {recent_pct:.2f}%")

if era_2021_pct > 50:
    verdict = "ROW-LEVEL: :updated_at is mostly 2021-era for long-settled rows — tracks genuine change."
elif recent_pct > 50:
    verdict = "BATCH-REPUBLISH: :updated_at is mostly recent for long-settled rows — advances regardless of content change."
else:
    verdict = "MIXED/AMBIGUOUS: neither 2021 nor recent dominates — needs manual review."

print(f"\nVERDICT: {verdict}")
report["verdict"] = verdict
report["era_2021_pct"] = era_2021_pct
report["recent_pct"] = recent_pct

# ---------------------------------------------------------------------------
# Step 5 — Estimate expected rows-per-incremental-run.
# ---------------------------------------------------------------------------
hr("Step 5 — Estimate rows-per-incremental-run")
now = datetime.now(timezone.utc)
daily_counts = []
for days_back in range(1, 8):
    day_start = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00.000")
    day_end = (now - timedelta(days=days_back - 1)).strftime("%Y-%m-%dT00:00:00.000")
    resp = get({
        "$select": "count(*)",
        "$where": f":updated_at >= '{day_start}' and :updated_at < '{day_end}'",
    })
    count = int(resp.json()[0]["count"]) if resp.status_code == 200 and resp.json() else 0
    daily_counts.append(count)
    print(f"  :updated_at in [{day_start}, {day_end}): {count} rows")

avg_daily = sum(daily_counts) / len(daily_counts) if daily_counts else 0
print(f"\nAverage rows/day with :updated_at advancing (last 7 days): {avg_daily:.0f}")
report["daily_updated_at_counts"] = daily_counts
report["avg_daily_updated_at_rows"] = avg_daily

# ---------------------------------------------------------------------------
# Requirement C — Verify resolution_action_updated_date for $where and $order.
# ---------------------------------------------------------------------------
hr("Requirement C — resolution_action_updated_date $where/$order support")
raud_where = "resolution_action_updated_date between '2026-08-01T00:00:00.000' and '2026-08-07T23:59:59.999'"
resp = get({
    "$where": raud_where,
    "$select": "unique_key,resolution_action_updated_date",
    "$order": "resolution_action_updated_date",
    "$limit": 5000,
})
print(f"HTTP status: {resp.status_code}")
rows = resp.json() if resp.status_code == 200 else []
vals = [r.get("resolution_action_updated_date") for r in rows if r.get("resolution_action_updated_date")]
out_of_window = [v for v in vals if not ("2026-08-01T00:00:00.000" <= v <= "2026-08-07T23:59:59.999")]
is_sorted = vals == sorted(vals)
print(f"Rows returned: {len(rows)}")
print(f"Rows outside requested $where window: {len(out_of_window)}")
print(f"Response sorted ascending by resolution_action_updated_date ($order): {is_sorted}")
raud_where_works = len(rows) > 0 and len(out_of_window) == 0
print(f"$where filtering confirmed working: {raud_where_works}")
print(f"$order sorting confirmed working: {is_sorted}")
report["raud_where_works"] = raud_where_works
report["raud_order_works"] = is_sorted
report["raud_rows"] = len(rows)

hr("Summary")
for k, v in report.items():
    print(f"{k}: {v}")
