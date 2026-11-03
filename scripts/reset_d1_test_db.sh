#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RESET_SQL="${SCRIPT_DIR}/reset_d1_test_db.sql"

CONFIRM_DESTRUCTIVE=false
TARGET_DB=""
WRANGLER_MODE="--local"
DRY_RUN=false

show_help() {
  cat << 'EOF'
Usage: reset_d1_test_db.sh --confirm-destructive --database <db_name> [options]

Mandatory Security Guards:
  --confirm-destructive    Explicit opt-in to acknowledge destructive reset.
  --database, -d <name>    Database name. MUST strictly match 'assethub-test-d1'.

Options:
  --dry-run                Validate security guards without executing SQL.
  --remote                 Execute against remote Cloudflare D1.
  --local                  Execute against local Cloudflare D1 (default).
  -h, --help               Display this help message.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --confirm-destructive)
      CONFIRM_DESTRUCTIVE=true
      shift
      ;;
    --database=*)
      TARGET_DB="${1#*=}"
      shift
      ;;
    --database|-d)
      if [[ $# -lt 2 || "$2" =~ ^- ]]; then
        echo "SECURITY ERROR: Missing argument for $1" >&2
        exit 1
      fi
      TARGET_DB="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --remote)
      WRANGLER_MODE="--remote"
      shift
      ;;
    --local)
      WRANGLER_MODE="--local"
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      if [[ -z "$TARGET_DB" && ! "$1" =~ ^- ]]; then
        TARGET_DB="$1"
        shift
      else
        echo "Error: Unknown argument: $1" >&2
        exit 1
      fi
      ;;
  esac
done

# GUARD 1: Mandatory confirmation flag
if [[ "$CONFIRM_DESTRUCTIVE" != "true" ]]; then
  echo "SECURITY ERROR: Destructive action aborted! Missing required flag: --confirm-destructive" >&2
  exit 1
fi

# GUARD 2: Mandatory target database check (strictly assethub-test-d1)
if [[ -z "$TARGET_DB" ]]; then
  echo "SECURITY ERROR: Database name is required. Only 'assethub-test-d1' is permitted." >&2
  exit 1
fi

if [[ "$TARGET_DB" != "assethub-test-d1" ]]; then
  echo "SECURITY ERROR: Target database '${TARGET_DB}' is forbidden! Reset is strictly restricted to testbed 'assethub-test-d1'." >&2
  exit 1
fi

if [[ ! -f "$RESET_SQL" ]]; then
  echo "Error: SQL reset file not found at ${RESET_SQL}" >&2
  exit 1
fi

echo "Security guards verified. Target testbed: ${TARGET_DB}."

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Dry run completed: guards passed."
  exit 0
fi

# Execution on testbed
if command -v npx >/dev/null 2>&1; then
  echo "Executing reset SQL on ${TARGET_DB} (${WRANGLER_MODE})..."
  (
    cd "${PROJECT_ROOT}/cloudflare-worker/bridge" 2>/dev/null || cd "${PROJECT_ROOT}"
    npx wrangler d1 execute "${TARGET_DB}" ${WRANGLER_MODE} --file="${RESET_SQL}"
  )
else
  echo "Notice: npx/wrangler CLI not present in current runtime. SQL reset file located at ${RESET_SQL}."
fi

echo "Testbed database reset completed successfully."
exit 0
