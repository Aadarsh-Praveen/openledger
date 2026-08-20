"""
Phase 1 / C1.1 — Verify watermark field availability for erm2-nwe9, BEFORE writing
any ingestion code.

The plan's working assumption ("watermark on :updated_at") is exactly the thing this
script must test, not assume. Every finding below comes from a live API call.

Throwaway diagnostic. Run: .venv/bin/python scripts/probe_watermark.py
"""

import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN")
if not APP_TOKEN:
    raise SystemExit("SOCRATA_APP_TOKEN not set — check .env")
HEADERS = {"X-App-Token": APP_TOKEN}

report = {}


def get(params):
    return requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)


def hr(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------------------------------------------------------------------------
# Q1 — Does :updated_at exist and is it selectable?
# ---------------------------------------------------------------------------
hr("Q1 — :updated_at selectable?")
resp = get({"$select": "unique_key,created_date,:updated_at", "$limit": 5})
print(f"HTTP status: {resp.status_code}")
rows = resp.json() if resp.status_code == 200 else []
for r in rows:
    print(f"  {r}")
has_updated_at = bool(rows) and all(":updated_at" in r for r in rows)
print(f":updated_at present in every sample row: {has_updated_at}")
report["q1_status"] = resp.status_code
report["q1_present"] = has_updated_at
report["q1_sample"] = rows[:2]

# ---------------------------------------------------------------------------
# Q2 — Is :updated_at filterable in $where? Verify by comparing min/max, not HTTP 200.
# ---------------------------------------------------------------------------
hr("Q2 — :updated_at filterable in $where?")
win_start = "2026-08-01T00:00:00.000"
win_end = "2026-08-07T23:59:59.999"
resp = get({
    "$where": f":updated_at between '{win_start}' and '{win_end}'",
    "$select": "unique_key,:updated_at",
    "$order": ":updated_at",
    "$limit": 5000,
})
print(f"HTTP status: {resp.status_code}")
rows = resp.json() if resp.status_code == 200 else []
vals = [r.get(":updated_at") for r in rows if r.get(":updated_at")]
out_of_window = [v for v in vals if not (win_start <= v <= win_end)]
print(f"Rows returned: {len(rows)}")
print(f"Min :updated_at in response: {min(vals) if vals else None}")
print(f"Max :updated_at in response: {max(vals) if vals else None}")
print(f"Rows outside requested window: {len(out_of_window)}")
filter_works = len(rows) > 0 and len(out_of_window) == 0
print(f"$where on :updated_at actually filters (not silently ignored): {filter_works}")
report["q2_status"] = resp.status_code
report["q2_rows"] = len(rows)
report["q2_out_of_window"] = len(out_of_window)
report["q2_filter_works"] = filter_works

# ---------------------------------------------------------------------------
# Q3 — Is :updated_at orderable in $order?
# ---------------------------------------------------------------------------
hr("Q3 — :updated_at orderable?")
resp = get({
    "$order": ":updated_at,unique_key",
    "$select": "unique_key,:updated_at",
    "$limit": 10,
})
print(f"HTTP status: {resp.status_code}")
rows = resp.json() if resp.status_code == 200 else []
vals = [r.get(":updated_at") for r in rows]
is_sorted = vals == sorted(vals)
print(f"Returned :updated_at values: {vals}")
print(f"Response is sorted ascending by :updated_at: {is_sorted}")
report["q3_status"] = resp.status_code
report["q3_sorted"] = is_sorted

# ---------------------------------------------------------------------------
# Q4 — Does :updated_at actually diverge from created_date for old rows?
# ---------------------------------------------------------------------------
hr("Q4 — :updated_at vs created_date divergence (rows >= 60 days old)")
cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT00:00:00.000")
old_window_start = "2026-05-01T00:00:00.000"
old_window_end = "2026-05-08T00:00:00.000"
resp = get({
    "$where": f"created_date between '{old_window_start}' and '{old_window_end}'",
    "$select": "unique_key,created_date,:updated_at,status",
    "$order": "created_date,unique_key",
    "$limit": 5000,
})
print(f"HTTP status: {resp.status_code}")
print(f"Cutoff for 'at least 60 days old' (informational): {cutoff}")
rows = resp.json() if resp.status_code == 200 else []
print(f"Rows in old window ({old_window_start} to {old_window_end}): {len(rows)}")

MATERIAL_DELTA = timedelta(hours=1)


def parse_ts(s):
    # created_date comes back offset-naive ("...000"); :updated_at comes back
    # offset-aware with a trailing "Z" ("...582Z"). Normalize both to naive UTC
    # so they're comparable — this mismatch itself is a real finding, not
    # incidental, and is recorded in the report below.
    s = s.replace("Z", "")
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=None)


diverged = 0
for r in rows:
    cd, ua = r.get("created_date"), r.get(":updated_at")
    if not cd or not ua:
        continue
    try:
        if parse_ts(ua) - parse_ts(cd) > MATERIAL_DELTA:
            diverged += 1
    except ValueError:
        continue

diverge_pct = (diverged / len(rows) * 100) if rows else 0.0
print(f"Rows where :updated_at > created_date by more than 1 hour: {diverged} ({diverge_pct:.2f}%)")
report["q4_status"] = resp.status_code
report["q4_sample_size"] = len(rows)
report["q4_diverged_count"] = diverged
report["q4_diverged_pct"] = diverge_pct

# Follow-up: Q1's sample showed multiple rows sharing one identical :updated_at
# timestamp despite different created_date values — check whether divergence in
# this sample is genuine per-row modification tracking or a single bulk-touch
# event (e.g. the Dec 2025 dataset restructuring) masquerading as "updates".
from collections import Counter
updated_at_counts = Counter(r.get(":updated_at") for r in rows if r.get(":updated_at"))
most_common = updated_at_counts.most_common(5)
distinct_updated_at = len(updated_at_counts)
print(f"Distinct :updated_at values in this {len(rows)}-row sample: {distinct_updated_at}")
print(f"5 most common :updated_at values (value, count): {most_common}")
dominant_value, dominant_count = most_common[0] if most_common else (None, 0)
dominant_share_pct = (dominant_count / len(rows) * 100) if rows else 0.0
print(f"Share of sample sharing the single most common :updated_at value: {dominant_share_pct:.2f}%")
report["q4_distinct_updated_at_values"] = distinct_updated_at
report["q4_dominant_updated_at_value"] = dominant_value
report["q4_dominant_updated_at_share_pct"] = dominant_share_pct

# Follow-up: does :updated_at track closure promptly, or lag it by a variable,
# sometimes large amount? Pull closed_date alongside and measure the gap for
# genuinely closed rows in the same old window.
resp = get({
    "$where": f"created_date between '{old_window_start}' and '{old_window_end}'",
    "$select": "unique_key,created_date,closed_date,:updated_at,status",
    "$order": "created_date,unique_key",
    "$limit": 5000,
})
closed_rows = [r for r in resp.json() if r.get("status") == "Closed" and r.get("closed_date")]
lags_days = []
for r in closed_rows:
    try:
        lag = (parse_ts(r[":updated_at"]) - parse_ts(r["closed_date"])).total_seconds() / 86400
        lags_days.append(lag)
    except (KeyError, ValueError):
        continue
if lags_days:
    lags_days.sort()
    median_lag = lags_days[len(lags_days) // 2]
    max_lag = max(lags_days)
    min_lag = min(lags_days)
    print(f"\nClosed-row lag (:updated_at minus closed_date), n={len(lags_days)}:")
    print(f"  min={min_lag:.2f}d  median={median_lag:.2f}d  max={max_lag:.2f}d")
    report["q4_closed_lag_min_days"] = min_lag
    report["q4_closed_lag_median_days"] = median_lag
    report["q4_closed_lag_max_days"] = max_lag

# ---------------------------------------------------------------------------
# Q5 — Other candidate modification-timestamp fields among the 44 columns?
# ---------------------------------------------------------------------------
hr("Q5 — Other candidate fields (:version, resolution_action_updated_date, etc.)")
resp = get({"$limit": 1})
print(f"HTTP status: {resp.status_code}")
row = resp.json()[0] if resp.status_code == 200 and resp.json() else {}
all_keys = list(row.keys())
candidates = [k for k in all_keys if "update" in k.lower() or "version" in k.lower() or k.startswith(":")]
print(f"All keys in a live row: {all_keys}")
print(f"Candidate modification-timestamp-like fields found: {candidates}")
report["q5_all_keys"] = all_keys
report["q5_candidates"] = candidates

# ---------------------------------------------------------------------------
# Q6 — Staleness risk: of requests created in a recent 30-day window, what
# fraction are still open at pull time?
# ---------------------------------------------------------------------------
hr("Q6 — Staleness risk (recent 30-day window, fraction still open)")
now = datetime.now(timezone.utc)
recent_start = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000")
recent_end = now.strftime("%Y-%m-%dT%H:%M:%S.000")
resp = get({
    "$select": "status,count(*)",
    "$where": f"created_date between '{recent_start}' and '{recent_end}'",
    "$group": "status",
})
print(f"HTTP status: {resp.status_code}")
status_counts = resp.json() if resp.status_code == 200 else []
print(f"Window: {recent_start} to {recent_end}")
print(f"Status breakdown: {status_counts}")
total = sum(int(r["count"]) for r in status_counts)
open_count = sum(int(r["count"]) for r in status_counts if r.get("status") != "Closed")
open_pct = (open_count / total * 100) if total else 0.0
print(f"Total rows in window: {total}")
print(f"Still-open (non-Closed) rows: {open_count} ({open_pct:.2f}%)")
report["q6_status_breakdown"] = status_counts
report["q6_total"] = total
report["q6_open_pct"] = open_pct

hr("Summary")
for k, v in report.items():
    print(f"{k}: {v}")
