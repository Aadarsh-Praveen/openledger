"""
C1.5 — Iceberg bronze table: format version 2, partitioned by day(created_date)
(stable — created_date never changes for a given unique_key, so rows never migrate
partitions), written via upsert() on unique_key.
"""

from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.transforms import DayTransform

from ingest.config import BRONZE_NAMESPACE, BRONZE_TABLE_ID, CATALOG_DIR, WAREHOUSE_DIR
from ingest.schema import iceberg_schema

CATALOG_DIR.mkdir(exist_ok=True)
WAREHOUSE_DIR.mkdir(exist_ok=True)


def get_catalog():
    return SqlCatalog(
        "openledger",
        **{"uri": f"sqlite:///{CATALOG_DIR}/catalog.db", "warehouse": f"file://{WAREHOUSE_DIR}"},
    )


def get_or_create_bronze_table():
    catalog = get_catalog()
    if (BRONZE_NAMESPACE,) not in catalog.list_namespaces():
        catalog.create_namespace(BRONZE_NAMESPACE)
    if catalog.table_exists(BRONZE_TABLE_ID):
        return catalog.load_table(BRONZE_TABLE_ID)

    schema = iceberg_schema()
    # created_date is field_id=2 in ingest/schema.py's COLUMNS.
    spec = PartitionSpec(
        PartitionField(source_id=2, field_id=1000, transform=DayTransform(), name="created_date_day")
    )
    table = catalog.create_table(
        BRONZE_TABLE_ID,
        schema=schema,
        partition_spec=spec,
        properties={"format-version": "2"},
    )
    assert table.format_version == 2, "STOP — bronze table is not format version 2"
    return table


def partitions_touched(arrow_table):
    """Distinct created_date (day) values present in a batch, as ISO date strings."""
    col = arrow_table.column("created_date")
    days = set()
    for v in col.to_pylist():
        if v is not None:
            days.add(v.date().isoformat())
    return sorted(days)


def scoped_upsert(table, arrow_table, join_cols=("unique_key",)):
    """Upsert a batch, scoped to the created_date-day partitions it touches.

    PyIceberg's built-in table.upsert() builds its "which existing rows might
    match" scan purely from an `unique_key IN (...)` predicate — it has no
    partition-scoping parameter, so it can't take advantage of the
    day(created_date) partition spec even though our batches are chunked by
    created_date. This reimplements the same match/diff/write logic (reusing
    pyiceberg.table.upsert_util, the same helpers upsert() itself uses) but ANDs a
    created_date partition-range predicate into the initial scan, so Iceberg's scan
    planner can prune whole partitions before ever looking at unique_key.
    """
    from datetime import datetime, timedelta

    import pyarrow as pa
    from pyiceberg.expressions import And, GreaterThanOrEqual, LessThan, Reference, literal
    from pyiceberg.table import upsert_util

    join_col = join_cols[0]
    assert len(join_cols) == 1, "scoped_upsert only supports a single join column (unique_key)"

    days = partitions_touched(arrow_table)
    if not days:
        result = table.upsert(arrow_table, join_cols=list(join_cols))
        return {"rows_updated": result.rows_updated, "rows_inserted": result.rows_inserted}

    min_day = datetime.fromisoformat(min(days))
    max_day_exclusive = datetime.fromisoformat(max(days)) + timedelta(days=1)
    partition_predicate = And(
        GreaterThanOrEqual(Reference("created_date"), literal(min_day)),
        LessThan(Reference("created_date"), literal(max_day_exclusive)),
    )
    match_predicate = upsert_util.create_match_filter(arrow_table, [join_col])
    combined_predicate = And(partition_predicate, match_predicate)

    existing = table.scan(row_filter=combined_predicate).to_arrow()

    rows_to_update = upsert_util.get_rows_to_update(arrow_table, existing, [join_col])

    updated = 0
    if len(rows_to_update) > 0:
        overwrite_filter = And(partition_predicate, upsert_util.create_match_filter(rows_to_update, [join_col]))
        table.overwrite(rows_to_update, overwrite_filter=overwrite_filter)
        updated = len(rows_to_update)

    if len(existing) > 0:
        existing_keys = set(existing.column(join_col).to_pylist())
        incoming_keys = arrow_table.column(join_col).to_pylist()
        mask = pa.array([k not in existing_keys for k in incoming_keys])
        rows_to_insert = arrow_table.filter(mask)
    else:
        rows_to_insert = arrow_table

    inserted = 0
    if len(rows_to_insert) > 0:
        table.append(rows_to_insert)
        inserted = len(rows_to_insert)

    return {"rows_updated": updated, "rows_inserted": inserted}
