#!/usr/bin/env bash
# terraform/destroy.sh
#
# Destroy a single environment (dev, staging, or prod) using your own
# `az login` session -- not the CI service principal.
#
# Usage (from anywhere):
#   ./terraform/destroy.sh dev
#   ./terraform/destroy.sh staging
#   ./terraform/destroy.sh prod
#
# Why this isn't just `terraform destroy`:
#
# 1. modules/rag_lab/main.tf sets `prevent_destroy = true` on the catalog,
#    schema, volume, and registered model on purpose (see the comment there).
#    This script disables it temporarily -- via a trap, so it's restored even
#    if you Ctrl-C or something fails -- rather than you having to remember to
#    edit and revert that file by hand.
#
# 2. Terraform's default destroy order took the Azure Databricks workspace
#    down *before* the catalog/schema/grants in practice, which cuts off the
#    only route back to the Unity Catalog metastore for those objects and
#    leaves the destroy stuck. This script destroys the Databricks-provider
#    resources first (catalog, schema, volume, registered model, job, repo,
#    vector search endpoint, permissions, storage credential, external
#    location) while the workspace is still alive to serve those API calls,
#    then destroys the Azure-side resources (workspace, storage account,
#    resource group, access connector) in a second pass.

set -euo pipefail

ENV="${1:-}"
if [[ "$ENV" != "dev" && "$ENV" != "staging" && "$ENV" != "prod" ]]; then
  echo "Usage: $0 <dev|staging|prod>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$SCRIPT_DIR/environments/$ENV"
MODULE_MAIN="$SCRIPT_DIR/modules/rag_lab/main.tf"
cd "$ENV_DIR"

if [[ ! -f backend.hcl || ! -f terraform.tfvars ]]; then
  echo "Missing backend.hcl or terraform.tfvars in $ENV_DIR -- nothing to destroy from here." >&2
  exit 1
fi

echo "==> terraform init ($ENV)"
terraform init -backend-config=backend.hcl -input=false

echo
if [[ "$ENV" == "prod" ]]; then
  read -r -p "This will DESTROY PRODUCTION. Type 'prod' to confirm: " CONFIRM
  [[ "$CONFIRM" == "prod" ]] || { echo "Aborted."; exit 1; }
else
  read -r -p "This will destroy $ENV. Type '$ENV' to confirm: " CONFIRM
  [[ "$CONFIRM" == "$ENV" ]] || { echo "Aborted."; exit 1; }
fi

echo "==> temporarily disabling prevent_destroy in modules/rag_lab/main.tf"
python3 - "$MODULE_MAIN" <<'PYEOF'
import sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()
old = """  lifecycle {
    prevent_destroy = true
  }"""
new = """  lifecycle {
    prevent_destroy = false # TEMP: destroy.sh -- restored automatically after
  }"""
n = content.count(old)
content = content.replace(old, new)
with open(path, "w") as f:
    f.write(content)
print(f"disabled prevent_destroy on {n} resources")
PYEOF

restore_prevent_destroy() {
  echo "==> restoring prevent_destroy in modules/rag_lab/main.tf"
  python3 - "$MODULE_MAIN" <<'PYEOF'
import sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()
old = """  lifecycle {
    prevent_destroy = false # TEMP: destroy.sh -- restored automatically after
  }"""
new = """  lifecycle {
    prevent_destroy = true
  }"""
n = content.count(old)
content = content.replace(old, new)
with open(path, "w") as f:
    f.write(content)
print(f"restored prevent_destroy on {n} resources")
PYEOF
}
trap restore_prevent_destroy EXIT

DATABRICKS_TARGETS=(
  -target=module.rag_lab.databricks_grants.catalog
  -target=module.rag_lab.databricks_permissions.job
  -target=module.rag_lab.databricks_job.rag_pipeline
  -target=module.rag_lab.databricks_registered_model.rag_model
  -target=module.rag_lab.databricks_volume.raw_docs
  -target=module.rag_lab.databricks_vector_search_index.rag_lab
  -target=module.rag_lab.databricks_vector_search_endpoint.rag_lab
  -target=module.rag_lab.databricks_repo.rag_lab
  -target=module.rag_lab.databricks_schema.rag_lab
  -target=module.rag_lab.databricks_catalog.rag_lab
  -target=module.rag_lab.databricks_external_location.unity_catalog
  -target=module.rag_lab.databricks_storage_credential.unity_catalog
)

echo "==> phase 1: destroying Databricks-provider resources (workspace still alive)"
terraform destroy -auto-approve -input=false "${DATABRICKS_TARGETS[@]}"

echo "==> phase 2: destroying remaining Azure resources (workspace, storage, resource group)"
terraform destroy -auto-approve -input=false

echo "==> $ENV destroyed"
