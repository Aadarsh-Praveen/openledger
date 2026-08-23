{{ config(severity='error') }}

-- fct_data_quality_checks.sql hardcodes each contracted model's column
-- count as a literal (contracts are build-gated, so there's no live query
-- that naturally produces this number the way the other three categories
-- do). Hardcoded literals drift silently when a column is added or
-- removed and the literal isn't updated by hand — found happening
-- already once in this same file (C3.7 added is_undated_closure to two
-- models; the literals were originally written wrong from memory, not
-- re-derived — see docs/decisions.md, C3.6). This test closes that gap:
-- compares the scorecard's stored measured_value per model against
-- DuckDB's own information_schema.columns count for that table, live,
-- every build.

with scorecard as (
    select grain as model_name, measured_value as reported_columns
    from {{ ref('fct_data_quality_checks') }}
    where check_name = 'contract_enforced'
      and run_date = (select max(run_date) from {{ ref('fct_data_quality_checks') }})
),

actual as (
    select table_name as model_name, count(*) as actual_columns
    from information_schema.columns
    where table_schema = 'main'
      and table_name in (select model_name from scorecard)
    group by 1
)

select s.model_name, s.reported_columns, a.actual_columns
from scorecard s
join actual a on s.model_name = a.model_name
where s.reported_columns != a.actual_columns
