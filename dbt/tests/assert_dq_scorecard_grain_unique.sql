{{ config(severity='error') }}

-- fct_data_quality_checks' declared grain is one row per
-- (check_name, grain, run_date) — the incremental model's own unique_key.
-- No native `unique` test can check a 3-column combination without
-- dbt_utils (not a dependency of this project); asserted directly here.

select check_name, grain, run_date, count(*) as n
from {{ ref('fct_data_quality_checks') }}
group by 1, 2, 3
having count(*) > 1
