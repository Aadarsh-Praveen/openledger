# Engineering Journal

Format per entry: what was decided or discovered / what the evidence was / what it
changes downstream.

## Phase 0

### Backfill window: 24 months (provisional)

- **Decided:** Backfill window is 24 months of `created_date`.
- **Evidence:** H0.2 states the full `erm2-nwe9` dataset (2020-01-01 to present) is
  22.2M rows, ~44 columns. That's roughly 6.6 years of data, so 22.2M / 6.6 ≈ 3.4M
  rows/year, and 24 months ≈ 6.7M rows. Two full seasonal cycles supports
  year-over-year comparison. The app-token throttle is reportedly ~1,000
  requests/hour, so ingestibility depends on the real page-size cap.
- **Decision rule (applied mechanically against the C0.3.3/C0.3.6 measurements,
  without asking first):**
  - measured page-size cap ≥ 10,000 rows/page → keep 24 months
  - cap 1,000–10,000 rows/page → recommend 12 months, state the revised row estimate
  - cap < 1,000 rows/page → stop and report; the incremental design needs rework
- **Outcome:** Measured page-size cap was **50,000 rows/page** (`scripts/verify_source.py`,
  run 2026-08-19) → rule says **keep 24 months**. Actual row count in the 24-month
  window: **7,511,072** (higher than the 6.7M rough estimate, still well within budget
  — at the observed 50,000-row cap that's only 151 pages, ~65 seconds of pure request
  time at median latency). See `docs/source-notes.md` C0.3 for full findings.

### Python toolchain: 3.14.7 vs 3.12 compatibility probe

- **Decided:** Use Python 3.14.7 (the system default) as the project interpreter. No
  fallback to 3.12 was needed.
- **Evidence:** Ran `pip install --dry-run` in a scratch venv on 3.14.7 for three
  package sets — Phase 0's actual install list, plus Phase 2's (`dbt-core`,
  `dbt-duckdb`) and Phase 3's (`soda-core-duckdb`) as resolution-only checks (nothing
  from Phase 2/3 was installed). All three resolved without error. Every native
  package needed for Phase 0 (`duckdb`, `pyarrow`, `mmh3`, `pyroaring`,
  `pyiceberg-core`, `zstandard`) had a prebuilt `cp314` wheel — no source builds were
  required for the Phase 0 install itself. Full detail in `docs/versions.md`.
- **Downstream effect:** Project venv (`.venv/`) built directly on 3.14.7. One risk
  surfaced by the probe: `soda-core-duckdb` 3.5.6 hard-pins `duckdb<1.1.0`, which
  conflicts with the `duckdb` 1.5.5 pinned here. It resolved in isolation (and even
  built duckdb 1.0.0 from source rather than erroring) only because it was checked as
  its own pip invocation, not installed alongside the others. Phase 3 will need a
  plan for this — most likely a separate virtualenv/process boundary between Soda and
  the main dbt-duckdb environment, since nothing requires them to share one Python
  process. Not actioned now; Soda isn't installed until Phase 3.

### DuckDB version line: 1.5.5, not 1.4 LTS

- **Discovered:** `pip install duckdb` on 3.14.7 resolved to **duckdb 1.5.5**
  (released 2026-07-22), which is on the 1.5+ line, not the 1.4 LTS line.
- **Evidence:** `pip freeze` output and `docs/versions.md`.
- **Downstream effect:** The partitioned-table UPDATE/DELETE restriction that applies
  on DuckDB 1.4 LTS is lifted on 1.5+ (per CLAUDE.md's known-traps note). Any local
  DuckDB-side mutation logic in later phases can rely on that being lifted — though
  bronze writes still go through PyIceberg regardless, per the locked architecture
  decision (DuckDB path-based Iceberg writes are unsupported).

### Socrata `$where`/pagination behavior matches the incremental design (C0.3)

- **Discovered:** All C0.3 checks passed against the live API on 2026-08-19.
  `$where` on `created_date` returned zero rows outside a requested bound. `$limit`
  accepted up to 50,000 rows in a single page (not clamped to the assumed
  1,000–5,000). `$offset` pagination with `$order=created_date,unique_key` was
  stable — zero `unique_key` overlap between consecutive pages, and page 1 was
  byte-for-byte reproducible on re-pull. 20 sequential requests hit zero throttling
  and no rate-limit headers; median latency 0.426s.
- **Evidence:** `scripts/verify_source.py` output, transcribed into
  `docs/source-notes.md` C0.3 table.
- **Downstream effect:** The watermarked incremental design (Phase 1) is validated —
  nothing here forces a redesign. Phase 1's pagination loop should use a large page
  size (the plan's 1,000–5,000 assumption was overly conservative), which will cut
  the number of HTTP requests substantially.

### Single-row API response does not show all 44 dataset-page columns

- **Discovered:** Q1 of `scripts/verify_source.py` pulled one live row and it
  contained 32 present JSON keys, not 44.
- **Evidence:** Printed column list in the C0.3 run; H0.2's dataset-page column count
  is 44.
- **Downstream effect:** Not a contradiction — Socrata's JSON API omits fields that
  are null for a given row rather than emitting `null` values. Phase 1's schema
  handling must tolerate any optional column being absent on a per-row basis, not
  assume a fixed 44-key shape on every response.

### DQ baseline: several classic 311 defects barely exist in current data (C0.4)

- **Discovered:** On a real one-month sample (July 2026, 342,892 rows, full window
  pulled — not a sub-sample): closed-before-created 0.019%, closed-status-with-null-
  closed-date 0.0003%, out-of-bounds/zero coordinates **0%** (0 of 342,892), missing
  lat/long 1.975%, unspecified/null borough 0.134%. 176 distinct `complaint_type`
  values, 14 distinct `agency` values.
- **Evidence:** `scripts/probe_data_quality.py` output, transcribed into
  `docs/source-notes.md` C0.4 table.
- **Downstream effect:** Per CLAUDE.md's standing rule to report real findings rather
  than manufacture them: the closed-before-created and out-of-bounds-coordinate
  defects that portfolio narratives often lean on are rare-to-absent in current
  (2026) 311 data. Phase 3's DQ scorecard should center on what's actually material
  here — missing coordinates (~2%) and, more marginally, unspecified borough
  (~0.13%) — and report the others honestly as low-incidence rather than inflate
  them.

### DuckDB `iceberg_scan` path/version-guessing quirks (C0.5)

- **Discovered:** Two platform quirks surfaced getting DuckDB to read a
  PyIceberg-written table:
  1. `iceberg_scan(<metadata.json path>)` fails — this DuckDB iceberg extension
     build resolves the path argument as the **table root** and appends
     `/metadata/...` itself; passing the `metadata.json` file path directly produces
     a `<file>.metadata.json/metadata/...` path-join `IOException`.
  2. Even with the table root, DuckDB refused to auto-locate the latest snapshot
     ("no version-hint could be found... globbing... disabled by default") because
     PyIceberg's `SqlCatalog` doesn't write a `version-hint.text` file. Fixed with
     `SET unsafe_enable_version_guessing = true;` before the scan.
- **Evidence:** `scripts/verify_iceberg_stack.py` run transcript (two intermediate
  failures before the working form); final row counts matched (5 = 5) between
  PyIceberg and DuckDB.
- **Downstream effect:** Any later-phase code or docs that read a local Iceberg table
  from DuckDB (dev loop, Phase 7 DuckDB-vs-Athena benchmark) must either pass the
  table root with `unsafe_enable_version_guessing` set, or ensure a version-hint file
  exists. Recorded here so Phase 1+ doesn't rediscover this.
