"""
C6.1 requirement 4 — the ONE-TIME bulk seed of bronze into S3.

This is a manual operation, run once, explicitly, watched — exactly like
the Phase 1 backfill it mirrors in spirit. It is NOT part of any
scheduled workflow. After this runs successfully once, every subsequent
scheduled run only ever writes the day's delta directly to S3 (C6.1's
whole point) — this script's O(total warehouse size) cost is paid
exactly once.

What it does: creates a NEW Iceberg table at the S3 warehouse root, with
the identical schema and partition spec as the local bronze table
(read directly from the local table's own metadata, not hand-copied, so
there is no risk of a manual transcription mismatch), then copies all
data across in monthly batches — mirroring the same monthly-window
structure Phase 1's original backfill used, for the same reason
(bounded memory, visible progress, a natural retry unit if one batch
fails).

Usage:
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=... \
        .venv/bin/python scripts/seed_bronze_to_s3.py

Requires the bucket to be empty at the target prefix (refuses to run
otherwise — this is a one-time seed, not a sync/merge tool).
"""

import os
import sys
import time
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyiceberg.catalog.sql import SqlCatalog
from ingest.config import CATALOG_DIR, WAREHOUSE_DIR, BRONZE_TABLE_ID, BRONZE_NAMESPACE

S3_BUCKET = "openledger-lakehouse-025044153778"
S3_WAREHOUSE_ROOT = f"s3://{S3_BUCKET}/warehouse"
S3_CATALOG_DB = Path(__file__).resolve().parent.parent / "state" / "s3_catalog.db"


def s3_credentials() -> dict:
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"):
        if not os.environ.get(var):
            print(f"ERROR: {var} not set. This script needs the bucket-scoped credentials.", file=sys.stderr)
            sys.exit(1)
    return {
        "s3.access-key-id": os.environ["AWS_ACCESS_KEY_ID"],
        "s3.secret-access-key": os.environ["AWS_SECRET_ACCESS_KEY"],
        "s3.region": os.environ["AWS_DEFAULT_REGION"],
    }


def main() -> None:
    local_catalog = SqlCatalog(
        "openledger", uri=f"sqlite:///{CATALOG_DIR}/catalog.db", warehouse=f"file://{WAREHOUSE_DIR}"
    )
    local_table = local_catalog.load_table(BRONZE_TABLE_ID)
    schema = local_table.schema()
    spec = local_table.spec()
    total_rows_local = local_table.current_snapshot().summary.get("total-records")
    print(f"Local bronze: {total_rows_local} rows, schema has {len(schema.fields)} fields, spec: {spec}")

    s3_catalog = SqlCatalog(
        "openledger",
        uri=f"sqlite:///{S3_CATALOG_DB}",
        warehouse=S3_WAREHOUSE_ROOT,
        **s3_credentials(),
    )
    s3_catalog.create_namespace_if_not_exists(BRONZE_NAMESPACE)

    if s3_catalog.table_exists(BRONZE_TABLE_ID):
        print(
            f"ERROR: {BRONZE_TABLE_ID} already exists at {S3_WAREHOUSE_ROOT}. "
            "This is a one-time seed for an empty target, not a sync tool. "
            "If re-seeding is genuinely intended, drop the table first — deliberately, "
            "not automatically, from this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    s3_table = s3_catalog.create_table(BRONZE_TABLE_ID, schema=schema, partition_spec=spec)
    print(f"Created S3-native table at {s3_table.metadata_location}")

    # Monthly batches, mirroring the original Phase 1 backfill's own window
    # structure — bounded memory, visible progress per batch.
    con_query = f"""
        select distinct date_trunc('month', created_date) as month
        from local_scan
        order by 1
    """
    import duckdb

    duck = duckdb.connect()
    # Force UTC explicitly — DuckDB's session TimeZone (defaults to the host
    # machine's local zone) otherwise re-types timestamptz columns to that
    # local zone on Arrow round-trip (here: 'America/New_York' instead of
    # 'UTC'), and PyIceberg only accepts UTC-normalized timestamp[us, tz=UTC]
    # for its timestamptz type — the same class of session-timezone-dependent
    # bug already found and fixed in int_request_resolution.sql (C3.3).
    duck.execute("SET TimeZone='UTC'")
    local_arrow = local_table.scan().to_arrow()
    duck.register("local_scan", local_arrow)
    months = [r[0] for r in duck.execute(con_query).fetchall()]
    print(f"{len(months)} monthly batches to write")

    total_written = 0
    t0 = time.time()
    for i, month in enumerate(months, 1):
        batch = duck.execute(
            "select * from local_scan where date_trunc('month', created_date) = ?", [month]
        ).to_arrow_table()
        # unique_key is `required` in the Iceberg schema (never actually null
        # in the data), but DuckDB's Arrow round-trip marks every column
        # nullable regardless — a schema-metadata mismatch, not a data
        # issue. Enforce it explicitly before appending.
        idx = batch.schema.get_field_index("unique_key")
        fixed_schema = batch.schema.set(idx, pa.field("unique_key", pa.string(), nullable=False))
        batch = batch.cast(fixed_schema)
        s3_table.append(batch)
        total_written += batch.num_rows
        elapsed = time.time() - t0
        print(f"[{i}/{len(months)}] {month.date()}: +{batch.num_rows} rows "
              f"(cumulative {total_written}, {elapsed:.0f}s elapsed)", flush=True)

    print(f"\nDone. {total_written} rows written to S3 in {time.time() - t0:.0f}s.")
    print(f"S3 catalog db: {S3_CATALOG_DB} (small — commit this to git alongside watermark/checkpoint)")


if __name__ == "__main__":
    main()
