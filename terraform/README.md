# Terraform module — Azure Databricks RAG lab infrastructure

Provisions the infra shell for the RAG pipeline in `../notebooks` and `../src`:
Unity Catalog objects, the Vector Search endpoint, a registered-model container,
a Databricks Job that runs notebooks 00–06, and (once a model exists) the Model
Serving endpoint. See the top-of-file comment in `main.tf` for exactly what is
and isn't Terraform-manageable here — some steps (populating tables, logging a
model version, the Review App) are runtime/code actions, not infrastructure.

## Prerequisites

- Terraform >= 1.7
- An Azure Databricks workspace on the **Premium** SKU with Unity Catalog
  attached (required for Vector Search + Model Serving + fine-grained UC
  permissions). If you don't have one yet, set `provision_workspace = true`
  (see below) and this module will create one.
- Azure AD auth available in your shell: either `az login`, or a service
  principal's `ARM_CLIENT_ID` / `ARM_CLIENT_SECRET` / `ARM_TENANT_ID` set as
  environment variables.
- Foundation Model API pay-per-token access enabled on the workspace (for
  `databricks-bge-large-en` and the LLM endpoint) — this is a workspace/account
  entitlement Terraform doesn't control; verify it in the Databricks UI first.

## Authentication

Never put a token or client secret in `.tf`/`.tfvars` files. Set environment
variables instead:

```bash
# Option A — Azure CLI (simplest for interactive/lab use)
az login
export ARM_SUBSCRIPTION_ID="<your-subscription-id>"

# Option B — Service principal (CI/CD)
export ARM_CLIENT_ID="..."
export ARM_CLIENT_SECRET="..."
export ARM_TENANT_ID="..."
export ARM_SUBSCRIPTION_ID="..."

# Option C — Databricks PAT (quickest for a one-off lab, least preferred)
export DATABRICKS_TOKEN="dapiXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
```

## Deploying into an EXISTING workspace (the common case)

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: set git_repo_url and databricks_host at minimum

terraform init
terraform plan
terraform apply
```

This creates: catalog/schema/volume, the git Repo checkout, the Vector Search
*endpoint* (not yet the index), the registered-model container, and the
pipeline Job — but does **not** yet create the Vector Search index or the
Serving endpoint, because both depend on artifacts that only exist after the
pipeline has actually run once (see "Two-phase apply" below).

## Standing up a NEW workspace from scratch

Set `provision_workspace = true` and fill in `azure_subscription_id` /
`azure_resource_group_name` / `azure_location`. Because Terraform resolves
provider configuration before resources, and the `databricks` provider needs
the workspace URL that `azurerm_databricks_workspace` is about to create, this
specific path requires **two applies**:

```bash
terraform apply -target=azurerm_databricks_workspace.this
terraform apply    # now local.databricks_host resolves; everything else applies
```

This is a standard Terraform pattern for "provision the platform your own
provider authenticates against" — not a workaround for a bug in this module.

## Running the pipeline

Terraform creates the Job; it does not run it (don't want `terraform apply`
silently kicking off a multi-minute pipeline with real endpoint costs). Trigger
it explicitly:

```bash
databricks jobs run-now --job-id "$(terraform output -raw pipeline_job_id)"
```

or click **Run Now** on the job in the Databricks Jobs UI (URL in
`terraform output pipeline_job_url`).

## Two-phase resources: vector index and serving endpoint

Both the Vector Search index and the Model Serving endpoint depend on
artifacts that don't exist until code has actually run (a synced Delta table
with Change Data Feed; a registered model version that passed evaluation).
Trying to declare them unconditionally on the very first `apply` would fail.
The pattern here:

| Phase | What you do | What happens |
|---|---|---|
| 1 | `terraform apply` with defaults (`manage_vector_index = false`, `model_version = ""`) | Infra shell + job created; index/serving skipped |
| 2 | Run the job (`00`→`06`) at least once via the Databricks UI or CLI | Silver table + CDF created, index created via the notebook's own SDK call, a model version registered and evaluated |
| 3 | Set `manage_vector_index = true` and `model_version = "<the version notebooks/04 printed>"` in `terraform.tfvars`, re-run `terraform apply` | Terraform imports/manages the index and creates the Serving endpoint going forward |

After phase 3, subsequent re-ingestion/re-registration cycles are still driven
by the notebooks (that's where the actual data/model work happens); Terraform
just keeps owning the endpoint configs and permissions around them.

## What's still manual after `terraform apply`

- **The Review App** (lab task 7): there's no Terraform resource for it. Run
  `notebooks/07_review_app_testing.py`, which calls `databricks.agents.deploy()`
  / `agents.set_permissions()` directly via the Python SDK.
- **Re-running ingestion** when the source docs change: re-trigger the job;
  Terraform doesn't watch for upstream content changes.

## Destroying

```bash
terraform destroy
```

Note this does **not** delete data written by the notebooks that Terraform
never created a resource for (e.g. rows in the Delta tables, MLflow run
history) — only the resources Terraform is tracking in state. Drop the schema
manually (`DROP SCHEMA main.rag_lab CASCADE`) if you want a full teardown.
