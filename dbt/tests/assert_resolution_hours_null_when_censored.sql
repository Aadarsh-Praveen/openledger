{{ config(severity='error') }}

-- Load-bearing (STOP GATE 2, criterion 5): resolution_hours must be null for
-- every censored row, with zero exceptions. Unlike the closed>=created test
-- below, there is no known legitimate defect rate here — any failing row is
-- a real bug in int_request_resolution's CASE logic.

select unique_key, is_censored, resolution_hours
from {{ ref('fct_service_requests') }}
where is_censored and resolution_hours is not null
