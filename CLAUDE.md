# OpenLedger — Project Context for Claude Code

## What this project is

An open-table-format analytics lakehouse over NYC 311 Service Requests, built to
demonstrate analytics-engineering depth: Apache Iceberg, incremental (micro-batch)
ingestion, dimensional modeling in dbt, layered data quality, a semantic layer, and a
free always-on public dashboard — with a cloud slice on S3 + Glue + Athena.

**Framing for a hiring audience:** "governed Iceberg lakehouse with incremental
ingestion and a semantic layer," not "a 311 dashboard." The analytical payoff is
agency SLA performance and geographic equity in civic service delivery.

## Non-negotiable framing: this is NOT streaming

NYC 311 is a **daily-refreshed batch source**. This project does **micro-batch
incremental** ingestion — watermarked pulls that fetch only rows newer than the last
loaded `created_date`, then MERGE into Iceberg.

Say this explicitly in the README. Do not describe any part of this project as
streaming, real-time, or event-driven. Streaming is StreamGuard's job; overlapping
claims weaken both projects.

The three-way distinction to articulate:
- **Streaming** — unbounded, event-at-a-time, sub-second (Kafka/Flink). Not this.
- **Micro-batch incremental** — scheduled pulls of new/changed rows only, via a stored
  high-watermark, then upsert. **This project.**
- **Full-refresh batch** — re-pull and rebuild everything each run. Wasteful; a red flag.

## Locked engineering decisions

Do not revisit these without an explicit instruction. Each was decided with a reason.

| Decision | Rationale |
|---|---|
| NYC 311 (`erm2-nwe9`) as the only source | Only candidate that is simultaneously large, genuinely daily-refreshed, free, and clean enough to model into a star schema. CMS is annual/static; USAspending caps at 100 rows/page with null-field quirks; GH Archive's feed degraded mid-2025 to mostly PushEvents. |
| PyIceberg + SQLite catalog + local Parquet warehouse for bronze | Zero cloud cost, zero running services, full Iceberg semantics including upsert and schema/partition evolution. Apache Polaris was considered and rejected — a REST catalog adds a running service for no portfolio gain. |
| **Iceberg spec v2 only** | Athena creates and writes only spec v2 (reads v1). PyIceberg can write v3 features; using them would break the Phase 7 cloud mirror. |
| Bronze partitioned on `created_date`, laid out to mirror the eventual S3 prefix structure | Makes Phase 7 a copy-and-register rather than a rebuild. Partition scheme is the one thing that's expensive to redo. |
| dbt-core + dbt-duckdb for local modeling | Free, fast dev loop. MetricFlow supports DuckDB as a target; it does not treat Athena as first-class. |
| dbt-athena (`table_type='iceberg'`, `merge` strategy) for the Phase 7 cloud slice | Produces the resume phrasing that actually appears in postings: Iceberg on S3 with Glue Data Catalog and Athena. |
| Soda Core **plus** dbt tests + model contracts + unit tests | Covers all four quality categories cheaply. Great Expectations 1.x was evaluated and rejected for this timeline — the 1.0 API was a breaking rewrite with heavy conceptual overhead (Data Contexts, Batch Requests, Expectation Suites). Record that comparative judgment in the README; it is itself a signal. |
| Open-source MetricFlow (Apache 2.0 as of Coalesce 2025) against dbt-core | Gives the semantic-layer bullet with no dbt Cloud subscription. **The hosted Semantic Layer API remains a paid dbt Cloud feature — frame the work as "local MetricFlow," never as "dbt Semantic Layer in production."** |
| GitHub Actions cron for orchestration | Free on public repos, zero infra, doubles as CI/CD. Airflow is already on the resume, so re-proving it adds nothing. Dagster+ moved to pay-as-you-go per-materialization pricing in 2026 — if Dagster is added as a stretch, self-host OSS only. |
| Evidence.dev → Vercel for the public dashboard | The only option satisfying all three constraints: trivial setup, native DuckDB/Parquet connectivity, and a **free always-on public URL**. Queries run once at build time and ship as static files, so there is no server and no per-view query cost. Metabase in Docker is the screenshot/video fallback only — do not pay to keep a BI instance live. |
| Local-first, AWS in Phase 7 | Guarantees a shippable artifact by end of Week 3 regardless of how IAM and Glue wiring goes. Note: the *cost* argument for deferring is weak here (Athena/Glue/S3 have no idle meter, unlike MSK/EKS) — the real reason is schedule insurance. |

## Phase map

| Phase | Scope | Week |
|---|---|---|
| 0 | Environment, credentials, version pinning, data-access verification | 1 |
| 1 | Watermarked incremental ingestion → Iceberg bronze with MERGE | 1 |
| 2 | dbt staging → intermediate → Kimball star schema marts | 2 |
| 3 | Data quality: tests, contracts, unit tests, Soda, DQ scorecard mart | 2 |
| 4 | Semantic layer in MetricFlow | 3 |
| 5 | Evidence.dev dashboard + Vercel public deploy | 3 |
| 6 | GitHub Actions cron, source freshness, Elementary observability | 3 |
| 7 | S3 + Glue + Athena mirror, DuckDB-vs-Athena benchmark, README | 4 |

**Phase files are issued one at a time.** Do not scaffold or begin work on a phase
whose spec file has not been provided. Each phase ends at a STOP gate; the gate must
be verified before the next spec is written.

## Execution protocol

Every phase file separates:
- **HUMAN-ONLY tasks** — anything requiring credentials, a browser, an account, a
  payment method, or a judgment call about scope. Claude Code must never attempt these
  and must never fabricate their output.
- **CLAUDE CODE tasks** — code, config, tests, docs.
- **STOP GATE** — explicit, checkable acceptance criteria. Claude Code halts and reports;
  it does not self-certify and continue.

If a stop-gate criterion cannot be met, **report the blocker and stop.** Do not work
around it silently, do not lower the criterion, and do not mark a gate passed on
partial evidence.

## Metrics to capture as you build

These become resume bullets, so record them in `docs/metrics.md` as they are produced.
Never estimate or backfill a number that wasn't measured.

- Rows ingested (total, and per incremental run)
- Incremental run duration; delta-only confirmation (run 2 adds only new rows)
- Iceberg snapshot count; a working time-travel query
- Test counts by category (data tests / contracts / unit tests / Soda checks) and pass rate
- Documentation coverage (% of models and columns documented)
- Data-quality finding, quantified (e.g. "X% of rows fail closed ≥ created")
- Freshness SLA: target vs achieved, across N scheduled runs
- Phase 7: query latency and Athena bytes-scanned, before and after partitioning;
  DuckDB vs Athena on identical queries; total AWS spend

## Standing rules

1. **Build the quality checks before trusting the marts.** Phase 3 exists before the
   dashboard for a reason.
2. **Always name the baseline.** "Query got faster" is worthless; "1,240 ms → 180 ms
   after partitioning, 4.2 GB → 310 MB scanned" is the bullet.
3. **Cost is a metric.** Record dollars and bytes scanned, not just latency.
4. **Report the failures.** The 311 data-integrity findings are an asset, not an
   embarrassment. Documented negative findings with evidence are the most credible
   thing in a portfolio. Same for anything that doesn't work as documented.
5. **One atomic commit per phase**, with the phase number in the message.
6. **Keep an engineering journal** at `docs/decisions.md`: every non-obvious choice,
   platform quirk, and undocumented behavior discovered, with root cause.
7. **No manual console clicking in Phase 7.** Scripted or IaC only, so it is reproducible.
8. **README must be followable by a stranger**: architecture diagram, quickstart,
   metrics table, limitations section, live demo link.

## Known traps (do not rediscover these the hard way)

- **Athena has no free tier.** The failure mode is not gradual drift, it is one
  unpartitioned full-table scan. Set a per-query data-scan limit on the workgroup
  before running anything (Phase 7, and it is a HUMAN-ONLY task).
- **Athena Iceberg is merge-on-read only** — DML writes positional delete files;
  copy-on-write is not configurable.
- **`OPTIMIZE` caps at ~100 partitions per statement** — needs a `WHERE` clause to scope.
- **Dropping dbt-athena Iceberg models can orphan S3 objects** unless `native_drop`
  is enabled.
- **dbt-athena Iceberg incrementals** require syncing all columns on schema change, and
  **a partition column cannot be removed via incremental refresh — that needs a full refresh.**
- **`dbt source freshness` is a separate command** from `dbt build`; it must be wired
  into CI explicitly or the SLA silently goes unchecked.
- **311 field vocabularies drift by design** — the docs warn that expected values for
  many fields change over time. `accepted_values` tests will legitimately fail on new
  categories. Treat that as a signal to review, not a bug to suppress.
- **DuckDB Iceberg writes require an attached REST catalog** (path-based writing is
  unsupported), which is why bronze writes go through PyIceberg. DuckDB is for reads,
  the dev loop, and the benchmark.
- **Socrata without an app token is throttled** on a shared anonymous pool.

## Environment

MacBook Pro M3 Pro, 18 GB RAM, arm64 / macOS. Everything through Phase 6 runs locally
and free. Phase 7 target: total AWS spend under $5.
