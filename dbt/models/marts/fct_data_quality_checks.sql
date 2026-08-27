{#
  C3.6 — the DQ scorecard mart. Grain: one row per (check_name, grain,
  run_date). Emits ONLY the current run's snapshot.

  C6.5 (was incremental until Phase 6): this was `materialized='incremental'`
  so history would accumulate in the warehouse. But dbt/target/
  openledger_prod.duckdb is not persisted between CI runs (fresh ephemeral
  runner each time, ~600MB, not in git), so every scheduled build started
  from an empty target and the incremental logic degraded to "insert
  everything as new" — silently discarding every prior day. History now
  lives in state/scorecard_history.csv (git-tracked, restored on checkout),
  appended by scripts/append_scorecard_history.py after each build and read
  by the dashboard for the trend. This model is the single-snapshot input
  to that append. Keeping it incremental here would be hidden state of
  exactly the kind (partial_parse.msgpack, is_settled) this project has
  been bitten by before. See docs/decisions.md, C6.4/C6.5.

  Four categories, per phase-3.md:
    - contract: build-gated. If a contract were violated, `dbt build`
      would have failed before this model runs at all — so every row here
      by construction reports status='pass'. Presence of the row IS the
      proof, run after run.
    - unit: same build-gated logic — a failing unit test halts the build
      before this model builds.
    - distributional: LIVE recomputation of the same 6 checks as
      quality/soda/checks/distributional_checks.yml, in SQL, so the
      scorecard doesn't depend on the separate Soda venv/process to know
      today's state (Soda remains the primary check; this is an
      independent, cheap cross-check with its own history).
    - detector: current-state rows pulled from the five
      dq_detector_*.sql models (each of which computes the FULL 24-month
      backtest every run, not just today — see those models for the
      reproducible detail behind every number in docs/decisions.md's
      H3.2/H3.2b entries).

  Acknowledgment handling (H3.2, review note 1): a detector firing that is
  a KNOWN, dated, accepted condition (DHS's persistent undated-closure
  rate — it fires every single scan by design, see
  dq_detector_undated_closure_rate.sql) gets status='acknowledged', not
  'fail' — a permanently-red check becomes wallpaper people learn to
  ignore. The acknowledgment itself is recorded and dated in
  seeds/quality_acknowledgments.csv, not silently hidden: the true
  measured value is still reported, just recontextualized.
#}

{{
  config(
    materialized='table'
  )
}}

with run_meta as (

    select
        timezone('{{ var("created_date_timezone") }}', current_timestamp)::date as run_date,
        current_timestamp as run_timestamp

),

-- ============================================================
-- CONTRACT (C3.2/C3.7) — build-gated, 8 models / 103 columns total
-- (counts verified directly against the schema YAMLs, not recalled —
-- 103, not the pre-C3.7 101, since is_undated_closure added one column
-- each to int_request_resolution and fct_service_requests).
-- ============================================================
contracts as (

    select * from (values
        ('contract_enforced', 'stg_service_requests', 49.0, '49 columns, all typed'),
        ('contract_enforced', 'int_request_resolution', 8.0, '8 columns, all typed'),
        ('contract_enforced', 'int_request_geography', 7.0, '7 columns, all typed'),
        ('contract_enforced', 'dim_agency', 3.0, '3 columns, all typed'),
        ('contract_enforced', 'dim_complaint_type', 3.0, '3 columns, all typed'),
        ('contract_enforced', 'dim_location', 3.0, '3 columns, all typed'),
        ('contract_enforced', 'dim_date', 11.0, '11 columns, all typed'),
        ('contract_enforced', 'fct_service_requests', 19.0, '19 columns, all typed')
    ) as t(check_name, grain, measured_value, threshold_description)

),

-- ============================================================
-- UNIT (C3.3) — build-gated, 7 unit tests, all on int_request_resolution.
-- ============================================================
unit_tests as (

    select * from (values
        ('unit_test_passed', 'test_resolution_hours_normal_close'),
        ('unit_test_passed', 'test_resolution_hours_closed_before_created_defect'),
        ('unit_test_passed', 'test_is_censored_for_open_request'),
        ('unit_test_passed', 'test_closed_but_not_yet_settled_yields_null_resolution'),
        ('unit_test_passed', 'test_is_settled_at_exactly_45_day_boundary'),
        ('unit_test_passed', 'test_is_settled_one_day_short_of_boundary'),
        ('unit_test_passed', 'test_is_settled_one_day_past_boundary')
    ) as t(check_name, grain)

),

-- ============================================================
-- DISTRIBUTIONAL (C3.4) — live recomputation of the 6 Soda checks.
-- Thresholds match quality/soda/checks/distributional_checks.yml exactly.
-- ============================================================
dist_base as (

    select f.*, ct.complaint_type, a.agency
    from {{ ref('fct_service_requests') }} f
    join {{ ref('dim_complaint_type') }} ct on f.complaint_type_key = ct.complaint_type_key
    join {{ ref('dim_agency') }} a on f.agency_key = a.agency_key

),

dist_volume as (

    select
        'row_count_volume' as check_name,
        'recent_complete_day' as grain,
        count(*)::double as measured_value,
        '[4000, 28000]' as threshold_description,
        case when count(*) between 4000 and 28000 then 'pass' else 'fail' end as status
    from dist_base, run_meta
    where created_date::date = run_meta.run_date - interval '2' day

),

dist_missing_coords as (

    select
        'missing_coordinate_rate' as check_name,
        'trailing_30_days' as grain,
        round(100.0 * sum(case when not has_valid_coordinates then 1 else 0 end) / count(*), 2) as measured_value,
        '[0.5%, 7.0%]' as threshold_description,
        case
            when round(100.0 * sum(case when not has_valid_coordinates then 1 else 0 end) / count(*), 2) between 0.5 and 7.0
            then 'pass' else 'fail'
        end as status
    from dist_base, run_meta
    where created_date >= run_meta.run_date - interval '30' day

),

dist_resolution_cohort as (

    select
        resolution_hours,
        'resolution_hours_p50_p90' as check_name
    from dist_base, run_meta
    where is_settled and is_closed and resolution_hours is not null
      and created_date >= run_meta.run_date - interval '75' day
      and created_date < run_meta.run_date - interval '45' day

),

dist_resolution as (

    select
        'resolution_hours_p50_p90' as check_name,
        'recently_settled_cohort' as grain,
        round(median(resolution_hours), 1) as measured_value,
        'median in [2,35], p90 in [250,600]' as threshold_description,
        case
            when round(median(resolution_hours), 1) between 2 and 35
             and round(quantile_cont(resolution_hours, 0.9), 1) between 250 and 600
            then 'pass' else 'fail'
        end as status
    from dist_resolution_cohort

),

dist_closure_rate as (

    select
        'closure_rate_at_settlement' as check_name,
        'settlement_boundary_cohort' as grain,
        round(100.0 * sum(case when is_closed then 1 else 0 end) / count(*), 2) as measured_value,
        '[90%, 99.9%]' as threshold_description,
        case
            when round(100.0 * sum(case when is_closed then 1 else 0 end) / count(*), 2) between 90 and 99.9
            then 'pass' else 'fail'
        end as status
    from dist_base, run_meta
    where is_settled
      and created_date >= run_meta.run_date - interval '75' day
      and created_date < run_meta.run_date - interval '45' day

),

dist_type_shift as (

    select
        max(abs(mom_delta)) as max_abs_delta
    from (
        select
            complaint_type, month,
            pct_share - lag(pct_share) over (partition by complaint_type order by month) as mom_delta
        from (
            select
                date_trunc('month', created_date) as month,
                complaint_type,
                count(*) * 100.0 / sum(count(*)) over (partition by date_trunc('month', created_date)) as pct_share
            from dist_base
            where complaint_type in (
                select complaint_type from dist_base group by 1 order by count(*) desc limit 10
            )
            group by 1, 2
        )
    ), run_meta
    where month >= date_trunc('month', run_meta.run_date) - interval '2' month

),

dist_composition_type as (

    select
        'complaint_type_mom_share_shift' as check_name,
        'top_10_types_trailing_2_months' as grain,
        coalesce(max_abs_delta, 0) as measured_value,
        'fail if any |delta| > 15pp' as threshold_description,
        case when coalesce(max_abs_delta, 0) > 15 then 'fail' else 'pass' end as status
    from dist_type_shift

),

dist_agency_shift as (

    select
        max(abs(mom_delta)) as max_abs_delta
    from (
        select
            agency, month,
            pct_share - lag(pct_share) over (partition by agency order by month) as mom_delta
        from (
            select
                date_trunc('month', created_date) as month,
                agency,
                count(*) * 100.0 / sum(count(*)) over (partition by date_trunc('month', created_date)) as pct_share
            from dist_base
            group by 1, 2
        )
    ), run_meta
    where month >= date_trunc('month', run_meta.run_date) - interval '2' month

),

dist_composition_agency as (

    select
        'agency_mom_share_shift' as check_name,
        'all_agencies_trailing_2_months' as grain,
        coalesce(max_abs_delta, 0) as measured_value,
        'fail if any |delta| > 15pp' as threshold_description,
        case when coalesce(max_abs_delta, 0) > 15 then 'fail' else 'pass' end as status
    from dist_agency_shift

),

distributional as (

    select check_name, grain, measured_value, threshold_description, status from dist_volume
    union all
    select check_name, grain, measured_value, threshold_description, status from dist_missing_coords
    union all
    select check_name, grain, measured_value, threshold_description, status from dist_resolution
    union all
    select check_name, grain, measured_value, threshold_description, status from dist_closure_rate
    union all
    select check_name, grain, measured_value, threshold_description, status from dist_composition_type
    union all
    select check_name, grain, measured_value, threshold_description, status from dist_composition_agency

),

-- ============================================================
-- DETECTOR (C3.5) — current-state rows from the five dq_detector_* models.
-- ============================================================

-- (a) redesigned: latest scan_month, one row per agency.
det_a_latest_scan as (
    select max(scan_month) as latest_scan_month from {{ ref('dq_detector_undated_closure_rate') }}
),
det_a as (
    select
        'undated_closure_rate_anomaly' as check_name,
        d.agency as grain,
        d.undated_closure_rate_pct as measured_value,
        'fired if rate > ' || d.threshold_pct || '%' as threshold_description,
        case when d.fired then 'fail' else 'pass' end as status
    from {{ ref('dq_detector_undated_closure_rate') }} d
    join det_a_latest_scan s on d.scan_month = s.latest_scan_month
),

-- (b): latest evaluated month, one row per type with a valid YoY comparison that month.
det_b_latest_month as (
    select max(month) as latest_month from {{ ref('dq_detector_composition_drift') }}
),
det_b as (
    select
        'composition_drift_yoy' as check_name,
        d.complaint_type as grain,
        d.yoy_delta_pp as measured_value,
        'fired if |YoY delta| > 5pp' as threshold_description,
        case when d.fired then 'fail' else 'pass' end as status
    from {{ ref('dq_detector_composition_drift') }} d
    join det_b_latest_month m on d.month = m.latest_month
),

-- (c): notification-only — entries first seen in the trailing 90 days.
det_c as (
    select
        'vocabulary_drift_new_member' as check_name,
        d.dimension || ':' || d.value as grain,
        d.volume_to_date::double as measured_value,
        'notification only, not pass/fail' as threshold_description,
        'info' as status
    from {{ ref('dq_detector_vocabulary_drift') }} d, run_meta
    where d.first_seen_month >= run_meta.run_date - interval '90' day
),

-- (d): the single most recently eligible cohort.
det_d_latest_cohort as (
    select max(cohort_month) as latest_cohort from {{ ref('dq_detector_settlement_completeness') }}
),
det_d as (
    select
        'settlement_completeness_45d' as check_name,
        d.cohort_month::varchar as grain,
        d.completeness_pct_45d as measured_value,
        'warn <90%, fail <85%' as threshold_description,
        d.status as status
    from {{ ref('dq_detector_settlement_completeness') }} d
    join det_d_latest_cohort c on d.cohort_month = c.latest_cohort
),

-- (e): rollup — firings in the trailing 30 days (usually 0; informational
-- otherwise). Full per-event detail stays queryable in the detector model.
det_e as (
    select
        'mass_metadata_touch_trailing_30d' as check_name,
        'dataset' as grain,
        count(*) filter (where d.fired)::double as measured_value,
        'informational: count of mass-touch nights in trailing 30 days' as threshold_description,
        case when count(*) filter (where d.fired) > 0 then 'info' else 'pass' end as status
    from {{ ref('dq_detector_mass_touch') }} d, run_meta
    where d.hour_bucket >= run_meta.run_date - interval '30' day

),

detectors_raw as (

    select check_name, grain, measured_value, threshold_description, status from det_a
    union all
    select check_name, grain, measured_value, threshold_description, status from det_b
    union all
    select check_name, grain, measured_value, threshold_description, status from det_c
    union all
    select check_name, grain, measured_value, threshold_description, status from det_d
    union all
    select check_name, grain, measured_value, threshold_description, status from det_e

),

detectors as (

    select
        r.check_name,
        r.grain,
        r.measured_value,
        r.threshold_description,
        case when r.status = 'fail' and ack.grain is not null then 'acknowledged' else r.status end as status,
        ack.acknowledged_date
    from detectors_raw r
    left join {{ ref('quality_acknowledgments') }} ack
        on r.check_name = ack.check_name and r.grain = ack.grain

),

-- ============================================================
-- ASSEMBLE
-- ============================================================
assembled as (

    select check_name, 'contract' as category, grain, measured_value, threshold_description, 'pass' as status, cast(null as date) as acknowledged_date
    from contracts

    union all

    select check_name, 'unit' as category, grain, cast(null as double) as measured_value, cast(null as varchar) as threshold_description, 'pass' as status, cast(null as date) as acknowledged_date
    from unit_tests

    union all

    select check_name, 'distributional' as category, grain, measured_value, threshold_description, status, cast(null as date) as acknowledged_date
    from distributional

    union all

    select check_name, 'detector' as category, grain, measured_value, threshold_description, status, acknowledged_date
    from detectors

)

select
    a.check_name,
    a.category,
    a.grain,
    a.measured_value,
    a.threshold_description,
    a.status,
    a.acknowledged_date,
    r.run_date,
    r.run_timestamp
from assembled a
cross join run_meta r
