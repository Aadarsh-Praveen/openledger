"""
C5.1 — build the pruned DuckDB source Evidence.dev's static build reads.

The full prod warehouse (dbt/target/openledger_prod.duckdb) is 596MB and
not in git; Evidence needs something small enough to commit (or fetch at
build time) and self-contained. This script reads the full marts
(read-only) and writes a small DuckDB file containing ONLY the
pre-aggregated tables the five dashboard pages actually query — never raw
fact rows. Run this, then re-run `npm run build` in dashboard/, any time
the marts change (the "refresh story," C5.8).

Every aggregate here uses the EXACT SAME filter logic as the corresponding
MetricFlow metric (Phase 4) — not a re-derivation. Where a table's numbers
should equal an `mf query` result, that reconciliation is checked directly
in docs/decisions.md, C5.1/C5.6, not assumed from matching SQL by eye.

Output: dashboard/sources/openledger/openledger.duckdb
"""

import duckdb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROD_DB = ROOT / "dbt" / "target" / "openledger_prod.duckdb"
OUT_DIR = ROOT / "dashboard" / "sources" / "openledger"
OUT_DB = OUT_DIR / "openledger.duckdb"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if OUT_DB.exists():
        OUT_DB.unlink()

    src = duckdb.connect(str(PROD_DB), read_only=True)
    out = duckdb.connect(str(OUT_DB))

    # Pull each source table into the destination connection as an Arrow
    # relation (no raw fact rows land in `out` at any point beyond what
    # each query below already aggregates down to).
    def pull(name: str, query: str) -> None:
        arrow_table = src.sql(query).arrow()
        out.sql(f"create table {name} as select * from arrow_table")

    # ---------------------------------------------------------------
    # Page 1 — Overview
    # ---------------------------------------------------------------
    pull(
        "overview",
        """
        select
            count(*) as request_count,
            count(distinct agency_key) as agency_count,
            strftime(min(created_date), '%Y-%m-%d') as date_min,
            strftime(max(created_date), '%Y-%m-%d') as date_max,
            round(100.0 * sum(case when is_closed then 1 else 0 end) / count(*), 4) as closure_rate_pct,
            round(100.0 * sum(case when is_settled then 1 else 0 end) / count(*), 4) as settlement_rate_pct,
            median(case when is_settled and is_closed and not is_undated_closure then resolution_hours else null end) as median_resolution_hours,
            quantile_cont(case when is_settled and is_closed and not is_undated_closure then resolution_hours else null end, 0.9) as p90_resolution_hours
        from main.fct_service_requests
        """,
    )

    # ---------------------------------------------------------------
    # Page 2 — Agency performance (median/p90 resolution + 3 closure-rate
    # flavors, matching Phase 4's metrics exactly)
    # ---------------------------------------------------------------
    pull(
        "agency_performance",
        """
        select
            a.agency,
            a.agency_name,
            count(*) as request_count,
            median(case when f.is_settled and f.is_closed and not f.is_undated_closure then f.resolution_hours else null end) as median_resolution_hours,
            quantile_cont(case when f.is_settled and f.is_closed and not f.is_undated_closure then f.resolution_hours else null end, 0.9) as p90_resolution_hours,
            round(100.0 * sum(case when f.is_closed then 1 else 0 end) / count(*), 4) as closure_rate_pct,
            round(100.0 * sum(case when f.is_settled then 1 else 0 end) / count(*), 4) as settlement_rate_pct,
            round(100.0 * sum(case when f.is_closed and f.is_settled then 1 else 0 end)
                / nullif(sum(case when f.is_settled then 1 else 0 end), 0), 4) as naive_closure_rate_pct,
            round(100.0 * sum(case when f.is_closed and f.is_settled then 1 else 0 end)
                / nullif(sum(case when f.is_settled and not f.is_undated_closure then 1 else 0 end), 0), 4) as closure_rate_excl_backlog_pct
        from main.fct_service_requests f
        join main.dim_agency a on f.agency_key = a.agency_key
        group by 1, 2
        order by request_count desc
        """,
    )

    # ---------------------------------------------------------------
    # Page 3 — Geographic equity: top 10 complaint types x borough,
    # resolution time. Missing-coordinate rate reported separately (it's
    # a property of the whole dataset, not of a type/borough cell) so the
    # page can state the exclusion once, honestly, rather than per-row.
    # ---------------------------------------------------------------
    pull(
        "geo_equity",
        """
        with top_types as (
            select ct.complaint_type
            from main.fct_service_requests f
            join main.dim_complaint_type ct on f.complaint_type_key = ct.complaint_type_key
            group by 1 order by count(*) desc limit 10
        )
        select
            ct.complaint_type,
            f.borough,
            count(*) as request_count,
            median(case when f.is_settled and f.is_closed and not f.is_undated_closure then f.resolution_hours else null end) as median_resolution_hours
        from main.fct_service_requests f
        join main.dim_complaint_type ct on f.complaint_type_key = ct.complaint_type_key
        where ct.complaint_type in (select complaint_type from top_types)
          and f.borough is not null and f.borough != ''
          and f.has_valid_coordinates
        group by 1, 2
        """,
    )
    pull(
        "geo_equity_missing_coords",
        """
        select round(100.0 * sum(case when not has_valid_coordinates then 1 else 0 end) / count(*), 2) as missing_coord_pct
        from main.fct_service_requests
        """,
    )

    # ---------------------------------------------------------------
    # Page 4 — Seasonality by composition: top 10 complaint types, share
    # of monthly volume, plus total monthly volume for the "volume is
    # flat" framing line.
    #
    # Excludes the calendar month containing MIN(created_date) and the one
    # containing MAX(created_date) — found while reconciling this page's
    # headline (C5.6): the true min/max monthly totals across the raw data
    # are 119,270 (Aug 2024, the backfill's own partial start month) and
    # 348,511 (Jan 2026, a real complete month) vs. a *misleadingly*
    # flat-looking 255K-348K range once the two partial edge months
    # (Aug 2024 backfill-start, Aug 2026 stale-bronze-end) are excluded.
    # Plotting the partial months would visually manufacture a "volume
    # crashes" story at both ends of the chart that isn't real — the
    # opposite of this page's honest "volume barely moves" claim.
    # ---------------------------------------------------------------
    pull(
        "seasonality",
        """
        with bounds as (
            select
                date_trunc('month', min(created_date)) as first_month,
                date_trunc('month', max(created_date)) as last_month
            from main.fct_service_requests
        ),
        top_types as (
            select ct.complaint_type
            from main.fct_service_requests f
            join main.dim_complaint_type ct on f.complaint_type_key = ct.complaint_type_key
            group by 1 order by count(*) desc limit 10
        ),
        monthly as (
            select
                date_trunc('month', f.created_date) as month,
                ct.complaint_type,
                count(*) as n
            from main.fct_service_requests f
            join main.dim_complaint_type ct on f.complaint_type_key = ct.complaint_type_key
            where ct.complaint_type in (select complaint_type from top_types)
            group by 1, 2
        ),
        month_totals as (
            select date_trunc('month', created_date) as month, count(*) as total
            from main.fct_service_requests
            group by 1
        )
        select
            m.month::date as month,
            m.complaint_type,
            m.n as request_count,
            round(m.n * 100.0 / t.total, 4) as pct_share_of_month,
            t.total as month_total_requests
        from monthly m
        join month_totals t on m.month = t.month
        cross join bounds b
        where m.month > b.first_month and m.month < b.last_month
        order by m.month, m.complaint_type
        """,
    )

    # ---------------------------------------------------------------
    # Page 5 — Data quality: reuse the existing quality marts/detectors
    # directly (already pre-aggregated, already the audited source of
    # truth from Phase 3) rather than re-deriving anything.
    # ---------------------------------------------------------------
    # C6.5: fct_data_quality_checks is now a single-snapshot table (the
    # marts DuckDB isn't persisted across CI runs, so it can't hold
    # history). The scorecard trend lives in state/scorecard_history.csv,
    # git-tracked and appended once per build. Read the full history from
    # there when it exists; fall back to the single current snapshot for a
    # fresh local checkout that hasn't run the append script yet.
    scorecard_csv = ROOT / "state" / "scorecard_history.csv"
    if scorecard_csv.exists():
        pull(
            "dq_scorecard",
            f"select * from read_csv_auto('{scorecard_csv.as_posix()}', header=true)",
        )
    else:
        print(f"note: {scorecard_csv} not found — dq_scorecard falling back to the current snapshot only")
        pull("dq_scorecard", "select * from main.fct_data_quality_checks")
    pull("dq_settlement_completeness", "select * from main.dq_detector_settlement_completeness")
    pull("dq_vocabulary_drift", "select * from main.dq_detector_vocabulary_drift")
    pull("dq_mass_touch", "select * from main.dq_detector_mass_touch where fired")
    pull(
        "dq_resolution_hours_naive_vs_correct",
        """
        select
            median(case when is_settled and is_closed and not is_undated_closure then resolution_hours else null end) as correct_median_resolution_hours,
            median(date_diff('hour', created_date, closed_date)) as naive_median_resolution_hours
        from main.fct_service_requests
        """,
    )

    src.close()
    out.close()

    size_mb = OUT_DB.stat().st_size / (1024 * 1024)
    print(f"Wrote {OUT_DB} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
