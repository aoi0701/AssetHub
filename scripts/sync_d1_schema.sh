#!/usr/bin/env bash
set -euo pipefail

# Determine project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SOURCE_SCHEMA="${PROJECT_ROOT}/cloudflare-worker/bridge/schema.sql"
TARGET_DIR="${PROJECT_ROOT}/backend/assethub_api/backends/d1/sql"
TARGET_SCHEMA="${TARGET_DIR}/schema.sql"

if [ ! -f "${SOURCE_SCHEMA}" ]; then
  echo "Error: Source schema not found at ${SOURCE_SCHEMA}" >&2
  exit 1
fi

mkdir -p "${TARGET_DIR}"
cp -f "${SOURCE_SCHEMA}" "${TARGET_SCHEMA}"

if cmp -s "${SOURCE_SCHEMA}" "${TARGET_SCHEMA}"; then
  echo "Schema synchronized successfully (byte-identical)."
else
  echo "Error: Schema copy failed to achieve byte-level parity." >&2
  exit 1
fi

# If backend container is active, synchronize container's root copies
if command -v docker >/dev/null 2>&1; then
  if docker ps --format '{{.Names}}' | grep -qw "assethub-backend-1"; then
    docker cp "${PROJECT_ROOT}/cloudflare-worker" assethub-backend-1:/ >/dev/null 2>&1 || true
    docker cp "${PROJECT_ROOT}/scripts" assethub-backend-1:/ >/dev/null 2>&1 || true
  fi
fi
