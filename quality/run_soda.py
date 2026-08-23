"""
Runs the Soda Core distributional checks against the prod dbt-duckdb build.

Must be invoked with THIS project's Soda venv, not the main one:
    .venv-soda/bin/python quality/run_soda.py

Why a wrapper script, not a bare `soda scan` invocation (C3.1/H3.1):
soda-core-duckdb hard-pins duckdb<1.1.0, incompatible with the main project's
duckdb 1.5.5 (requirements.txt) — so Soda runs in a separate venv
(.venv-soda/, requirements-soda.txt) against DuckDB 1.0.0, reading the same
.duckdb file dbt-duckdb 1.11.0 / DuckDB 1.5.5 built. That cross-version read
was verified to work (docs/decisions.md, C3.1) — but verified for these two
specific pinned versions, not guaranteed for any future upgrade of either.
This script asserts that compatibility explicitly, every run, rather than
assuming it silently holds forever (H3.1 requirement 2).

Also computes "today" once, here, in Python via zoneinfo — deliberately
NOT via DuckDB's own current_date. Found while writing this (see
docs/decisions.md, C3.4): DuckDB 1.0.0's current_date ignores the session
TimeZone setting entirely and returns a UTC-derived date regardless, unlike
DuckDB 1.5.5, where current_date is genuinely session-TimeZone-aware. Using
DuckDB's current_date inside the Soda check SQL would have silently computed
the wrong "today" under the Soda environment specifically — the same class
of bug already found and fixed in int_request_resolution.sql's is_settled
expression, recurring here for the same underlying reason (an unpinned,
version-dependent notion of "now"). Passed into the checks as an explicit
Soda scan variable (${today_ny}) instead, so no check SQL ever calls
current_date/current_timestamp at all.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
PROD_DB_PATH = REPO_ROOT / "dbt" / "target" / "openledger_prod.duckdb"
SODA_CONFIG = REPO_ROOT / "quality" / "soda" / "configuration.yml"
SODA_CHECKS = REPO_ROOT / "quality" / "soda" / "checks" / "distributional_checks.yml"

# Must match dbt_project.yml's created_date_timezone var.
CREATED_DATE_TIMEZONE = "America/New_York"


def assert_read_compatibility() -> None:
    """Open the real prod build with THIS venv's duckdb and run a real query
    against it. Fails loudly with an explicit, named message if it can't —
    the seam most likely to break silently on a future DuckDB upgrade on
    either side (H3.1 requirement 2)."""
    import duckdb

    soda_duckdb_version = duckdb.__version__

    if not PROD_DB_PATH.exists():
        print(
            f"ERROR: {PROD_DB_PATH} does not exist. Run `dbt build --target prod` "
            "first — Soda checks the build's output, it does not build it.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        con = duckdb.connect(str(PROD_DB_PATH), read_only=True)
        con.execute("select count(*) from main.fct_service_requests").fetchone()
        con.close()
    except Exception as exc:
        # DuckDB's file lock is exclusive even against a read_only opener — a
        # concurrent `dbt build` holding the file produces "Could not set lock
        # on file" here, which is NOT a version-drift problem and has a
        # different fix (don't schedule Soda and dbt build to overlap — see
        # docs/decisions.md, H3.1(c)). Distinguished from a genuine drift/
        # corruption failure by message content, since DuckDB raises the same
        # IOException type for both — confirmed empirically for both cases.
        if "Could not set lock" in str(exc):
            print(
                f"ERROR: Soda's DuckDB {soda_duckdb_version} could not open "
                f"{PROD_DB_PATH.name} — another process currently holds the "
                f"file (most likely a concurrent `dbt build`). This is a "
                f"scheduling conflict, not a version drift between the two "
                f"DuckDB pins — do not run Soda while dbt build is running "
                f"(docs/decisions.md, H3.1(c)).\n"
                f"Original error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"ERROR: Soda's DuckDB {soda_duckdb_version} cannot read "
            f"{PROD_DB_PATH.name} — the environments have drifted.\n"
            f"This file was built by the main project venv's dbt-duckdb "
            f"(see requirements.txt for its pinned duckdb version); Soda "
            f"reads it from a separate venv pinned to duckdb=={soda_duckdb_version} "
            f"(requirements-soda.txt), a compatibility verified empirically for "
            f"today's specific version pair (docs/decisions.md, C3.1) and not "
            f"guaranteed across an upgrade of either pin.\n"
            f"Original error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> int:
    assert_read_compatibility()

    today_ny = datetime.now(ZoneInfo(CREATED_DATE_TIMEZONE)).date().isoformat()

    soda_bin = Path(sys.executable).parent / "soda"
    cmd = [
        str(soda_bin),
        "scan",
        "-d",
        "openledger_prod",
        "-c",
        str(SODA_CONFIG),
        "-v",
        f"today_ny={today_ny}",
        str(SODA_CHECKS),
    ]
    result = subprocess.run(cmd)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
