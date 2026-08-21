{#
  Grain: one row per agency. 16 members currently (vocabulary drift: 14 -> 16
  since Phase 1, per CLAUDE.md's carried-forward fact) — Type 1, surrogate-
  keyed, so new agencies are absorbed by a fresh row, not a schema change.
  agency -> agency_name verified 1:1 (no drift/inconsistency) across all 16.
#}

with source as (

    select distinct
        agency,
        agency_name
    from {{ ref('stg_service_requests') }}
    where agency is not null

)

select
    row_number() over (order by agency) as agency_key,
    agency,
    agency_name
from source
