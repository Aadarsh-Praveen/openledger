{{ config(severity='error') }}

-- Row-count reconciliation, staging vs bronze (STOP GATE 2, criterion 3).
-- Only meaningful in prod: dev deliberately samples via dev_row_limit
-- (C2.2), so a mismatch there is expected, not a defect. This test is a
-- no-op in dev for that reason, not because the check doesn't apply.

{% if target.name == 'dev' %}

select 1 as staging_count, 1 as bronze_count where false

{% else %}

with staging_count as (
    select count(*) as n from {{ ref('stg_service_requests') }}
),

bronze_count as (
    select count(*) as n from {{ source('bronze', 'service_requests') }}
)

select staging_count.n as staging_count, bronze_count.n as bronze_count
from staging_count, bronze_count
where staging_count.n != bronze_count.n

{% endif %}
