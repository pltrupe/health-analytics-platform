#!/usr/bin/env bash
# Copy the gold-table JSON snapshots from the exports Volume into the dashboard.
set -euo pipefail
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."
CATALOG="${CATALOG:-workspace}"
SCHEMA="${SCHEMA:-health}"

databricks fs cp -r --overwrite "dbfs:/Volumes/${CATALOG}/${SCHEMA}/exports/dashboard" dashboard/public/data
echo "==> dashboard/public/data refreshed:"
ls -l dashboard/public/data
