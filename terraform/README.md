# Terraform — Azure Databricks RAG lab infrastructure (organization production standard)

Provisions the infra shell for the RAG pipeline in `../notebooks` and `../src` across
**dev / staging / prod**, with remote state, deletion protection on data-bearing
resources, variable validation, security scanning, and a CI/CD pipeline with
approval gates. If you're looking for the flat single-environment version this grew
out of, it no longer exists on `main` — this structure replaces it.

## Structure

```
terraform/
├── modules/rag_lab/          # the ONE reviewed module -- identical code runs in every environment
│   ├── main.tf                # catalog/schema/volume (deletion-protected), repo, vector search,
│   │                           # registered model, job, serving endpoint, permissions
│   ├── azure_workspace.tf     # optional: provision the Azure Databricks workspace itself
│   ├── network.tf             # optional: diagnostic logging to Log Analytics
│   ├── variables.tf           # inputs, most with NO default -- forces explicit per-env values
│   ├── outputs.tf
│   └── versions.tf            # NO provider blocks -- see the comment at the top of versions.tf
├── environments/
│   ├── dev/                   # cheap, permissive, single-node, scale-to-zero
│   ├── staging/               # mirrors prod's latency/network posture to catch issues pre-release
│   └── prod/                  # network hardening ON by default, no scale-to-zero, deletion-protected
│       (each: providers.tf, backend.tf, variables.tf, main.tf, outputs.tf,
│        terraform.tfvars.example, backend.hcl.example)
├── bootstrap/                 # run ONCE per Azure subscription: creates the remote state storage account
├── policy/README.md           # security-scanning policy (tfsec gate, suppression convention)
└── .tflint.hcl
```

See the top-of-file comment in `modules/rag_lab/main.tf` for exactly what is and
isn't Terraform-manageable — populating tables, logging a model version, and the
Review App are runtime/code actions, not infrastructure, in every environment.

## One-time setup (platform admin, not per-developer)

### 1. Bootstrap the remote state backend

```bash
cd terraform/bootstrap
terraform init
terraform apply
terraform output backend_config_dev      # paste into environments/dev/backend.hcl
terraform output backend_config_staging  # paste into environments/staging/backend.hcl
terraform output backend_config_prod     # paste into environments/prod/backend.hcl
```

Do this once. `bootstrap/` intentionally uses **local** state — it's the one
piece of infrastructure that can't depend on the remote state store it's
creating (see the comment at the top of `bootstrap/main.tf`). Back up its
`terraform.tfstate` somewhere durable (encrypted, outside this repo).

### 2. Azure AD OIDC federation (for CI, avoids any stored client secret)

Create an App Registration, grant it Contributor + Storage Blob Data Contributor
scoped to the resource groups involved, then add a federated credential trusting
GitHub Actions for this repo:

```bash
az ad app federated-credential create \
  --id <app-registration-object-id> \
  --parameters '{
    "name": "github-actions-rag-lab",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:Joseph-peemi/databricks_project_repo_01:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

Add a second federated credential with `"subject": "repo:...:pull_request"` so PR
plans (which don't run on the `main` ref) can authenticate too.

### 3. GitHub repo configuration

In **Settings → Environments**, create six environments:
`dev`, `dev-plan`, `staging`, `staging-plan`, `prod`, `prod-plan`.

- `*-plan` environments: read-only credentials, **no required reviewers** — PR
  plans run and post automatically.
- `dev`: no required reviewers — merges to `main` auto-apply.
- `staging` / `prod`: **add required reviewers** here. This is the actual approval
  gate; `.github/workflows/terraform.yml` has no gating logic of its own, it just
  references these environment names and GitHub enforces the rule.

On each environment, set these as **Environment variables** (not secrets — none of
this is sensitive; the OIDC token exchange is what's actually protected):
`ARM_CLIENT_ID`, `ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID`, `TF_STATE_RG`,
`TF_STATE_SA`, `TF_STATE_CONTAINER`, `DATABRICKS_HOST`, `RUN_AS`,
`NOTIFICATION_EMAILS` (JSON list string, e.g. `["you@company.com"]`),
`REVIEWER_EMAILS`. `prod` additionally needs `PROD_GIT_BRANCH`, `PROD_VNET_ID`,
`PROD_PUBLIC_SUBNET_NAME`, `PROD_PRIVATE_SUBNET_NAME`, and (Azure Databricks
workspace `custom_parameters` quirk: this field takes the *subnet's own*
resource ID, not the NSG's) `PROD_PUBLIC_SUBNET_NSG_ASSOCIATION_ID` /
`PROD_PRIVATE_SUBNET_NSG_ASSOCIATION_ID` (network hardening defaults ON in
prod — see `environments/prod/variables.tf`).

The VNet itself is **not** created by this module (see `azure_workspace.tf`'s
top comment) — it's a landing-zone dependency a platform team owns. For this
lab, that VNet was provisioned by hand in its own resource group
(`rg-network-prod`, kept separate from `rg-rag-lab-prod` so Terraform's
`azurerm_resource_group.this` doesn't collide with it): a `/16` VNet with two
`/24` subnets (`public-subnet`, `private-subnet`), each delegated to
`Microsoft.Databricks/workspaces`, each with its own NSG using Azure's
default rules (no custom rules needed — the defaults already allow the
VNet-internal and outbound-internet traffic Databricks' control plane needs).
The CI service principal also needs **Network Contributor** on that resource
group, since the workspace deployment has to join those subnets.

If an environment sets `provision_workspace = true` in its `terraform.tfvars`
(dev does, to stand up its own workspace — see "Standing up a NEW Azure
Databricks workspace from scratch" below), that environment's GitHub
Environment **also** needs `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP_NAME`,
`AZURE_LOCATION`, `PROVISION_WORKSPACE`, and `NODE_TYPE_ID` set to match — CI
has no access to your local `terraform.tfvars` (it's gitignored), so without
these the apply job falls back to the variable defaults (`provision_workspace
= false`), which plans to *destroy* the resource group and workspace instead
of matching your local state.

Also note: the federated credential `subject` in step 2 assumes your org
hasn't turned on "use repository and organization ID instead of name" for
Actions OIDC subject claims. If it has (check via `gh api
repos/<owner>/<repo>/actions/oidc/customization/sub`), and any job pins an
`environment:` (every job in `terraform.yml` does), the actual subject GitHub
presents is `repo:<owner>@<owner-id>/<repo>@<repo-id>:environment:<env-name>`
— federated credentials need one entry per environment name
(`dev`/`dev-plan`/`staging`/`staging-plan`/`prod`/`prod-plan`), not the
`ref:refs/heads/main` / `pull_request` subjects shown above.

### 4. Databricks-side access for the CI identity

Azure RBAC (step 2) only covers the `azurerm` provider. The `databricks`
provider authenticates as the same service principal but Databricks enforces
its *own* authorization on top — Azure RBAC gets the SP nothing inside the
workspace or Unity Catalog. Without this step, `apply` fails with
`User not authorized` (workspace-level calls: repo, jobs, vector search,
spark_version/node_type lookups) or `User does not have any privileges on ...`
(Unity Catalog objects), even though `terraform init`/auth succeed.

**Workspace access** — add the SP as a member of the workspace's `admins`
group (SCIM API, needs an AAD token for the well-known Azure Databricks
resource ID `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d`, run as someone who's
already a workspace admin):

```bash
TOKEN=$(az account get-access-token --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d --query accessToken -o tsv)
HOST="https://<workspace-url>"

# register the SP in the workspace
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/scim+json" \
  "$HOST/api/2.0/preview/scim/v2/ServicePrincipals" -d '{
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"],
    "applicationId": "<sp-app-id>",
    "displayName": "github-actions-rag-lab",
    "active": true,
    "entitlements": [{"value": "workspace-access"}, {"value": "allow-cluster-create"}]
  }'
# note the returned "id", then add it to the admins group (look up the
# admins group id via GET .../Groups?filter=displayName%20eq%20admins)
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/scim+json" \
  "$HOST/api/2.0/preview/scim/v2/Groups/<admins-group-id>" -d '{
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
    "Operations": [{"op": "add", "value": {"members": [{"value": "<sp-scim-id>"}]}}]
  }'
```

**Unity Catalog access** — workspace admin alone isn't enough for UC
securables; each one enforces its own grants. Grant `MANAGE` to the SP
(identified by its Azure AD application ID) on every UC object the module
touches:

```bash
grant() { # $1 = securable type, $2 = full name
  curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    "$HOST/api/2.1/unity-catalog/permissions/$1/$2" \
    -d "{\"changes\":[{\"principal\":\"<sp-app-id>\",\"add\":[\"MANAGE\"]}]}"
}
grant storage_credential uc-storage-credential-<env>
grant external_location  uc-external-location-<env>
grant schema             <env>.rag_lab
grant volume             <env>.rag_lab.raw_docs
```

**Catalog is different — grant it via ownership, not `MANAGE`.** The module's
`databricks_grants.catalog` resource (`main.tf`) is *authoritative*: every
apply replaces the catalog's whole grant list with exactly what's declared
(`run_as`/`reviewer_emails`), which doesn't include the CI SP. If the SP's
only authority is a `MANAGE` grant, that first `apply` strips its own grant
as part of reconciling to desired state and dies mid-call with `User does not
have MANAGE on Catalog` — it revoked the permission it needed to finish the
revoke. Ownership isn't touched by `databricks_grants`, so make the SP the
catalog owner instead:

```bash
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$HOST/api/2.1/unity-catalog/catalogs/<env>" -d '{"owner":"<sp-app-id>"}'
```

(`registered_model` permissions returned `REGISTERED_MODEL is not enabled`
from this same API in testing — that securable type doesn't take grants this
way. It didn't block `apply`, so left as-is; revisit if it ever does.)

## Day-to-day workflow (after one-time setup)

1. Branch, edit `modules/rag_lab/*` or an environment's `terraform.tfvars`, open a PR.
2. CI runs `fmt -check`, `validate` (all 3 environments + module + bootstrap),
   `tflint`, `tfsec` (blocking), then a real `terraform plan` per environment
   posted as a PR comment.
3. Merge to `main` → `apply-dev` runs automatically → `apply-staging` waits for a
   reviewer approval in the GitHub UI → `apply-prod` waits for a separate
   reviewer approval. Promotion is linear: prod can't apply before staging does.

## Local usage (debugging, or before CI/environments exist)

```bash
cd environments/dev
cp backend.hcl.example backend.hcl        # fill in from bootstrap output; gitignored
cp terraform.tfvars.example terraform.tfvars   # fill in; gitignored
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

This creates: catalog/schema/volume, the git Repo checkout, the Vector Search
*endpoint* (not yet the index), the registered-model container, and the pipeline
Job — but **not yet** the Vector Search index or Serving endpoint (see "Two-phase
resources" below).

## Standing up a NEW Azure Databricks workspace from scratch

Set `provision_workspace = true` in that environment's tfvars, plus
`azure_subscription_id` / `azure_resource_group_name` / `azure_location`. Because
Terraform resolves provider configuration before resources, and the `databricks`
provider needs the workspace URL that `azurerm_databricks_workspace` is about to
create, this path requires **two applies**:

```bash
terraform apply -target=module.rag_lab.azurerm_databricks_workspace.this
terraform apply    # everything else applies now that the workspace URL resolves
```

Standard Terraform pattern for "provision the platform your own provider
authenticates against" — not a workaround for a bug in this module.

If you're applying as a CI service principal (not your own `az login`
session), the first apply also creates the resource group, so you can't
grant it Azure RBAC scoped to that resource group beforehand — do it between
the two applies (Contributor + Storage Blob Data Contributor, same as step 2)
and add the workspace-admin/Unity-Catalog access from step 4 before the
second apply, or that one will fail on the UC resources (catalog, storage
credential, etc.) the same way `apply(dev)` initially did.

## Running the pipeline

Terraform creates the Job; it does not run it (an `apply` silently kicking off a
multi-minute, real-money pipeline run would be a nasty surprise). Trigger it
explicitly:

```bash
databricks jobs run-now --job-id "$(terraform output -raw pipeline_job_id)"
```

## Two-phase resources: vector index and serving endpoint

Both depend on artifacts that don't exist until code has actually run (a synced
Delta table with Change Data Feed; a registered model version that passed
evaluation). Declaring them unconditionally on the first `apply` would fail.

| Phase | What you do | What happens |
|---|---|---|
| 1 | `terraform apply` with defaults (`manage_vector_index = false`, `model_version = ""`) | Infra shell + job created; index/serving skipped |
| 2 | Run the job (`00`→`06`) at least once | Silver table + CDF created, index created via the notebook's own SDK call, a model version registered and evaluated |
| 3 | Set `manage_vector_index = true` and `model_version = "<version notebooks/04 printed>"`, re-apply (via PR, same as any other change) | Terraform manages the index and creates the Serving endpoint going forward |

## Deletion protection

`modules/rag_lab/main.tf` sets `lifecycle { prevent_destroy = true }` on the
catalog, schema, volume, and registered model — in **every** environment,
including dev, because they hold real ingested data and version history, not
just config. `terraform destroy` will refuse to remove them; that's intentional
friction, not a bug. To actually decommission an environment: comment out the
`prevent_destroy` blocks in a dedicated PR (so it's reviewed as a deliberate,
visible act), apply, then destroy — don't reach for `-target` or manual state
surgery as a shortcut.

## What's still manual after `terraform apply`

- **The Review App** (lab task 7): no Terraform resource exists for it. Run
  `notebooks/07_review_app_testing.py`, which calls `databricks.agents.deploy()` /
  `agents.set_permissions()` via the Python SDK.
- **Re-running ingestion** when source docs change: re-trigger the job; Terraform
  doesn't watch for upstream content changes.

## Destroying

```bash
terraform destroy
```

Won't touch data written by the notebooks that Terraform never created a resource
for (rows in Delta tables, MLflow run history) — only resources in Terraform
state, and even then not the deletion-protected ones (see above) until you
deliberately remove that protection.
