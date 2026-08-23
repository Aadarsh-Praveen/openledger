{#
  Grain: ONE ROW PER unique_key. The single most important piece of
  documentation in this project (phase-2.md's own words) — every measure
  and every join below assumes it, and C2.9's `unique` test on unique_key is
  the load-bearing proof that it actually holds (guaranteed by bronze's
  upsert, per Phase 1, but verified here independently, not assumed).

  No pre-aggregation — the Phase 4 semantic layer (MetricFlow) does that
  over this grain, not here.
#}

with staged as (

    select * from {{ ref('stg_service_requests') }}

),

resolution as (

    select * from {{ ref('int_request_resolution') }}

),

geography as (

    select * from {{ ref('int_request_geography') }}

),

agency_dim as (

    select * from {{ ref('dim_agency') }}

),

complaint_type_dim as (

    select * from {{ ref('dim_complaint_type') }}

),

location_dim as (

    select * from {{ ref('dim_location') }}

)

select
    staged.unique_key,

    -- Foreign keys to all four dimensions.
    agency_dim.agency_key,
    complaint_type_dim.complaint_type_key,
    location_dim.location_key,
    cast(staged.created_date as date) as date_key,

    -- Timestamps, kept at the fact grain (not dimension-worthy per phase-2.md:
    -- lat/long-level and timestamp-level detail is too granular for a dim).
    staged.created_date,
    staged.closed_date,
    staged.updated_at,

    -- Measures.
    resolution.resolution_hours,
    resolution.is_closed,
    resolution.is_censored,
    resolution.is_settled,
    resolution.is_undated_closure,
    geography.has_valid_coordinates,

    -- Kept at fact grain for anything a dimension join can't answer directly
    -- (e.g. exact coordinates for a map, exact ZIP for ad hoc drill-down).
    geography.latitude,
    geography.longitude,
    geography.incident_zip,
    staged.status,
    staged.borough

from staged
left join resolution on staged.unique_key = resolution.unique_key
left join geography on staged.unique_key = geography.unique_key
left join agency_dim on staged.agency = agency_dim.agency
left join complaint_type_dim
    on staged.complaint_type = complaint_type_dim.complaint_type
    and coalesce(staged.descriptor, '') = coalesce(complaint_type_dim.descriptor, '')
-- Joins on the FULL grain of dim_location (community_board, borough), not
-- community_board alone: a real data-quality finding (646 rows, mostly
-- community_board='08 BRONX' paired with borough='MANHATTAN') means the same
-- community_board string is not always paired with the same borough in the
-- source. Joining on community_board alone fanned the fact table out to
-- 1,024,548 rows for 982,790 distinct unique_keys — caught immediately by
-- the grain check below, not left in. Joining the full pair is correct, not
-- a workaround: it's what dim_location's own declared grain already is.
left join location_dim
    on staged.community_board = location_dim.community_board
    and staged.borough = location_dim.borough
