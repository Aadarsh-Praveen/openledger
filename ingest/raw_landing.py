"""C1.3 — raw landing layer. Each API page lands as Parquet under
raw/ingest_date=<YYYY-MM-DD>/, preserving the payload with minimal transformation
(the schema.rows_to_arrow type coercion — timestamps and numbers, nothing else).
Raw is immutable and replayable: never edit a landed file, only add new ones."""

import uuid
from datetime import datetime, timezone

import pyarrow.parquet as pq

from ingest.config import RAW_DIR
from ingest.schema import rows_to_arrow


def land_page(rows, window_label, page_index, ingest_date=None):
    """Write one API page (list of raw row dicts) as a Parquet file under
    raw/ingest_date=<date>/. Returns (path, byte_size, row_count)."""
    if ingest_date is None:
        ingest_date = datetime.now(timezone.utc).date().isoformat()
    day_dir = RAW_DIR / f"ingest_date={ingest_date}"
    day_dir.mkdir(parents=True, exist_ok=True)

    table = rows_to_arrow(rows)
    fname = f"{window_label}_page{page_index:04d}_{uuid.uuid4().hex[:8]}.parquet"
    path = day_dir / fname
    pq.write_table(table, path)
    return path, path.stat().st_size, len(rows)
