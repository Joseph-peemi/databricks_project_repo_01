#!/usr/bin/env bash
# terraform/apply.sh
#
# Apply Terraform to a single environment (dev, staging, or prod) using your
# own `az login` session -- not the CI service principal. Since you already
# own whatever you create, this skips the RBAC-grant / Unity-Catalog-ownership
# dance that .github/workflows/terraform.yml needs for the CI identity.
#
# Usage (from anywhere):
#   ./terraform/apply.sh dev
#   ./terraform/apply.sh staging
#   ./terraform/apply.sh prod
#
# Requires environments/<env>/backend.hcl and terraform.tfvars to already
# exist -- cp from the .example files and fill in first (see root README,
# "Local usage" section). If provision_workspace = true and databricks_host
# is still blank (or the "adb-YYYY..." placeholder), this script handles the
# two-phase apply automatically: create the workspace shell, read back its
# URL, write it into terraform.tfvars, then apply everything else.

set -euo pipefail

ENV="${1:-}"
if [[ "$ENV" != "dev" && "$ENV" != "staging" && "$ENV" != "prod" ]]; then
  echo "Usage: $0 <dev|staging|prod>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$SCRIPT_DIR/environments/$ENV"
cd "$ENV_DIR"

if [[ ! -f backend.hcl ]]; then
  echo "Missing $ENV_DIR/backend.hcl -- cp backend.hcl.example backend.hcl and fill in (see root README)." >&2
  exit 1
fi
if [[ ! -f terraform.tfvars ]]; then
  echo "Missing $ENV_DIR/terraform.tfvars -- cp terraform.tfvars.example terraform.tfvars and fill in." >&2
  exit 1
fi

echo "==> terraform init ($ENV)"
terraform init -backend-config=backend.hcl -input=false

PROVISION_WORKSPACE=$(grep -E '^\s*provision_workspace\s*=' terraform.tfvars | grep -o 'true\|false' || echo "false")
DATABRICKS_HOST=$(grep -E '^\s*databricks_host\s*=' terraform.tfvars | sed -E 's/.*=\s*"(.*)"/\1/' || echo "")

if [[ "$PROVISION_WORKSPACE" == "true" && ( -z "$DATABRICKS_HOST" || "$DATABRICKS_HOST" == *"YYYY"* ) ]]; then
  echo "==> provision_workspace=true and databricks_host isn't set yet -- phase 1: creating the workspace shell"
  terraform apply -target=module.rag_lab.azurerm_databricks_workspace.this -input=false
  WORKSPACE_URL=$(terraform output -raw workspace_url)
  echo "==> workspace created: https://$WORKSPACE_URL"
  echo "==> writing databricks_host into terraform.tfvars"
  python3 - "$WORKSPACE_URL" <<'PYEOF'
import re, sys
url = sys.argv[1]
with open("terraform.tfvars") as f:
    content = f.read()
content = re.sub(
    r'databricks_host\s*=\s*"[^"]*"',
    f'databricks_host = "https://{url}"',
    content,
    count=1,
)
with open("terraform.tfvars", "w") as f:
    f.write(content)
PYEOF
fi

echo "==> terraform plan ($ENV)"
terraform plan -out=.apply.tfplan -input=false

echo
if [[ "$ENV" == "prod" ]]; then
  read -r -p "Type 'prod' to confirm applying to PRODUCTION: " CONFIRM
  if [[ "$CONFIRM" != "prod" ]]; then
    echo "Aborted."
    rm -f .apply.tfplan
    exit 1
  fi
else
  read -r -p "Apply the plan above to $ENV? [y/N] " CONFIRM
  if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted."
    rm -f .apply.tfplan
    exit 1
  fi
fi

echo "==> terraform apply ($ENV)"
terraform apply -input=false .apply.tfplan
rm -f .apply.tfplan
