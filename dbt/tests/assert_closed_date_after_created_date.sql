{{ config(severity='warn', warn_if='>0', error_if='>3500') }}

-- STOP GATE 2 criterion 9: this test is DESIGNED to fail at a small rate,
-- not to pass at 100%. A known, real source defect (see docs/decisions.md,
-- Phase 1 C1.x) has a small share of rows with closed_date < created_date —
-- measured at ~0.0242% of rows with closed_date present in the full prod
-- build (1,763 of 7,289,995). warn_if '>0' means this shows as WARN on
-- every run, by design, so the defect stays visible rather than silently
-- passing. error_if '>3500' (roughly double the measured rate) is the real
-- assertion: it only turns into a hard failure if the defect rate grows,
-- which would indicate a new, undiagnosed problem rather than the known one.
-- Evaluated against the dev row-limited slice, the same defect is present at
-- the same rate but a much smaller absolute count, so it will never approach
-- this threshold there either.

select unique_key, created_date, closed_date
from {{ ref('fct_service_requests') }}
where closed_date is not null
  and closed_date < created_date
