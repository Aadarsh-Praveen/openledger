{#
  Grain: one row per distinct location key. Key deliberately chosen as
  community_board (format "NN BOROUGH", e.g. "12 BRONX") over the
  alternatives phase-2.md names: borough alone (5 values) is too coarse for
  geographic-equity analysis (the project's stated headline finding);
  incident_zip is real-world dirty (typos, out-of-range values, 0.88% null,
  no verified crosswalk to borough here); lat/long is too granular to be a
  dimension (near request-level cardinality). community_board already
  encodes borough + community district as one field (77 distinct values,
  0 nulls, graceful "Unspecified BOROUGH" / "0 Unspecified" fallbacks) —
  exactly phase-2.md's "borough + community district is stable" — so it's
  used directly rather than re-derived by splitting and rejoining, which
  would just reconstruct the same string with more chances to disagree
  with the source's own encoding.

  borough itself is carried as a coarser roll-up attribute (verified
  redundant with park_borough at 100.00% agreement — see
  int_request_geography — so only one is carried past that model).
#}

with source as (

    select distinct
        community_board,
        borough
    from {{ ref('int_request_geography') }}
    where community_board is not null

)

select
    row_number() over (order by borough, community_board) as location_key,
    community_board,
    borough
from source
