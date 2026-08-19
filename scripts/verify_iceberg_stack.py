"""
Phase 0 / C0.5 — Smallest possible end-to-end proof of the bronze-layer approach:
PyIceberg + SQLite catalog + local Parquet warehouse, format v2, partitioned,
appended, upserted, time-travelled, and read back from DuckDB.

Throwaway smoke test, not a fixture. Deletes the test table and its files at the end.

Run: .venv/bin/python scripts/verify_iceberg_stack.py
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import DayTransform
from pyiceberg.types import LongType, NestedField, StringType, TimestampType

ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / "catalog"
WAREHOUSE_DIR = ROOT / "warehouse"
CATALOG_DIR.mkdir(exist_ok=True)
WAREHOUSE_DIR.mkdir(exist_ok=True)

NAMESPACE = "smoke"
TABLE_NAME = "phase0_test"
TABLE_ID = f"{NAMESPACE}.{TABLE_NAME}"


def hr(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


hr("Setup — SqlCatalog (SQLite) + local warehouse")
catalog = SqlCatalog(
    "phase0_smoke",
    **{
        "uri": f"sqlite:///{CATALOG_DIR}/catalog.db",
        "warehouse": f"file://{WAREHOUSE_DIR}",
    },
)
if (NAMESPACE,) not in catalog.list_namespaces():
    catalog.create_namespace(NAMESPACE)
print(f"Catalog created at {CATALOG_DIR}/catalog.db, warehouse at {WAREHOUSE_DIR}")

schema = Schema(
    NestedField(field_id=1, name="id", field_type=LongType(), required=True),
    NestedField(field_id=2, name="value", field_type=StringType(), required=False),
    NestedField(field_id=3, name="event_ts", field_type=TimestampType(), required=True),
)

if catalog.table_exists(TABLE_ID):
    catalog.drop_table(TABLE_ID)

hr("Step 2 — Create table, format version 2, partitioned by day(event_ts)")
table = catalog.create_table(
    TABLE_ID,
    schema=schema,
    partition_spec=PartitionSpec(
        PartitionField(
            source_id=3,  # event_ts
            field_id=1000,
            transform=DayTransform(),
            name="event_ts_day",
        )
    ),
    properties={"format-version": "2"},
)
print(f"Table created: {table.name()}")
print(f"Format version: {table.format_version}")
assert table.format_version == 2, "STOP — table is not format version 2"

pa_schema = pa.schema([
    pa.field("id", pa.int64(), nullable=False),
    pa.field("value", pa.string(), nullable=True),
    pa.field("event_ts", pa.timestamp("us"), nullable=False),
])

hr("Step 3 — Append first batch, confirm snapshot exists")
batch1 = pa.table({
    "id": [1, 2, 3],
    "value": ["a", "b", "c"],
    "event_ts": pa.array(
        [datetime(2026, 8, 1, tzinfo=timezone.utc)] * 3, type=pa.timestamp("us")
    ),
}, schema=pa_schema)
table.append(batch1)
table.refresh()
snapshots_after_append1 = list(table.snapshots())
print(f"Snapshot count after append 1: {len(snapshots_after_append1)}")
first_snapshot_id = snapshots_after_append1[-1].snapshot_id
row_count_after_1 = len(table.scan().to_arrow())
print(f"Row count after append 1: {row_count_after_1}")
assert len(snapshots_after_append1) >= 1, "STOP — no snapshot after first append"

hr("Step 4 — Append second batch, confirm snapshot count incremented")
batch2 = pa.table({
    "id": [4, 5],
    "value": ["d", "e"],
    "event_ts": pa.array(
        [datetime(2026, 8, 2, tzinfo=timezone.utc)] * 2, type=pa.timestamp("us")
    ),
}, schema=pa_schema)
table.append(batch2)
table.refresh()
snapshots_after_append2 = list(table.snapshots())
print(f"Snapshot count after append 2: {len(snapshots_after_append2)}")
row_count_after_2 = len(table.scan().to_arrow())
print(f"Row count after append 2: {row_count_after_2}")
assert len(snapshots_after_append2) > len(snapshots_after_append1), "STOP — snapshot count did not increment"
assert row_count_after_2 == row_count_after_1 + 2, "STOP — row count did not grow by batch 2 size"

hr("Step 5 — Upsert a colliding row, confirm row count unchanged, value updated")
upsert_batch = pa.table({
    "id": [3],
    "value": ["c-UPDATED"],
    "event_ts": pa.array([datetime(2026, 8, 1, tzinfo=timezone.utc)], type=pa.timestamp("us")),
}, schema=pa_schema)
table.upsert(upsert_batch, join_cols=["id"])
table.refresh()
row_count_after_upsert = len(table.scan().to_arrow())
updated_row = table.scan(row_filter="id == 3").to_arrow().to_pylist()[0]
print(f"Row count after upsert: {row_count_after_upsert}")
print(f"Row id=3 value after upsert: {updated_row['value']}")
assert row_count_after_upsert == row_count_after_2, "STOP — upsert changed row count"
assert updated_row["value"] == "c-UPDATED", "STOP — upsert did not update the value"

hr("Step 6 — Time-travel read against the first snapshot")
pre_second_batch = table.scan(snapshot_id=first_snapshot_id).to_arrow()
print(f"Row count at first snapshot (time travel): {len(pre_second_batch)}")
print(f"IDs present at first snapshot: {sorted(pre_second_batch.column('id').to_pylist())}")
assert len(pre_second_batch) == row_count_after_1, "STOP — time-travel row count mismatch"
assert sorted(pre_second_batch.column("id").to_pylist()) == [1, 2, 3], "STOP — time-travel contents mismatch"

hr("Step 7 — Read the same table from DuckDB via the Iceberg extension (metadata path)")
metadata_location = table.metadata_location
table_root = table.location()
print(f"Metadata location: {metadata_location}")
print(f"Table root location: {table_root}")
con = duckdb.connect()
con.execute("INSTALL iceberg")
con.execute("LOAD iceberg")
# This build of the DuckDB iceberg extension resolves iceberg_scan's path argument
# as the table root (it looks for <path>/metadata/... itself), not a metadata.json
# file — passing the metadata_location produced "<file>.metadata.json/metadata/..."
# path-join errors. Pass the table root instead. PyIceberg's SqlCatalog doesn't
# write a version-hint.text file, so DuckDB also needs explicit permission to
# glob the metadata directory for the latest version.
con.execute("SET unsafe_enable_version_guessing = true")
duckdb_count = con.execute(
    "SELECT count(*) FROM iceberg_scan(?, allow_moved_paths => true)", [table_root]
).fetchone()[0]
print(f"Row count via DuckDB iceberg_scan: {duckdb_count}")
print(f"Row count via PyIceberg: {row_count_after_upsert}")
assert duckdb_count == row_count_after_upsert, "STOP — DuckDB and PyIceberg row counts do not match"
con.close()

hr("Step 8 — On-disk warehouse layout")
layout_lines = []
for path in sorted(WAREHOUSE_DIR.rglob("*")):
    rel = path.relative_to(WAREHOUSE_DIR)
    layout_lines.append(f"{'  ' * (len(rel.parts) - 1)}{rel.name}{'/' if path.is_dir() else ''}")
layout_text = "\n".join(layout_lines)
print(layout_text)

hr("Cleanup — dropping test table and purging files")
catalog.purge_table(TABLE_ID)
catalog.drop_namespace(NAMESPACE)
print("Test table and namespace dropped.")

hr("RESULT")
print("All C0.5 assertions passed: format v2 confirmed, snapshots incremented,")
print("upsert row-count-stable, time-travel correct, DuckDB read matches PyIceberg.")

# Write the layout to a scratch file for the calling shell to pick up (docs update
# is done by hand afterward, this just avoids re-typing the tree).
(ROOT / "docs" / "_warehouse_layout_snapshot.txt").write_text(layout_text)
