"""
C6.5 (Variant B) — append today's DQ scorecard snapshot to the git-tracked
history CSV.

Why this exists
---------------
`fct_data_quality_checks` is a plain table model that emits ONLY today's
snapshot (one row per check_name/grain for the current run_date). It used
to be `materialized='incremental'` so history would accumulate in the
warehouse — but `dbt/target/openledger_prod.duckdb` is never persisted
between CI runs (fresh ephemeral runner each time, ~600MB, not in git), so
every scheduled build started from an empty target and dbt's incremental
logic degraded to "insert everything as new", silently discarding every
prior day. See docs/decisions.md, C6.4/C6.5.

`state/scorecard_history.csv` is now the single source of truth for the
scorecard trend. It is small (~103 rows/day), git-tracked alongside
watermark.json / staleness.json, restored on checkout, and read by
scripts/build_dashboard_source.py to build the dashboard's `dq_scorecard`
table.

Guarantees
----------
Idempotent: today's rows are keyed on (check_name, grain, run_date). A
re-run replaces today's rows in place; it never appends a second copy. A
CI job that runs this, then fails a later step and is re-run, leaves the
file byte-identical.

Crash-safe: the CSV is never mutated in place. The merged result is
written to a temp file in the same directory and os.replace()'d over the
target — an atomic rename on POSIX. A crash mid-write leaves either the
complete old file or the complete new file, never a truncated one. A
leftover temp file from an earlier crash is overwritten, not appended to.

Bounded: rows with run_date older than RETENTION_DAYS before today are
dropped. An unbounded append-only file on a public repo reads as
unconsidered; a bounded trailing window reads as designed. 180 days
exceeds every detector's own lookback and covers a quarter-plus of trend.
"""

import argparse
import os
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
PROD_DB = ROOT / "dbt" / "target" / "openledger_prod.duckdb"
HISTORY_CSV = ROOT / "state" / "scorecard_history.csv"
RETENTION_DAYS = 180

# The scorecard's stable column contract. Both the warehouse snapshot and
# the on-disk CSV are coerced to exactly this shape before they are merged,
# so cross-run CSV type-inference drift (e.g. an all-NULL acknowledged_date
# inferred as VARCHAR one day and DATE the next) can never break the union.
COLUMNS = [
    ("check_name", "VARCHAR"),
    ("category", "VARCHAR"),
    ("grain", "VARCHAR"),
    ("measured_value", "DOUBLE"),
    ("threshold_description", "VARCHAR"),
    ("status", "VARCHAR"),
    ("acknowledged_date", "DATE"),
    ("run_date", "DATE"),
    ("run_timestamp", "TIMESTAMP"),
]


def _projection(prefix: str = "") -> str:
    """SELECT list that coerces a source relation (already all-VARCHAR) to
    the COLUMNS contract, tolerating empty strings for the nullable
    numeric/date columns."""
    parts = []
    for name, typ in COLUMNS:
        col = f"{prefix}{name}"
        if typ in ("DOUBLE", "DATE", "TIMESTAMP"):
            parts.append(f"try_cast(nullif(cast({col} as varchar), '') as {typ}) as {name}")
        else:
            parts.append(f"cast({col} as {typ}) as {name}")
    return ",\n        ".join(parts)


def merge_and_write(history_csv: Path = HISTORY_CSV, prod_db: Path = PROD_DB) -> dict:
    """Merge today's snapshot into the history CSV. Returns a summary dict.
    Does an atomic replace; writes nothing on the target path until the
    merged file is fully materialised."""
    if not prod_db.exists():
        raise FileNotFoundError(
            f"{prod_db} not found — this script runs immediately after `dbt build`, "
            "against the marts it produced."
        )

    con = duckdb.connect()
    con.execute(f"attach '{prod_db}' as prod (read_only)")

    if con.execute(
        "select count(*) from duckdb_tables() where database_name='prod' "
        "and schema_name='main' and table_name='fct_data_quality_checks'"
    ).fetchone()[0] == 0:
        raise RuntimeError(
            "prod.main.fct_data_quality_checks does not exist — did `dbt build` run and succeed?"
        )

    con.execute(
        f"""
        create table today as
        select
            {_projection('')}
        from prod.main.fct_data_quality_checks
        """
    )
    row = con.execute("select count(*), max(run_date) from today").fetchone()
    today_rows, today_run_date = row
    if today_rows == 0:
        raise RuntimeError("today's scorecard snapshot is empty — refusing to touch the history file.")
    if con.execute("select count(distinct run_date) from today").fetchone()[0] != 1:
        raise RuntimeError("today's snapshot spans multiple run_dates — the model contract is one snapshot per build.")

    if history_csv.exists():
        con.execute(
            f"""
            create table hist as
            select
                {_projection('')}
            from read_csv_auto('{history_csv}', header=true, all_varchar=true)
            """
        )
    else:
        con.execute("create table hist as select * from today limit 0")

    hist_rows_before = con.execute("select count(*) from hist").fetchone()[0]

    con.execute(
        f"""
        create table merged as
        with combined as (
            select * from hist
            where (check_name, grain, run_date) not in (
                select check_name, grain, run_date from today
            )
            union all by name
            select * from today
        )
        select * from combined
        where run_date >= date '{today_run_date}' - interval {RETENTION_DAYS} day
        order by run_date, category, check_name, grain
        """
    )
    summary = con.execute(
        "select count(*), count(distinct run_date), min(run_date), max(run_date) from merged"
    ).fetchone()
    merged_rows, distinct_days, min_day, max_day = summary
    replaced_today = con.execute(
        f"""
        select count(*) from hist
        where run_date = date '{today_run_date}'
        """
    ).fetchone()[0]
    dropped_by_retention = con.execute(
        f"""
        select count(*) from hist
        where run_date < date '{today_run_date}' - interval {RETENTION_DAYS} day
        """
    ).fetchone()[0]

    history_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp = history_csv.with_suffix(history_csv.suffix + ".tmp")
    # Overwrite (not append to) any stale tmp left by an earlier crash.
    con.execute(f"copy merged to '{tmp}' (header, format csv)")
    os.replace(tmp, history_csv)  # atomic on POSIX

    con.close()
    return {
        "today_run_date": str(today_run_date),
        "today_rows": today_rows,
        "history_rows_before": hist_rows_before,
        "today_rows_replaced_in_history": replaced_today,
        "dropped_by_retention": dropped_by_retention,
        "merged_rows": merged_rows,
        "distinct_run_dates": distinct_days,
        "run_date_span": f"{min_day} .. {max_day}",
        "path": str(history_csv),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-csv", type=Path, default=HISTORY_CSV)
    parser.add_argument("--prod-db", type=Path, default=PROD_DB)
    args = parser.parse_args()

    result = merge_and_write(args.history_csv, args.prod_db)
    width = max(len(k) for k in result)
    for k, v in result.items():
        print(f"  {k.ljust(width)} : {v}")
    print("scorecard history updated (atomic replace).")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — CI needs a nonzero exit + a readable reason
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
