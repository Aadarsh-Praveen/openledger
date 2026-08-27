#!/usr/bin/env bash
#
# C6.4 — refuse any dbt invocation that carries a full-refresh, in CI.
#
# Why enforced, not just documented: `dbt build --full-refresh` against the
# S3-native bronze table would trigger a full ~140s+ re-scan of all 808 S3
# objects for every table model (C6.4 measured this), and — before Phase 6's
# Variant-B change — would also have wiped the incremental scorecard's base.
# Every other documented-but-unenforced scheduling constraint in this project
# eventually got tripped by accident (partial_parse, is_settled, the session
# timezone). This is the cheap insurance: the wrapper all CI dbt calls go
# through, which exits nonzero before dbt starts if a full-refresh is present.
#
# Local dev is unaffected — developers call `dbt` directly, not this wrapper.
#
# Usage in CI:  scripts/dbt_guard.sh build --profiles-dir . --target prod
set -euo pipefail

for arg in "$@"; do
  case "$arg" in
    --full-refresh|--full-refresh=true|--full-refresh=True|--full-refresh=1|-f)
      echo "::error::dbt_guard: '$arg' is forbidden in CI — a full refresh re-scans all of S3 bronze and destroys incremental state. See docs/decisions.md, C6.4. Remove it or run the rebuild as a deliberate, watched manual operation." >&2
      exit 2
      ;;
  esac
done

case "${DBT_FULL_REFRESH:-}" in
  1|true|True|TRUE)
    echo "::error::dbt_guard: DBT_FULL_REFRESH=${DBT_FULL_REFRESH} is set — forbidden in CI (dbt honors this env var as --full-refresh). See docs/decisions.md, C6.4." >&2
    exit 2
    ;;
esac

DBT_BIN="$(dirname "$0")/../.venv/bin/dbt"
[ -x "$DBT_BIN" ] || DBT_BIN="dbt"   # fall back to PATH if the venv layout ever changes
exec "$DBT_BIN" "$@"
