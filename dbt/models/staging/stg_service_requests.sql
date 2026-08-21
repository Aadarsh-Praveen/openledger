{#
  Staging: shape, not meaning. Casts/renames/normalizes; no business logic
  (censoring, resolution time, settlement) lives here — that's C2.5's
  intermediate layer.
#}

with source as (

    select * from {{ source('bronze', 'service_requests') }}

    {% if target.name == 'dev' %}
    -- Dev-time row limit (H2.1 addition 2): a small recent slice, never an
    -- edited model — switching --target is the only thing that changes this.
    -- Gated on target.name directly, not on dev_row_limit's own nullness —
    -- see dbt_project.yml for why a target-polymorphic var value doesn't
    -- work reliably in this dbt version (verified empirically, not assumed).
    where created_date >= (
        (select max(created_date) from {{ source('bronze', 'service_requests') }})
        - interval '{{ var("dev_row_limit") }} days'
    )
    {% endif %}

),

renamed as (

    select
        unique_key,

        -- Timestamps: created_date/closed_date/due_date/resolution_action_updated_date
        -- are naive in bronze, assumed Eastern local (inferential evidence — a
        -- call-volume-trough pattern, not a documented Socrata guarantee; see
        -- docs/decisions.md, Phase 1 C1.1b). Localizing here, once, via the
        -- project var is the single documented place this assumption lives
        -- (phase-2.md problem #3) — every downstream model inherits a real
        -- timestamptz, not a repeated assumption. timezone() is DST-aware
        -- (verified: EST in January, EDT in July), resolving Phase 1's
        -- "DST-awareness unverified" open question.
        timezone('{{ var("created_date_timezone") }}', created_date) as created_date,
        timezone('{{ var("created_date_timezone") }}', closed_date) as closed_date,
        timezone('{{ var("created_date_timezone") }}', due_date) as due_date,
        timezone('{{ var("created_date_timezone") }}', resolution_action_updated_date)
            as resolution_action_updated_date,
        -- updated_at is already UTC-aware (Socrata's :updated_at) — no conversion.
        updated_at,

        agency,
        agency_name,

        -- complaint_type/descriptor: real casing inconsistency measured directly
        -- (e.g. 'Plumbing' vs 'PLUMBING', 'Elevator' vs 'ELEVATOR' both present) —
        -- normalized to match the schema's otherwise-dominant uppercase
        -- convention (agency, borough, city are already all-uppercase).
        -- dim_complaint_type's grain depends on this: without normalization,
        -- case variants would incorrectly count as distinct dimension members.
        upper(trim(complaint_type)) as complaint_type,
        upper(trim(descriptor)) as descriptor,
        trim(descriptor_2) as descriptor_2,

        location_type,
        incident_zip,
        incident_address,
        street_name,
        cross_street_1,
        cross_street_2,
        intersection_street_1,
        intersection_street_2,
        address_type,

        -- city: real casing inconsistency measured directly (e.g. 'NY'/'ny'/'Ny',
        -- 'Ozone Park'/'OZONE PARK'/'ozone park') — normalized. Not part of the
        -- dimensional model (dim_location keys on borough + community_district,
        -- per phase-2.md), kept here for completeness/debugging only.
        upper(trim(city)) as city,

        landmark,
        facility_type,

        -- status: already consistent in source (Open/In Progress/Closed/etc,
        -- verified no case-insensitive variants) — trimmed only, casing preserved.
        trim(status) as status,

        resolution_description,
        community_board,
        council_district,
        police_precinct,
        bbl,

        -- borough: already consistent in source (verified) — trimmed only.
        trim(borough) as borough,

        x_coordinate_state_plane,
        y_coordinate_state_plane,
        open_data_channel_type,
        park_facility_name,
        park_borough,
        vehicle_type,
        taxi_company_borough,
        taxi_pick_up_location,
        bridge_highway_name,
        bridge_highway_direction,
        road_ramp,
        bridge_highway_segment,

        -- Coordinate source ambiguity resolved (C2.4): latitude/longitude and
        -- location_lat/location_lon agree 100.0000% of the time when both are
        -- present (7,403,755 of 7,403,755 checked, within 0.0001 degrees /
        -- ~11m), and are always both-present or both-null together (0 rows
        -- with only one pair populated) — measured directly, not assumed.
        -- latitude/longitude chosen as canonical: simpler names, no GeoJSON
        -- derivation step. location_lat/location_lon dropped as fully
        -- redundant, not carried past staging.
        latitude,
        longitude,

        -- Coordinate validity as a flag, not a filter (per phase-2.md: the
        -- out-of-bounds defect is 0% but missing coords reach ~2.5%, so the
        -- useful distinction is present-vs-absent, not valid-vs-invalid).
        (
            latitude is not null and longitude is not null
            and latitude between 40.4959 and 40.9153
            and longitude between -74.2557 and -73.7002
            and not (latitude = 0 and longitude = 0)
        ) as has_valid_coordinates,

        computed_region_community_districts,
        computed_region_borough_boundaries,
        computed_region_police_precincts,
        computed_region_city_council_districts

    from source

)

select * from renamed
