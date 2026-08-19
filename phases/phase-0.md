# Phase 0 — Environment, Credentials, and Data-Access Verification

**Week:** 1 (front half)
**Estimated effort:** 3–5 hours
**Goal:** Prove the data source actually behaves the way the plan assumes, and pin every
version before writing a line of pipeline code.

## Why this phase exists

Two failure modes kill this project in Week 3 if not caught now:

1. **The Socrata API doesn't support the incremental pattern the plan depends on.** The
   entire project narrative rests on watermarked `$where` filtering over `created_date`.
   If that returns wrong counts, is throttled below usable rates, or the dataset moved
   after the December 2025 split, the design changes. Find out in hour two, not week three.

2. **Version drift.** PyIceberg, dbt-core, dbt-duckdb, and DuckDB all move fast, and
   several documented behaviors changed between minor versions (notably DuckDB's
   partitioned-table DELETE/UPDATE restriction, lifted between 1.4 LTS and 1.5). Pin
   everything now and record what was actually installed.

Nothing in this phase produces a deliverable a reviewer sees. It produces the evidence
that the next six phases aren't built on an assumption.

---

## HUMAN-ONLY tasks

Claude Code must not attempt these and must not fabricate their results.

### H0.1 — Register a Socrata app token
1. Create a free account at the NYC Open Data / Socrata developer portal.
2. Register an application and copy the **app token**.
3. Store it in `.env` as `SOCRATA_APP_TOKEN=...`. Confirm `.env` is gitignored.

An app token is technically optional, but anonymous requests share a small throttled
pool. Do not skip this — Phase 1 will make thousands of paginated requests.

### H0.2 — Confirm the dataset identity and current shape
Open the dataset landing page for `erm2-nwe9` in a browser and record:
- The dataset's current title (it was renamed during the Dec 2025 split — verify whether
  `erm2-nwe9` now points at "311 Service Requests from 2020 to Present" or something else)
- The stated update cadence
- The stated row count and column count
- The date range covered
- Whether a separate 2010–2019 historical dataset exists, and its ID

Paste these verbatim into `docs/source-notes.md`. **Do not let Claude Code infer these
from memory** — the dataset was restructured recently and any pre-2026 assumption is suspect.

### H0.3 — Decide the historical backfill window
The full history is tens of millions of rows. Decide how far back Phase 1 backfills.

Recommendation: **24 months.** Enough for seasonality analysis and year-over-year
comparison, small enough to ingest on a laptop in a reasonable time, and it keeps the
Phase 7 S3 mirror comfortably inside the cost target. Record the decision and the
reasoning in `docs/decisions.md`.

If you want the pipeline to *demonstrate* handling a larger corpus, that is a Phase 7
stretch goal, not a Phase 1 scope increase.

### H0.4 — Confirm Python toolchain
Confirm which Python version is active (`python3 --version`) and that you're comfortable
with it as the project baseline. Everything else pins against it.

---

## CLAUDE CODE tasks

### C0.1 — Repository scaffold

Create this structure. Empty directories get a `.gitkeep`.

```
openledger/
├── CLAUDE.md
├── README.md                  # stub only; written in Phase 7
├── .env.example               # keys with empty values, committed
├── .gitignore
├── requirements.txt           # pinned, with a comment on why each pin
├── docs/
│   ├── decisions.md           # engineering journal
│   ├── source-notes.md        # H0.2 output + C0.3/C0.4 findings
│   ├── metrics.md             # measured numbers only
│   └── versions.md            # C0.2 output
├── ingest/
│   └── __init__.py
├── warehouse/                 # local Iceberg warehouse root (gitignored)
├── catalog/                   # SQLite catalog file lives here (gitignored)
├── dbt/                       # scaffolded in Phase 2
├── quality/                   # scaffolded in Phase 3
├── dashboard/                 # scaffolded in Phase 5
└── scripts/
    └── verify_source.py       # C0.3
```

`.gitignore` must cover: `.env`, `warehouse/`, `catalog/`, `*.duckdb`, `__pycache__/`,
`.venv/`, `target/`, `logs/`, `dbt_packages/`.

### C0.2 — Create the virtualenv and pin versions

1. Create a virtualenv. Install: `pyiceberg` (with the `sql-sqlite` and `pyarrow` extras),
   `duckdb`, `requests`, `python-dotenv`, `pyarrow`.
2. **Do not hardcode version numbers from any plan document or from memory.** Install
   current versions, then run `pip freeze` and pin exactly what resolved.
3. Write `docs/versions.md` recording, for each package: the installed version, its
   release date if discoverable, and one line on why it matters. For DuckDB, explicitly
   note whether the installed version is on the 1.4 LTS line or 1.5+, because the
   partitioned-table UPDATE/DELETE behavior differs between them.
4. Do **not** install dbt or Soda yet. Those are Phase 2 and Phase 3, and pinning them
   now risks a resolver conflict you'd have to untangle blind.

### C0.3 — Write and run the source verification script

`scripts/verify_source.py`. It must answer six questions and print a clear report. It is
a throwaway diagnostic, not production code — prioritize legibility over structure.

1. **Reachability.** Fetch one row. Confirm HTTP 200 and that the app token is being
   sent (header `X-App-Token`). Print the column names and types returned.

2. **Does `$where` on `created_date` work?** Request a bounded window (e.g. one specific
   week) and confirm every returned row falls inside it. Print min and max `created_date`
   from the response. **If any row falls outside the window, stop and report** — the
   incremental design is invalid and needs rethinking.

3. **What is the real page size cap?** Request `$limit=50000` and count what actually
   comes back. Socrata clients clamp this at different values; the plan assumed roughly
   1,000–5,000. Record the observed ceiling — Phase 1's pagination loop depends on it.

4. **Does `$offset` paginate correctly and stably?** Pull two consecutive pages with a
   deterministic `$order` (order by `created_date`, then by `unique_key` as a tiebreaker)
   and confirm zero overlap in `unique_key` between them. Then re-pull page 1 and confirm
   it returns identical keys. Unstable pagination without a total ordering is a classic
   silent duplicate source.

5. **Row-count sanity for the chosen backfill window.** Use a `$select=count(*)` query
   with the same `$where` bound as H0.3's window. Print the count. This is the number
   Phase 1's ingestion must reconcile against.

6. **Throughput and throttling.** Make 20 sequential paginated requests, timing each.
   Report median latency, total elapsed, any non-200 responses, and any rate-limit
   headers observed. From the observed rate and the C0.3.5 row count, compute and print
   an **estimated backfill duration**. If that estimate exceeds ~2 hours, flag it — the
   backfill window may need narrowing.

Write all six findings into `docs/source-notes.md` as a table: question, observed value,
implication for Phase 1.

### C0.4 — Probe the data-quality findings early

Using a bounded sample (one month is plenty — do not pull the full backfill yet), measure
and record the rate of each of these. They are the intended Phase 3 findings, and knowing
now whether they actually exist in current data matters.

- Rows where `closed_date` < `created_date`
- Rows with status "Closed" but null `closed_date`
- Rows with null or missing `latitude`/`longitude`
- Rows with coordinates at (0,0) or plainly outside NYC bounds
- Count of distinct `complaint_type` values, and of distinct `agency` values
- Rows where `borough` is "Unspecified" or null

Record each as a percentage of the sample, with the sample size and window stated, in
`docs/source-notes.md`.

**If a finding turns out not to exist in current data, say so plainly.** An honest "the
closed-before-created defect appears in 0.02% of 2026 rows, far less than the literature
suggests" is a better journal entry than a manufactured problem. Phase 3 will build the
scorecard around whatever is actually there.

### C0.5 — Verify the Iceberg local stack end to end

Smallest possible proof that the bronze-layer approach works before Phase 1 depends on it:

1. Create a `SqlCatalog` backed by SQLite at `catalog/`, warehouse root at `warehouse/`.
2. Create a table with a trivial schema, **explicitly at format version 2**, partitioned
   by day on a timestamp column.
3. Append a handful of rows. Confirm a snapshot exists.
4. Append a second batch. Confirm snapshot count incremented.
5. `upsert()` a row that collides on the key. Confirm the row count did not grow and the
   value changed.
6. Run a time-travel read against the first snapshot and confirm it returns the
   pre-second-batch state.
7. Read the same table from DuckDB via the Iceberg extension using the metadata path
   (read-only, no catalog attachment). Confirm row counts match.
8. Print the on-disk directory layout of `warehouse/` and record it in
   `docs/source-notes.md` — this is the layout Phase 7 will mirror to S3, so it needs
   to be understood now.

Then delete the test table and its files. This is a smoke test, not a fixture.

If step 2 cannot be pinned to format version 2, or step 7 fails, **stop and report.**
Both are load-bearing for Phase 7.

### C0.6 — Seed the engineering journal

`docs/decisions.md` gets its first entries: the backfill-window decision from H0.3, the
observed page-size cap and its implication, anything in C0.3–C0.5 that behaved
differently from what the plan assumed, and the DuckDB version-line note.

Format each entry: what was decided or discovered / what the evidence was / what it
changes downstream.

### C0.7 — Initial commit

One commit: `Phase 0: environment, version pinning, and source verification`.

---

## STOP GATE 0

Do not proceed to Phase 1 until every line is true and evidenced. Report each with the
actual observed value, not a checkmark.

| # | Criterion | Evidence required |
|---|---|---|
| 1 | Socrata app token works and is being sent | HTTP 200 with token header; `.env` gitignored |
| 2 | Dataset identity confirmed by human | `docs/source-notes.md` contains H0.2 verbatim |
| 3 | `$where` on `created_date` filters correctly | Min/max of returned rows inside requested window, zero exceptions |
| 4 | Observed page-size cap recorded | The actual number returned for `$limit=50000` |
| 5 | Pagination is stable and non-overlapping | Zero `unique_key` overlap across consecutive pages; page 1 reproducible |
| 6 | Backfill row count known | Count from `$select=count(*)` over the H0.3 window |
| 7 | Estimated backfill duration computed and acceptable | Median request latency + derived estimate; flagged if > 2 h |
| 8 | Data-quality baseline measured | Six rates in `docs/source-notes.md`, with sample size and window |
| 9 | Iceberg spec v2 table created, partitioned, upserted, time-travelled | Snapshot counts before/after; upsert row-count unchanged; time-travel read matches |
| 10 | DuckDB reads the PyIceberg-written table | Matching row counts from both engines |
| 11 | Warehouse on-disk layout documented | Directory tree in `docs/source-notes.md` |
| 12 | Versions pinned and recorded | `requirements.txt` from `pip freeze`; `docs/versions.md` including DuckDB version line |
| 13 | Journal seeded | `docs/decisions.md` has real entries, not placeholders |
| 14 | One atomic commit | Commit message names Phase 0 |

**On any failure: report the blocker and stop.** Criteria 3, 5, 9, and 10 are
load-bearing — a workaround for any of those changes the architecture, which is a
decision for the next phase spec, not something to patch around silently.

---

## What Phase 1 will do (context only — do not start)

Watermarked incremental ingestion: a puller that reads the stored high-watermark,
requests only newer rows with stable ordering and correct pagination, writes raw payloads
as dated Parquet, and MERGEs into the bronze Iceberg table on `unique_key`. Plus the
backfill run, and proof that a second run adds only deltas.
