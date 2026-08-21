{#
  Grain: one row per (complaint_type, descriptor). A real two-level hierarchy
  measured directly: 202 distinct complaint_type values, 1,278 distinct
  (complaint_type, descriptor) pairs (~6.3 descriptors/type on average).
  descriptor is null 0.33% of the time — coalesced only for the surrogate
  key's grouping, never overwritten in the displayed attribute, so a genuine
  null descriptor stays visibly null rather than becoming the string
  'UNSPECIFIED' on the dashboard.
#}

with source as (

    select distinct
        complaint_type,
        descriptor
    from {{ ref('stg_service_requests') }}
    where complaint_type is not null

)

select
    row_number() over (
        order by complaint_type, coalesce(descriptor, '')
    ) as complaint_type_key,
    complaint_type,
    descriptor
from source
