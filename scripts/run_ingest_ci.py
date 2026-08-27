"""
C6.3 — the scheduled ingest workflow's entrypoint.

Wraps ingest.pipeline.run_incremental() for CI: forces the S3-native bronze
table (OPENLEDGER_USE_S3=1, C6.1), prints a summary, and separates two
distinct failure modes so the workflow can react to each correctly (C6.6):

- An exception here means the S3 write itself failed, or the Socrata fetch
  did. This script exits nonzero WITHOUT writing $GITHUB_OUTPUT, so the
  workflow's later "commit state" step (gated on this step's success) is
  skipped by default — watermark/checkpoint/staleness in git are left
  untouched at their last-known-good value. This is C6.1's "advance the
  pointer only after S3 confirms" design applied one level up, at the git
  layer (see docs/decisions.md, C6.1 requirement 6).
- A tripped staleness alarm is NOT an exception — the run itself succeeded
  (or correctly short-circuited) and that outcome must still be persisted.
  This script exits 0 and instead writes alarm=true to $GITHUB_OUTPUT; a
  separate, later workflow step fails the job for that specific reason,
  after the state commit has already happened.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["OPENLEDGER_USE_S3"] = "1"

from ingest.config import STALENESS_ALARM_THRESHOLD
from ingest.pipeline import run_incremental


def main():
    result = run_incremental()
    staleness = result["staleness"]
    no_advance = staleness["consecutive_no_advance"]
    short_circuits = staleness.get("consecutive_short_circuits", 0)
    print(
        f"rows_fetched={result['rows_fetched']} rows_updated={result['rows_updated']} "
        f"rows_inserted={result['rows_inserted']} rows_no_op={result['rows_no_op']} "
        f"skipped={result.get('skipped', False)} "
        f"consecutive_no_advance={no_advance} "
        f"consecutive_short_circuits={short_circuits} "
        f"last_advanced={staleness['last_advanced']}"
    )

    alarm = no_advance >= STALENESS_ALARM_THRESHOLD
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"alarm={'true' if alarm else 'false'}\n")
            f.write(f"consecutive_no_advance={no_advance}\n")
            f.write(f"consecutive_short_circuits={short_circuits}\n")

    if alarm:
        print(
            f"STALENESS ALARM: {no_advance} consecutive runs without a watermark advance "
            f"(threshold={STALENESS_ALARM_THRESHOLD}); {short_circuits} of them were no-op "
            f"short-circuits. State will still be committed; a later workflow step fails "
            f"this job for this reason specifically. See docs/decisions.md, C6.2.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
