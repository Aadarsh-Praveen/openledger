"""
Phase 1 / C1.3b — Does PyIceberg's upsert() already skip no-op updates?

Before hand-rolling a pre-upsert value-diff filter (design option ii), check
empirically whether upsert() already avoids writing a matched row when the incoming
values are byte-identical to the existing row. Test: create a small table, insert a
batch, then upsert() an identical copy of one row and observe snapshot count and
data-file count before/after. Then upsert() a batch with one genuinely changed row
and one byte-identical row, to see whether PyIceberg can tell them apart within a
single batch.

Throwaway smoke test. Run: .venv/bin/python scripts/probe_upsert_noop.py
"""

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField, StringType, TimestampType

ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / "catalog"
WAREHOUSE_DIR = ROOT / "warehouse"
CATALOG_DIR.mkdir(exist_ok=True)
WAREHOUSE_DIR.mkdir(exist_ok=True)

NAMESPACE = "smoke"
TABLE_NAME = "upsert_noop_test"
TABLE_ID = f"{NAMESPACE}.{TABLE_NAME}"


def hr(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def snapshot_count(t):
    t.refresh()
    return len(list(t.snapshots()))


def data_file_count(t):
    t.refresh()
    files = set()
    for task in t.scan().plan_files():
        files.add(task.file.file_path)
    return len(files)


catalog = SqlCatalog(
    "phase1_smoke",
    **{"uri": f"sqlite:///{CATALOG_DIR}/catalog.db", "warehouse": f"file://{WAREHOUSE_DIR}"},
)
if (NAMESPACE,) not in catalog.list_namespaces():
    catalog.create_namespace(NAMESPACE)
if catalog.table_exists(TABLE_ID):
    catalog.drop_table(TABLE_ID)

schema = Schema(
    NestedField(field_id=1, name="id", field_type=LongType(), required=True),
    NestedField(field_id=2, name="value", field_type=StringType(), required=False),
    NestedField(field_id=3, name="event_ts", field_type=TimestampType(), required=True),
)
pa_schema = pa.schema([
    pa.field("id", pa.int64(), nullable=False),
    pa.field("value", pa.string(), nullable=True),
    pa.field("event_ts", pa.timestamp("us"), nullable=False),
])

hr("Setup")
table = catalog.create_table(TABLE_ID, schema=schema, properties={"format-version": "2"})
ts = datetime(2026, 8, 1, tzinfo=timezone.utc)
initial = pa.table({"id": [1, 2, 3], "value": ["a", "b", "c"], "event_ts": pa.array([ts] * 3, type=pa.timestamp("us"))}, schema=pa_schema)
table.append(initial)
snap0 = snapshot_count(table)
files0 = data_file_count(table)
print(f"After initial append: snapshots={snap0}, data files={files0}")

hr("Test 1 — upsert() a batch that is BYTE-IDENTICAL to existing rows (no-op)")
identical_batch = pa.table({"id": [1, 2, 3], "value": ["a", "b", "c"], "event_ts": pa.array([ts] * 3, type=pa.timestamp("us"))}, schema=pa_schema)
result = table.upsert(identical_batch, join_cols=["id"])
snap1 = snapshot_count(table)
files1 = data_file_count(table)
print(f"UpsertResult: rows_updated={result.rows_updated}, rows_inserted={result.rows_inserted}")
print(f"After no-op upsert: snapshots={snap1} (was {snap0}), data files={files1} (was {files0})")
no_op_created_snapshot = snap1 > snap0
no_op_wrote_files = files1 > files0
print(f"Created a new snapshot for a no-op upsert: {no_op_created_snapshot}")
print(f"Wrote new data files for a no-op upsert: {no_op_wrote_files}")

hr("Test 2 — upsert() a MIXED batch: 1 genuinely changed row + 2 identical rows")
mixed_batch = pa.table({
    "id": [1, 2, 3],
    "value": ["a-CHANGED", "b", "c"],
    "event_ts": pa.array([ts] * 3, type=pa.timestamp("us")),
}, schema=pa_schema)
result2 = table.upsert(mixed_batch, join_cols=["id"])
snap2 = snapshot_count(table)
files2 = data_file_count(table)
row_after = table.scan(row_filter="id == 1").to_arrow().to_pylist()[0]
print(f"UpsertResult: rows_updated={result2.rows_updated}, rows_inserted={result2.rows_inserted}")
print(f"Row id=1 value after mixed upsert: {row_after['value']}")
print(f"After mixed upsert: snapshots={snap2} (was {snap1}), data files={files2} (was {files1})")

hr("RESULT")
print(f"rows_updated on the pure no-op upsert (Test 1): {result.rows_updated}")
print(f"rows_updated on the mixed upsert (Test 2, 1 real change among 3): {result2.rows_updated}")
if result.rows_updated == 0:
    verdict = "PyIceberg's upsert() ALREADY SKIPS no-op rows (rows_updated=0 for identical batch). No hand-rolled diff-filter needed."
elif result.rows_updated == len(identical_batch) and result2.rows_updated == 1:
    verdict = "PyIceberg's upsert() does NOT skip no-ops at the whole-batch level, but DOES detect per-row equality within a mixed batch (see Test 2). Needs further characterization."
else:
    verdict = "PyIceberg's upsert() does NOT skip no-op rows — it writes/counts identical rows as updates regardless of value equality. A pre-upsert diff-filter would provide real savings."
print(f"\nVERDICT: {verdict}")

hr("Cleanup")
catalog.purge_table(TABLE_ID)
catalog.drop_namespace(NAMESPACE)
print("Test table dropped and purged.")
