# Versions (Phase 0)

## Python toolchain

- **Python 3.14.7** (system default via Homebrew, confirmed by `python3 --version`).
- H0.4 required a compatibility probe before committing to this interpreter, because
  3.14 is new enough that native-extension packages sometimes lack wheels.
- Probe (dry-run only, nothing installed): `pip install --dry-run` against a scratch
  venv on 3.14.7 for three package sets:
  1. `pyiceberg[sql-sqlite,pyarrow] duckdb requests python-dotenv` — resolved cleanly,
     pre-built `cp314` wheels found for every native package (`duckdb`, `pyarrow`,
     `mmh3`, `pyroaring`, `pyiceberg-core`, `zstandard`, `sqlalchemy`).
  2. `dbt-core dbt-duckdb` (Phase 2, resolution check only) — resolved cleanly.
  3. `soda-core-duckdb` (Phase 3, resolution check only) — resolved, but pulls
     `duckdb<1.1.0` and built that duckdb version from an sdist (`duckdb-1.0.0.tar.gz`,
     no prebuilt wheel for it), rather than failing. Flagged below as a future
     conflict, not a Phase 0 blocker.
- **Outcome:** all three probes resolved without error → Python 3.14.7 was used as-is
  for the project venv (`.venv/`). No fallback to 3.12 was needed.
- **Known downstream risk (not actioned in Phase 0):** `soda-core-duckdb` 3.5.6 pins
  `duckdb<1.1.0`, which directly conflicts with the `duckdb` 1.5.5 pinned here for
  local dev. Phase 3 will need to resolve this — likely by installing Soda into a
  separate venv/environment from the main dbt-duckdb one, since the two never need to
  share a Python process. Recorded in `docs/decisions.md`.

## Installed packages (`requirements.txt`, from `pip freeze`)

Installed into `.venv` (Python 3.14.7) via:
`pip install 'pyiceberg[sql-sqlite,pyarrow]' duckdb requests python-dotenv pyarrow`

| Package | Installed version | Release date (if discoverable) | Why it matters |
|---|---|---|---|
| pyiceberg | 0.11.1 | 2026-03-03 | Core Iceberg table read/write library; the bronze layer's catalog and MERGE/upsert logic depend on it directly. |
| pyiceberg-core | 0.7.0 | not independently checked (bundled native extension of pyiceberg) | Native (Rust) acceleration extension pulled in automatically by `pyiceberg`. |
| duckdb | **1.5.5** | 2026-07-22 | Local read engine and dev loop. **On the 1.5+ line, not the 1.4 LTS line** — the partitioned-table UPDATE/DELETE restriction that exists on 1.4 LTS is lifted here. This matters for any local DuckDB-side mutation logic in later phases; PyIceberg still owns bronze writes regardless. |
| pyarrow | 25.0.1 | 2026-08-10 | Arrow interop for pyiceberg reads/writes and Parquet I/O. |
| requests | 2.34.2 | not checked | Socrata HTTP client for `scripts/verify_source.py` and later the ingest puller. |
| python-dotenv | 1.2.3 | not checked | Loads `SOCRATA_APP_TOKEN` from `.env` without ever hardcoding it. |
| sqlalchemy | 2.0.52 | not checked | Backing engine for pyiceberg's `SqlCatalog` (SQLite catalog). |
| mmh3 | 5.2.1 | not checked | MurmurHash3 implementation pyiceberg uses for partition bucketing/hashing. |
| pyroaring | 1.1.0 | not checked | Roaring-bitmap dependency pulled in by `pyiceberg[sql-sqlite]`. |
| zstandard | 0.25.0 | not checked | Compression codec used by pyiceberg for metadata/Parquet. |
| fsspec | 2026.7.0 | not checked | Filesystem abstraction pyiceberg uses under `pyarrow` extras. |
| pydantic / pydantic_core | 2.13.4 / 2.46.4 | not checked | pyiceberg's config and schema validation models. |
| click | 8.4.2 | not checked | CLI plumbing dependency of pyiceberg. |
| rich | 14.3.4 | not checked | Console output formatting used by pyiceberg's CLI surface. |
| strictyaml | 1.7.3 | not checked | pyiceberg catalog/config file parsing. |
| tenacity | 9.1.4 | not checked | Retry logic inside pyiceberg. |
| cachetools | 6.2.6 | not checked | Caching utility used by pyiceberg's catalog layer. |
| certifi, charset-normalizer, idna, urllib3 | 2026.7.22 / 3.5.1 / 3.19 / 2.7.0 | not checked | Transitive `requests` dependencies (TLS, encoding). |
| python-dateutil, six | 2.9.0.post0 / 1.17.0 | not checked | Transitive dependency of `strictyaml`. |
| markdown-it-py, mdurl, Pygments | 4.2.0 / 0.1.2 / 2.21.0 | not checked | Transitive dependencies of `rich`. |
| typing-extensions, typing-inspection, annotated-types | 4.16.0 / 0.4.4 / 0.8.0 | not checked | Transitive `pydantic` typing support. |

Full pinned list with per-line rationale comments lives in `requirements.txt`. Not
installed yet, per phase-0 scope: **dbt-core, dbt-duckdb, soda-core-duckdb** (Phases 2
and 3 — dry-run resolution only, verified above, nothing installed to `.venv`).

## Phase 2 additions

Installed 2026-08-21 via `pip install dbt-core dbt-duckdb` (matches the Phase 0
dry-run probe — resolved cleanly the same way, now actually installed):

| Package | Installed version | Why it matters |
|---|---|---|
| dbt-core | 1.12.3 | Modeling framework: staging/intermediate/marts, tests, docs. |
| dbt-duckdb | 1.11.0 | dbt adapter targeting local DuckDB; supports the `iceberg` extension and arbitrary connection `settings`/`extensions` via the profile, which is what makes the direct bronze read path work (see C2.1 findings in `docs/decisions.md`). |
| metricflow | 0.212.0 | Bundled transitively with dbt-core; this is the local, Apache-2.0 MetricFlow the Phase 4 semantic layer will use directly against dbt-core (not the paid dbt Cloud Semantic Layer — see CLAUDE.md's locked decision). |

`duckdb` itself stayed at **1.5.5** (no downgrade forced by dbt-duckdb 1.11.0's
`duckdb>=1.0.0` constraint) — the 1.4-LTS-vs-1.5+ distinction from Phase 0 still
applies unchanged. ~40 further transitive dependencies (jinja2, jsonschema, sqlglot,
etc.) pinned in `requirements.txt`, not individually annotated here — none are
load-bearing for anything this project does directly.

## Phase 3 additions

**Two separate, deliberately un-mergeable environments** (per C3.1's Soda/DuckDB
resolution — H3.1 approved): the main project venv (`.venv/`, `requirements.txt`)
and a second, independent venv for Soda (`.venv-soda/`, `requirements-soda.txt`),
installed 2026-08-21.

| Environment | Python | DuckDB | soda-core-duckdb | Why separate |
|---|---|---|---|---|
| `.venv/` (main) | 3.14.7 | **1.5.5** | not installed | Everything else in the project — dbt, pyiceberg, ingestion. |
| `.venv-soda/` (Soda only) | 3.11.15 | **1.0.0** | 3.5.6 | `soda-core-duckdb` 3.5.6 (the latest release as of this check) still hard-pins `duckdb<1.1.0` in its published wheel metadata — confirmed directly, not assumed. Cannot coexist with the main venv's DuckDB 1.5.5 in one environment. |

**The compatibility this rests on is verified for today's exact versions only, not
guaranteed going forward** (H3.1's first requirement): DuckDB 1.0.0 was confirmed
able to read a database file written by DuckDB 1.5.5 (C3.1's storage-format check),
but a future upgrade to either pin could break that silently — DuckDB's storage
format has changed across major versions before. `quality/run_soda.py` asserts
this compatibility explicitly at scan start every run (H3.1's second requirement —
see `docs/decisions.md`, C3.4) rather than relying on this table staying accurate.

Full pinned list for the Soda environment, with the same "why separate" rationale,
lives in `requirements-soda.txt`.

## Phase 4 additions

**MetricFlow, installed into the main venv — no separate environment needed**,
unlike Soda. `metricflow==0.212.0` was already a transitive dependency of
`dbt-core==1.12.3` (used internally for manifest parsing); `dbt-metricflow==0.14.0`
(the separate package providing the actual `mf` CLI) was resolved with
`pip install --dry-run` first — zero conflicts against the existing
`dbt-core<1.13,>=1.11` / `dbt-duckdb==1.11.0` pins, confirmed before installing
for real. 5 small new transitive deps (`halo`, `log-symbols`, `spinners`,
`termcolor`, `update-checker` — all CLI-spinner-related, non-load-bearing).

**License, confirmed via `pip show`, not assumed from the plan's claim**:
both `metricflow` and `dbt-metricflow` report `License-Expression: Apache-2.0`.

**Confirmed: no dbt Cloud account or credentials involved.** `mf` (the CLI
entry point `dbt_metricflow.cli.main:cli`) queries directly against dbt-core's
own compiled `target/semantic_manifest.json` and runs SQL through dbt-duckdb's
adapter connection — the same local DuckDB file dbt itself builds, no network
call to any dbt Labs service. No `DBT_CLOUD_*` environment variables exist
anywhere in this project's environment (checked directly).

**Two real toolchain requirements found, neither obvious from the plan, both
now handled**:
1. **A time spine is mandatory** — `mf` refuses to run anything ("At least
   one time spine must be configured to use the semantic layer") until one
   model is designated via `time_spine: standard_granularity_column: <col>`
   config. `dim_date` (already a full one-row-per-day calendar, 2024-08-19
   through 2027-12-31) was reused for this rather than building a duplicate
   spine model — `calendar_date` is already exactly what a time spine needs.
2. **`mf` does not take `--target`/`--profiles-dir` CLI flags the way `dbt`
   does.** It resolves the target via the `DBT_TARGET` environment variable
   (defaulting to `profiles.yml`'s own `target:` if unset) — and critically,
   **that target must match whatever target the manifest was last parsed
   with**, or `mf query` fails at runtime with a DuckDB catalog-not-found
   error (`Catalog "openledger_prod" does not exist!"`) — not a MetricFlow
   bug, but a real mismatch: the compiled SQL has the parse-time target's
   catalog name baked in as a literal, while `mf`'s own connection opens
   whatever target it resolves separately, and DuckDB catalog names are
   database-file-specific (`openledger_prod` vs `openledger_dev`).

   **The single sanctioned invocation, and only this one — no manual
   variant** (a cwd-based default without `DBT_PROFILES_DIR` was tested and
   does also work when run from `dbt/`, but is deliberately NOT used or
   documented as an alternative: a scheduled job silently picking up
   whichever variant someone happened to type is exactly how the
   catalog-mismatch failure above reaches production undetected — see
   H4.2 review):
   ```
   DBT_TARGET=prod DBT_PROFILES_DIR=. mf query --metrics <...> [...]
   ```
   run from the `dbt/` directory, immediately after `dbt build
   --profiles-dir . --target prod` (or any `dbt parse`/`dbt build` with
   the same `--target prod`) — same explicit `--profiles-dir .` this
   project already always uses for `dbt` itself. **This exact form is
   what Phase 6's scheduled job must invoke, unchanged** — it is not a
   convenience example. Every `mf` invocation in this project's C4.2-C4.5
   work uses this exact command, with no other variant used anywhere.
   See `docs/decisions.md`, C4.1, for the full reproduction.

## Phase 5 additions

**`dashboard/package.json`** (npm, Node v20.19.0): `@evidence-dev/evidence@40.1.8`
(MIT), `@evidence-dev/duckdb@^2.0.1`, `@evidence-dev/core-components@^5.4.2`,
`@evidence-dev/tailwind@^3.1.4`, `@sveltejs/vite-plugin-svelte@3.1.2` —
**pinned exactly, not a caret range**, because installing it unpinned
resolves to a 7.x major requiring Vite 8 while the rest of the Evidence
40.1.8 dependency tree is built against Vite 5.4, producing a build
failure with no useful error message pointing at the version mismatch
(`docs/decisions.md`, Phase 5, has the full symptom and fix). Plus
`git-remote-origin-url@^4.0.0`, and `autoprefixer`/`postcss` as
devDependencies.

**A real product split found, not assumed**: the previously-documented
scaffolding path, `npx degit evidence-dev/template`, now provisions a
different, incompatible, hosted product ("Evidence Studio" — requires
`evidence login`, defaults to a remote "Evidence Warehouse" backend).
The classic, self-hosted, static-build tool this project's architecture
requires still exists as a separately-installable, actively-maintained
package (`@evidence-dev/evidence`, last published 2026-02-06) — confirmed
via `npm view`, not assumed from the degit template's now-stale
association with it. Scaffolded by hand instead of via the broken
template.
