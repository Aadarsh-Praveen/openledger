"""
Phase 0 / C0.4 — Data-quality baseline probe for erm2-nwe9, bounded to one month.

Throwaway diagnostic. Pulls a real one-month sample from the live API (not the full
backfill) and measures the rate of each candidate Phase 3 defect. Reports whatever the
real rates are, including "this defect barely exists" if that's what's observed.

Run: .venv/bin/python scripts/probe_data_quality.py
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN")
HEADERS = {"X-App-Token": APP_TOKEN}

WINDOW_START = "2026-07-01T00:00:00.000"
WINDOW_END = "2026-07-31T23:59:59.999"
WHERE = f"created_date between '{WINDOW_START}' and '{WINDOW_END}'"

FIELDS = "unique_key,created_date,closed_date,status,latitude,longitude,complaint_type,agency,borough"

# NYC bounding box (generous, covers all five boroughs + harbor).
NYC_LAT_MIN, NYC_LAT_MAX = 40.4959, 40.9153
NYC_LON_MIN, NYC_LON_MAX = -74.2557, -73.7002


def get(params):
    return requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)


# Count the sample first.
resp = get({"$select": "count(*)", "$where": WHERE})
sample_size = int(resp.json()[0]["count"])
print(f"Window: {WINDOW_START} to {WINDOW_END}")
print(f"Sample size (count(*)): {sample_size}")

# Pull all rows in the window, paginated.
rows = []
page_size = 50000
offset = 0
while True:
    r = get({
        "$where": WHERE,
        "$select": FIELDS,
        "$order": "created_date,unique_key",
        "$limit": page_size,
        "$offset": offset,
    })
    batch = r.json()
    if not batch:
        break
    rows.extend(batch)
    offset += page_size
    if len(batch) < page_size:
        break

print(f"Rows actually pulled: {len(rows)}")
n = len(rows)

# --- Metric 1: closed_date < created_date ---
closed_before_created = 0
for row in rows:
    cd = row.get("closed_date")
    crd = row.get("created_date")
    if cd and crd and cd < crd:
        closed_before_created += 1

# --- Metric 2: status "Closed" but null closed_date ---
closed_status_null_closed_date = sum(
    1 for row in rows if row.get("status") == "Closed" and not row.get("closed_date")
)

# --- Metric 3: null/missing latitude or longitude ---
missing_coords = sum(
    1 for row in rows if not row.get("latitude") or not row.get("longitude")
)

# --- Metric 4: coordinates at (0,0) or outside NYC bounds ---
outside_bounds = 0
for row in rows:
    lat, lon = row.get("latitude"), row.get("longitude")
    if not lat or not lon:
        continue
    try:
        lat_f, lon_f = float(lat), float(lon)
    except ValueError:
        outside_bounds += 1
        continue
    if (lat_f == 0.0 and lon_f == 0.0) or not (
        NYC_LAT_MIN <= lat_f <= NYC_LAT_MAX and NYC_LON_MIN <= lon_f <= NYC_LON_MAX
    ):
        outside_bounds += 1

# --- Metric 5: distinct complaint_type / agency counts ---
distinct_complaint_types = len({row.get("complaint_type") for row in rows if row.get("complaint_type")})
distinct_agencies = len({row.get("agency") for row in rows if row.get("agency")})

# --- Metric 6: borough "Unspecified" or null ---
unspecified_borough = sum(
    1 for row in rows if not row.get("borough") or row.get("borough") == "Unspecified"
)


def pct(count):
    return (count / n * 100) if n else 0.0


print("\n=== Data-quality baseline (July 2026, N={}) ===".format(n))
print(f"1. closed_date < created_date:            {closed_before_created} ({pct(closed_before_created):.4f}%)")
print(f"2. status=Closed, closed_date null:        {closed_status_null_closed_date} ({pct(closed_status_null_closed_date):.4f}%)")
print(f"3. missing latitude/longitude:             {missing_coords} ({pct(missing_coords):.4f}%)")
print(f"4. (0,0) or outside NYC bounds:             {outside_bounds} ({pct(outside_bounds):.4f}%)")
print(f"5. distinct complaint_type: {distinct_complaint_types}; distinct agency: {distinct_agencies}")
print(f"6. borough Unspecified/null:                {unspecified_borough} ({pct(unspecified_borough):.4f}%)")
