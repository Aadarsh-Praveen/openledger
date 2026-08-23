{#
  Where the hard thinking goes (C2.5). Computes resolution_hours, is_closed,
  is_censored, is_settled. A row that is open, or closed but unsettled, is
  distinguishable from one that is genuinely resolved — resolution_hours is
  null for both, but is_closed/is_settled tell you which.

  H2.2 approved the observation-cutoff *concept* at 30 days; the actual
  settlement curve was then measured directly (not inferred from C1.1's
  closed_date-to-:updated_at lag, a different quantity) and revised to 45
  days — see docs/decisions.md and docs/findings.md. is_settled keys on
  created_date, not closed_date: keying on closed_date would exclude exactly
  the slow-resolving/still-open requests the cutoff exists to account for
  (an open row has no closed_date to key on at all; a just-closed row would
  pass a closed_date-based recency check instantly, inverting the correction).
#}

with staged as (

    select * from {{ ref('stg_service_requests') }}

),

resolution as (

    select
        unique_key,
        created_date,
        closed_date,
        status,

        -- is_closed: status='Closed' AND a closed_date exists. The known
        -- ~0.85%-in-2024 (declining to ~0.03% by 2026, per C1.9) "Closed with
        -- null closed_date" defect means status alone is not sufficient —
        -- C2.8 investigates whether that pattern is bulk administrative
        -- closure; this model just declines to call it "closed" without a
        -- closed_date to compute a resolution time from.
        (status = 'Closed' and closed_date is not null) as is_closed,

        -- is_undated_closure (C3.7): status='Closed' but closed_date is
        -- null — a request administratively closed without a resolution
        -- date. Currently indistinguishable from a genuinely open request
        -- by is_closed/is_censored alone (both are false/true the same way
        -- for either case) — this flag exists so a consumer CAN
        -- distinguish them. Found via C3.5's detector (a) redesign to be
        -- 99.9% concentrated in one agency (DHS), a frozen historical
        -- backlog (17,356 rows, created 2024-08-19 through 2025-05-06, no
        -- growth since) rather than an ongoing condition — see
        -- docs/decisions.md, C3.7, for the quantified effect on DHS's
        -- apparent SLA/closure rate if this flag is not accounted for.
        (status = 'Closed' and closed_date is null) as is_undated_closure,

        -- is_settled: created_date old enough that even a currently-open
        -- reading is trustworthy (per the measured completeness curve, not
        -- the C1.1 lag distribution — those are different quantities).
        -- Keys on created_date, never closed_date — see module docstring.
        --
        -- Deliberately NOT bare `current_date`: that resolves against
        -- DuckDB's *session* TimeZone setting, not this project's single
        -- documented timezone assumption (created_date_timezone). Found in
        -- C3.3: those two happen to coincide on this dev machine (session
        -- TimeZone defaults to America/New_York here), which is exactly why
        -- it went unnoticed — but a session running as UTC (the likely
        -- default for Phase 6's CI runner) would resolve `current_date` to
        -- the next calendar day for roughly 7-8 hours every evening Eastern
        -- time, silently shifting the settled/censored boundary for any row
        -- exactly 45 days old with no error anywhere. Anchoring explicitly
        -- to created_date_timezone removes the dependency on session state.
        (
            created_date
            <= timezone('{{ var("created_date_timezone") }}', current_timestamp)::date
                - interval '{{ var("observation_cutoff_days") }} days'
        ) as is_settled

    from staged

),

flagged as (

    select
        *,

        -- is_censored: true whenever the resolution outcome can't be fully
        -- trusted as final — either genuinely still open, or closed but too
        -- recent to trust that closure is stable. Two different reasons,
        -- same downstream treatment (resolution_hours must be null).
        (not is_closed or not is_settled) as is_censored

    from resolution

)

select
    unique_key,
    created_date,
    closed_date,
    is_closed,
    is_settled,
    is_censored,
    is_undated_closure,

    -- Do not compute a resolution time for censored rows. Null is correct —
    -- a zero or imputed value would silently corrupt every downstream
    -- aggregate (phase-2.md's explicit warning). Computed even for the rare
    -- (~0.02-0.03%, see C1.9/C0.4) closed-before-created defect rows when
    -- they ARE closed+settled — suppressing that here would hide the exact
    -- finding the C2.9 closed>=created test is designed to surface.
    case
        when is_censored then null
        else date_diff('hour', created_date, closed_date)
    end as resolution_hours

from flagged
