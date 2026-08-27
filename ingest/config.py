"""
Central configuration for Phase 1 ingestion. Every number here traces back to a
measured Phase 0 / Phase 1 finding recorded in docs/decisions.md — do not hand-edit
without updating the journal entry it came from.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=ROOT / ".env")

BASE_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN")
if not APP_TOKEN:
    raise RuntimeError("SOCRATA_APP_TOKEN not set — check .env")
HEADERS = {"X-App-Token": APP_TOKEN}

# C0.3: observed page-size cap.
PAGE_SIZE = 50_000

# H0.3 / C0.3.5: locked 24-month backfill window, decision rule satisfied (cap
# 50,000 >= 10,000 threshold). Bounds are fixed calendar dates, not relative to
# "now", so re-running this module later doesn't silently shift the backfill scope.
BACKFILL_START = "2024-08-19T00:00:00.000"
BACKFILL_END_EXCLUSIVE = None  # None = up to "now" at run time, resolved by caller

# C1.1b / decisions.md requirement A: lookback buffer and window-boundary anchor.
WATERMARK_BUFFER_HOURS = 48
BATCH_BOUNDARY_ANCHOR_UTC_HOUR = 3  # clears the observed 01:33-02:03 UTC cluster

# Retry/backoff for non-200 responses.
MAX_RETRIES = 5
RETRY_BACKOFF_BASE_SECONDS = 2

# C6.2: alarm after this many consecutive scheduled runs with no watermark
# advance. At a daily cron, 2 = ~48h of silence — one quiet day fits the
# observed steady-state/publish-lag distribution (see docs/decisions.md);
# two in a row does not.
STALENESS_ALARM_THRESHOLD = 2

RAW_DIR = ROOT / "raw"
STATE_DIR = ROOT / "state"
RAW_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)

CATALOG_DIR = ROOT / "catalog"
WAREHOUSE_DIR = ROOT / "warehouse"

BRONZE_NAMESPACE = "bronze"
BRONZE_TABLE = "service_requests"
BRONZE_TABLE_ID = f"{BRONZE_NAMESPACE}.{BRONZE_TABLE}"

# C6.1: the S3-native bronze catalog seeded by scripts/seed_bronze_to_s3.py.
# Same catalog name ("openledger") as the local catalog is deliberate —
# PyIceberg's SqlCatalog scopes tables by catalog_name in its own metadata
# tables; a mismatched name here caused a false NoSuchTableError during the
# atomicity test (see docs/decisions.md, C6.1 build requirement 3).
S3_BUCKET = "openledger-lakehouse-025044153778"
S3_WAREHOUSE_ROOT = f"s3://{S3_BUCKET}/warehouse"
S3_CATALOG_DB = STATE_DIR / "s3_catalog.db"

WATERMARK_FIELD = ":updated_at"
ORDER_TIEBREAKER = "unique_key"
