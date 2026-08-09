# =============================================================================
# variables.tf
# Defaults here intentionally mirror config/config.yaml in the main project
# (catalog=main, schema=rag_lab, volume=raw_docs, etc.) so the Terraform-
# managed infra and the notebook code agree on names without extra wiring.
# If you change a name here, change it in config/config.yaml too.
# =============================================================================

# --- Azure Databricks workspace connection --------------------------------------
variable "provision_workspace" {
  description = <<-EOT
    If true, Terraform creates a NEW Azure Databricks workspace (resource
    group + azurerm_databricks_workspace, see azure_workspace.tf) and the
    databricks provider authenticates against it via Azure AD.
    If false (default), Terraform deploys INTO an existing workspace and you
    authenticate the databricks provider directly (host + token, or Azure CLI).
    Most lab / Academy environments already have a workspace provisioned for
    you -- leave this false unless you're standing up infra from scratch.
  EOT
  type        = bool
  default     = false
}

variable "databricks_host" {
  description = "Azure Databricks workspace URL, e.g. https://adb-1234567890123456.7.azuredatabricks.net. Required when provision_workspace = false."
  type        = string
  default     = ""
}

variable "azure_subscription_id" {
  description = "Azure subscription ID. Required when provision_workspace = true."
  type        = string
  default     = ""
}

variable "azure_resource_group_name" {
  description = "Resource group for the Databricks workspace (existing, or created when provision_workspace = true)."
  type        = string
  default     = "rg-rag-lab"
}

variable "azure_location" {
  description = "Azure region for new resources, e.g. eastus2, westeurope."
  type        = string
  default     = "eastus2"
}

# --- Unity Catalog ------------------------------------------------------------------
variable "bootstrap_unity_catalog" {
  description = "Create the catalog/schema/volume via Terraform. Set false if a platform team pre-creates main.rag_lab and grants you USE/CREATE instead."
  type        = bool
  default     = true
}

variable "catalog_name" {
  description = "Unity Catalog catalog name."
  type        = string
  default     = "main"
}

variable "schema_name" {
  description = "Unity Catalog schema name."
  type        = string
  default     = "rag_lab"
}

variable "volume_name" {
  description = "UC Volume name for landing raw Databricks documentation files."
  type        = string
  default     = "raw_docs"
}

# --- Git / Repo --------------------------------------------------------------------
variable "git_repo_url" {
  description = "HTTPS URL of the git repo containing this project."
  type        = string
  default     = "https://github.com/Joseph-peemi/databricks_project_repo_01.git"
}

variable "git_provider" {
  description = "Git provider for the Databricks Repo object: gitHub, azureDevOpsServices, gitLab, bitbucketCloud, etc."
  type        = string
  default     = "gitHub"
}

variable "git_branch" {
  description = "Branch to check out in the Databricks Repo."
  type        = string
  default     = "main"
}

variable "workspace_repo_root" {
  description = "Workspace path prefix the repo is checked out under."
  type        = string
  default     = "/Repos/rag-lab"
}

# --- Compute -------------------------------------------------------------------------
variable "num_workers" {
  description = "Worker count for the job cluster running the pipeline notebooks. 0 = single-node cluster, fine for a lab-scale corpus."
  type        = number
  default     = 0
}

variable "run_as" {
  description = "User (email) or service principal application ID the job runs as. Defaults to the account running `terraform apply` if left empty."
  type        = string
  default     = "peemijoe9522@gmail.com"
}

variable "notification_emails" {
  description = "Emails notified on job failure."
  type        = list(string)
  default     = ["peemijoe9522@gmail.com"]
}

# --- Vector Search ---------------------------------------------------------------------
variable "vector_search_endpoint_name" {
  type    = string
  default = "rag_lab_vs_endpoint"
}

variable "vector_index_name" {
  description = "Unqualified index name (will be namespaced as catalog.schema.<this>)."
  type        = string
  default     = "databricks_docs_index"
}

variable "chunked_docs_table" {
  description = "Unqualified Silver table name the index syncs from (must already exist with Change Data Feed enabled -- created by notebooks/01)."
  type        = string
  default     = "databricks_docs_chunked"
}

variable "embedding_model_endpoint" {
  type    = string
  default = "databricks-bge-large-en"
}

variable "manage_vector_index" {
  description = <<-EOT
    Whether Terraform should own the databricks_vector_search_index resource.
    Keep false on first apply -- the source Delta table doesn't exist until
    notebooks/01 has run at least once. After the pipeline job (below) has
    run successfully one time, set this to true and re-apply so Terraform
    adopts/manages the index going forward.
  EOT
  type        = bool
  default     = false
}

# --- Model registry / serving --------------------------------------------------------
variable "registered_model_name" {
  description = "Unqualified UC registered model name."
  type        = string
  default     = "databricks_docs_rag_model"
}

variable "serving_endpoint_name" {
  type    = string
  default = "databricks_docs_rag_endpoint"
}

variable "model_version" {
  description = <<-EOT
    Version number of the registered model to serve. Leave "" on first apply
    (Terraform will skip creating the serving endpoint). After notebooks/04
    has registered a version and it has passed evaluation (notebooks/05),
    set this to that version number and re-apply to create/update the
    serving endpoint via Terraform.
  EOT
  type        = string
  default     = ""
}

variable "workload_size" {
  description = "Small | Medium | Large"
  type        = string
  default     = "Small"
}

variable "scale_to_zero_enabled" {
  type    = bool
  default = true
}

# --- Job orchestration -----------------------------------------------------------------
variable "create_pipeline_job" {
  description = "Create a Databricks Job that runs notebooks 00-06 in sequence (task 07, the Review App, stays a manual/UI step)."
  type        = bool
  default     = true
}

variable "job_name" {
  type    = string
  default = "rag-databricks-lab-pipeline"
}

# --- Access control ------------------------------------------------------------------
variable "reviewer_emails" {
  description = "Emails granted CAN_VIEW on the job and CAN_QUERY on the serving endpoint for Review App / stakeholder testing."
  type        = list(string)
  default     = ["peemijoe9522@gmail.com"]
}
