#!/usr/bin/env bash
# End to end: deploy the bundle, land an export in the Volume, run the job, pull the JSON.
#
#   scripts/run_pipeline.sh                      # synthetic sample export
#   scripts/run_pipeline.sh ~/Downloads/export.zip
#
# Needs the Databricks CLI, authenticated (databricks auth login --host <workspace-url>).
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: don't rewrite /Volumes/... paths

cd "$(dirname "$0")/.."
EXPORT_ZIP="${1:-data/sample/export.zip}"
CATALOG="${CATALOG:-workspace}"
SCHEMA="${SCHEMA:-health}"

echo "==> Deploying bundle"
databricks bundle deploy

echo "==> Making sure ${CATALOG}.${SCHEMA} and its landing volume exist"
databricks schemas create "$SCHEMA" "$CATALOG" >/dev/null 2>&1 || true
databricks volumes create "$CATALOG" "$SCHEMA" landing MANAGED >/dev/null 2>&1 || true

echo "==> Landing ${EXPORT_ZIP}"
databricks fs cp --overwrite "$EXPORT_ZIP" "dbfs:/Volumes/${CATALOG}/${SCHEMA}/landing/export.zip"

echo "==> Running job"
databricks bundle run health_pipeline

scripts/pull_dashboard_data.sh
