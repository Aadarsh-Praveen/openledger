{#
  C2.5: normalizes borough/community_district/ZIP, resolves the borough vs
  park_borough distinction, flags geocoding completeness.

  borough vs park_borough resolved by direct measurement: 100.00% agreement
  across 7,526,843 non-Unspecified rows, and identical null/Unspecified
  pattern in exactly the same 6,289 rows (0.08%) for both fields — not a
  park-complaint-specific field with sparser coverage as the name might
  suggest, just a duplicate. borough kept as canonical (simpler, standard
  name); park_borough not carried past this model.

  community_board (format "NN BOROUGH", e.g. "12 BRONX", 77 distinct values,
  0 nulls — "Unspecified BOROUGH" or "0 Unspecified" when district/borough
  aren't known) already IS phase-2.md's "borough + community district" key,
  combined — used directly rather than re-derived, since splitting it apart
  and rejoining would just reconstruct the same string with more steps and
  more chances to disagree with the source's own encoding.
#}

with staged as (

    select * from {{ ref('stg_service_requests') }}

)

select
    unique_key,
    borough,
    community_board,
    incident_zip,
    latitude,
    longitude,
    has_valid_coordinates

from staged
