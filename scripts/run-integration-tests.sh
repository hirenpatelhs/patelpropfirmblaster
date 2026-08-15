#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "usage: $0 POSTGRES_TEST_URL REDIS_TEST_URL" >&2
  exit 2
fi
if [[ ! "$1" =~ [Tt][Ee][Ss][Tt] ]]; then
  echo "PostgreSQL URL must name a dedicated TEST database" >&2
  exit 2
fi
if [[ "$2" =~ /0$ ]] || [[ ! "$2" =~ /[1-9][0-9]*$ ]]; then
  echo "Redis URL must use a dedicated nonzero database" >&2
  exit 2
fi
export DATABASE_URL="$1" REDIS_URL="$2" PPB_RUN_REAL_INTEGRATION=1
export ENVIRONMENT=integration-test APP_SECRET=integration-test-only-not-for-production WORKER_CLAIM_IDLE_MS=1000
script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir/../backend"
python -m alembic upgrade head
python -m pytest tests/integration -q
