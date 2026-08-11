# =============================================================================
# variables.tf (module)
# Every variable that used to default to a personal email or a bare string
# now either has NO default (forces the calling environment root to supply
# it explicitly) or a validation block (fails `terraform plan` with a clear
# message instead of surfacing as a confusing Databricks API error).
# =============================================================================

# --- Environment identity ------------------------------------------------------------
variable "environment" {
  description = "Deployment environment. Drives resource naming/tagging and safe defaults."
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be exactly one of: dev, staging, prod."
  }
}

variable "owner" {
  description = "Team or individual accountable for this environment's spend/incidents (used in tags, not a Databricks concept)."
  type        = string
}

# --- Azure Databricks workspace connection --------------------------------------
variable "provision_workspace" {
  description = "If true, this module also creates the Azure Databricks workspace itself (see azure_workspace.tf). Requires the two-phase apply documented in the root README."
  type        = bool
  default     = false
}

variable "azure_subscription_id" {
  type    = string
  default = ""
}

variable "azure_resource_group_name" {
  type    = string
  default = ""
}

variable "azure_location" {
  type    = string
  default = "eastus2"
}

# --- Network hardening (optional, off by default so the lab path stays simple) -----
variable "enable_network_hardening" {
  description = "Enable VNet injection + Secure Cluster Connectivity (No Public IP) on a Terraform-provisioned workspace. Only applies when provision_workspace = true. REQUIRED for staging/prod in most Azure landing zones."
  type        = bool
  default     = false
}

variable "vnet_id" {
  description = "Existing VNet resource ID to inject the workspace into. Required when enable_network_hardening = true."
  type        = string
  default     = ""
}

variable "public_subnet_name" {
  type    = string
  default = ""
}

variable "public_subnet_nsg_association_id" {
  type    = string
  default = ""
}

variable "private_subnet_name" {
  type    = string
  default = ""
}

variable "private_subnet_nsg_association_id" {
  type    = string
  default = ""
}

variable "log_analytics_workspace_id" {
  description = "If set, workspace diagnostic logs (notebook, cluster, UC audit) stream here. Strongly recommended for staging/prod."
  type        = string
  default     = ""
}

# --- Unity Catalog ------------------------------------------------------------------
variable "bootstrap_unity_catalog" {
  description = "Create the catalog/schema/volume via Terraform. Set false if a platform team owns catalog creation and grants USE/CREATE instead."
  type        = bool
  default     = true
}

variable "catalog_name" {
  description = "Unity Catalog catalog name. Databricks best practice: one catalog PER ENVIRONMENT (e.g. 'dev', 'staging', 'prod'), not a shared 'main' catalog with environment-suffixed schemas."
  type        = string
}

variable "schema_name" {
  type    = string
  default = "rag_lab"
}

variable "volume_name" {
  type    = string
  default = "raw_docs"
}

# --- Git / Repo --------------------------------------------------------------------
variable "git_repo_url" {
  type    = string
  default = "https://github.com/Joseph-peemi/databricks_project_repo_01.git"
}

variable "git_provider" {
  type    = string
  default = "gitHub"
  validation {
    condition     = contains(["gitHub", "gitHubEnterprise", "azureDevOpsServices", "gitLab", "gitLabEnterpriseEdition", "bitbucketCloud", "bitbucketServer"], var.git_provider)
    error_message = "git_provider must be a value the Databricks Repos API recognizes (e.g. gitHub, azureDevOpsServices, gitLab)."
  }
}

variable "git_branch" {
  description = "Branch this environment tracks. Convention: dev -> a feature/develop branch, staging -> main, prod -> a tagged release branch."
  type        = string
}

variable "workspace_repo_root" {
  type    = string
  default = "/Repos/rag-lab"
}

# --- Compute -------------------------------------------------------------------------
variable "num_workers" {
  type    = number
  default = 0
  validation {
    condition     = var.num_workers >= 0
    error_message = "num_workers must be >= 0."
  }
}

variable "node_type_id" {
  description = "Override the job cluster's VM SKU. Leave empty to auto-pick the smallest node type with local disk -- set explicitly when that auto-picked family (often a confidential-computing SKU) has 0 quota on the subscription."
  type        = string
  default     = ""
}

variable "run_as" {
  description = "User email or service principal application ID the job runs as. No default -- every environment must set this explicitly (a job silently running as whoever happened to `terraform apply` first is a production incident waiting to happen)."
  type        = string
}

variable "notification_emails" {
  type = list(string)
}

# --- Vector Search ---------------------------------------------------------------------
variable "vector_search_endpoint_name" {
  type    = string
  default = "rag_lab_vs_endpoint"
}

variable "vector_index_name" {
  type    = string
  default = "databricks_docs_index"
}

variable "chunked_docs_table" {
  type    = string
  default = "databricks_docs_chunked"
}

variable "embedding_model_endpoint" {
  type    = string
  default = "databricks-bge-large-en"
}

variable "manage_vector_index" {
  description = "Second-phase toggle -- see root README 'Two-phase resources'."
  type        = bool
  default     = false
}

# --- Model registry / serving --------------------------------------------------------
variable "registered_model_name" {
  type    = string
  default = "databricks_docs_rag_model"
}

variable "serving_endpoint_name" {
  type    = string
  default = "databricks_docs_rag_endpoint"
}

variable "model_version" {
  description = "Second-phase toggle -- leave empty until a version has been registered and evaluated."
  type        = string
  default     = ""
  validation {
    condition     = var.model_version == "" || can(regex("^[0-9]+$", var.model_version))
    error_message = "model_version must be empty, or a plain positive integer matching a real UC model version (e.g. \"3\") -- not an alias like \"champion\"."
  }
}

variable "workload_size" {
  type    = string
  default = "Small"
  validation {
    condition     = contains(["Small", "Medium", "Large"], var.workload_size)
    error_message = "workload_size must be one of: Small, Medium, Large."
  }
}

variable "scale_to_zero_enabled" {
  type    = bool
  default = true
}

# --- Job orchestration -----------------------------------------------------------------
variable "create_pipeline_job" {
  type    = bool
  default = true
}

variable "job_name" {
  type    = string
  default = "rag-databricks-lab-pipeline"
}

# --- Access control ------------------------------------------------------------------
variable "reviewer_emails" {
  description = "Emails granted CAN_VIEW on the job and CAN_QUERY on the serving endpoint."
  type        = list(string)
}
