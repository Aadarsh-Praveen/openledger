"""
Phase 0 / C0.3 — Source verification for erm2-nwe9 (NYC 311 Service Requests).

Throwaway diagnostic, not production code. Answers six questions the Phase 1
incremental design depends on, and prints/records a plain report. Every number
printed here is a real, live API observation — nothing is estimated or assumed.

Run: .venv/bin/python scripts/verify_source.py
"""

import os
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN")

if not APP_TOKEN:
    raise SystemExit("SOCRATA_APP_TOKEN not set — check .env and python-dotenv load.")

HEADERS = {"X-App-Token": APP_TOKEN}

report = {}


def get(params, headers=None):
    h = HEADERS if headers is None else headers
    return requests.get(BASE_URL, params=params, headers=h, timeout=30)


def hr(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------------------------------------------------------------------------
# Q1 — Reachability: fetch one row, confirm 200 + token header, print schema.
# ---------------------------------------------------------------------------
hr("Q1 — Reachability")
resp = get({"$limit": 1})
print(f"HTTP status: {resp.status_code}")
print(f"Request headers sent: X-App-Token={'present' if 'X-App-Token' in resp.request.headers else 'MISSING'}")
row = resp.json()[0] if resp.status_code == 200 and resp.json() else {}
print(f"Columns returned ({len(row)}):")
for k, v in row.items():
    print(f"  {k}: {type(v).__name__} = {v!r}")
report["q1_status"] = resp.status_code
report["q1_token_sent"] = "X-App-Token" in resp.request.headers
report["q1_columns"] = list(row.keys())

# ---------------------------------------------------------------------------
# Q2 — Does $where on created_date work? Bounded one-week window.
# ---------------------------------------------------------------------------
hr("Q2 — $where on created_date")
win_start = "2026-08-01T00:00:00.000"
win_end = "2026-08-07T23:59:59.999"
where_clause = f"created_date between '{win_start}' and '{win_end}'"
resp = get({
    "$where": where_clause,
    "$order": "created_date",
    "$limit": 5000,
    "$select": "unique_key,created_date",
})
print(f"HTTP status: {resp.status_code}")
rows = resp.json() if resp.status_code == 200 else []
print(f"Rows returned: {len(rows)}")
dates = [r["created_date"] for r in rows]
out_of_window = [d for d in dates if not (win_start <= d <= win_end)]
if dates:
    print(f"Min created_date in response: {min(dates)}")
    print(f"Max created_date in response: {max(dates)}")
print(f"Rows outside requested window: {len(out_of_window)}")
if out_of_window:
    print("STOP — $where filtering is unreliable. Examples:", out_of_window[:5])
report["q2_status"] = resp.status_code
report["q2_rows"] = len(rows)
report["q2_min_created_date"] = min(dates) if dates else None
report["q2_max_created_date"] = max(dates) if dates else None
report["q2_out_of_window_count"] = len(out_of_window)

# ---------------------------------------------------------------------------
# Q3 — Real page-size cap: request $limit=50000, count what comes back.
# ---------------------------------------------------------------------------
hr("Q3 — Page-size cap")
resp = get({"$limit": 50000, "$select": "unique_key"})
print(f"HTTP status: {resp.status_code}")
rows = resp.json() if resp.status_code == 200 else []
observed_cap = len(rows)
print(f"Requested $limit=50000, rows actually returned: {observed_cap}")
report["q3_status"] = resp.status_code
report["q3_observed_cap"] = observed_cap

# ---------------------------------------------------------------------------
# Q4 — Does $offset paginate correctly and stably?
# ---------------------------------------------------------------------------
hr("Q4 — $offset pagination stability")
page_size = 5000
order = "created_date,unique_key"

def fetch_page(offset):
    r = get({
        "$order": order,
        "$limit": page_size,
        "$offset": offset,
        "$select": "unique_key",
    })
    return r, [row["unique_key"] for row in r.json()] if r.status_code == 200 else []

r1, page1_keys = fetch_page(0)
r2, page2_keys = fetch_page(page_size)
r1b, page1_keys_repeat = fetch_page(0)

overlap = set(page1_keys) & set(page2_keys)
page1_reproducible = page1_keys == page1_keys_repeat

print(f"Page 1 (offset=0) status/count: {r1.status_code}/{len(page1_keys)}")
print(f"Page 2 (offset={page_size}) status/count: {r2.status_code}/{len(page2_keys)}")
print(f"Overlap in unique_key between page 1 and page 2: {len(overlap)}")
print(f"Page 1 re-pulled identical to first pull: {page1_reproducible}")
if overlap or not page1_reproducible:
    print("STOP — pagination is not stable/non-overlapping.")
report["q4_page1_count"] = len(page1_keys)
report["q4_page2_count"] = len(page2_keys)
report["q4_overlap_count"] = len(overlap)
report["q4_page1_reproducible"] = page1_reproducible

# ---------------------------------------------------------------------------
# Q5 — Row-count sanity for the H0.3 backfill window (24 months).
# ---------------------------------------------------------------------------
hr("Q5 — Backfill window row count (24 months)")
now = datetime.now(timezone.utc)
backfill_start = (now - timedelta(days=730)).strftime("%Y-%m-%dT00:00:00.000")
resp = get({
    "$select": "count(*)",
    "$where": f"created_date >= '{backfill_start}'",
})
print(f"HTTP status: {resp.status_code}")
count_val = resp.json()[0].get("count") if resp.status_code == 200 and resp.json() else None
print(f"Backfill window start: {backfill_start} (24 months before {now.date()})")
print(f"Row count in window: {count_val}")
report["q5_status"] = resp.status_code
report["q5_backfill_start"] = backfill_start
report["q5_row_count"] = int(count_val) if count_val else None

# ---------------------------------------------------------------------------
# Q6 — Throughput and throttling: 20 sequential paginated requests, timed.
# ---------------------------------------------------------------------------
hr("Q6 — Throughput / throttling (20 sequential requests)")
latencies = []
non_200 = []
rate_limit_headers_seen = {}
n_requests = 20
for i in range(n_requests):
    t0 = time.monotonic()
    r = get({
        "$order": order,
        "$limit": page_size,
        "$offset": i * page_size,
        "$select": "unique_key",
    })
    elapsed = time.monotonic() - t0
    latencies.append(elapsed)
    if r.status_code != 200:
        non_200.append((i, r.status_code))
    for h in r.headers:
        if "ratelimit" in h.lower() or "throttle" in h.lower():
            rate_limit_headers_seen[h] = r.headers[h]

median_latency = statistics.median(latencies)
total_elapsed = sum(latencies)
print(f"Requests made: {n_requests}")
print(f"Median request latency: {median_latency:.3f}s")
print(f"Total elapsed: {total_elapsed:.3f}s")
print(f"Non-200 responses: {non_200 if non_200 else 'none'}")
print(f"Rate-limit headers observed: {rate_limit_headers_seen if rate_limit_headers_seen else 'none'}")

report["q6_median_latency_s"] = median_latency
report["q6_total_elapsed_s"] = total_elapsed
report["q6_non_200"] = non_200
report["q6_rate_limit_headers"] = rate_limit_headers_seen

# Derive estimated backfill duration from observed rate and Q5 row count.
if report.get("q5_row_count") and report.get("q3_observed_cap"):
    pages_needed = -(-report["q5_row_count"] // report["q3_observed_cap"])  # ceil
    est_seconds = pages_needed * median_latency
    est_hours = est_seconds / 3600
    print(f"\nRows in backfill window: {report['q5_row_count']:,}")
    print(f"Observed page-size cap: {report['q3_observed_cap']:,}")
    print(f"Pages needed at that cap: {pages_needed:,}")
    print(f"Estimated backfill duration at median latency: {est_hours:.2f} hours")
    if est_hours > 2:
        print("FLAG — estimated backfill duration exceeds 2 hours.")
    report["q6_pages_needed"] = pages_needed
    report["q6_estimated_backfill_hours"] = est_hours
else:
    print("Cannot estimate backfill duration — missing Q5 row count or Q3 page cap.")

# ---------------------------------------------------------------------------
# H0.3 decision rule — applied mechanically against the measured page-size cap.
# ---------------------------------------------------------------------------
hr("H0.3 decision rule outcome")
cap = report.get("q3_observed_cap", 0)
if cap >= 10000:
    decision = "KEEP 24 months (cap >= 10,000 rows/page)"
elif cap >= 1000:
    decision = "RECOMMEND 12 months (cap 1,000-10,000 rows/page) — revise estimate"
else:
    decision = "STOP — cap < 1,000 rows/page, incremental design needs rework"
print(f"Observed page-size cap: {cap}")
print(f"Decision: {decision}")
report["h03_decision"] = decision

hr("Summary (for docs/source-notes.md)")
for k, v in report.items():
    print(f"{k}: {v}")
