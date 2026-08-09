# =============================================================================
# variables.tf (environments/dev)
# Root-level variable declarations so `terraform.tfvars` has something to
# bind to; values are passed straight through to module "rag_lab" in
# main.tf. Defaults here (where present) are dev-appropriate: permissive,
# cheap, easy to tear down and recreate. Compare against
# environments/prod/variables.tf, which defaults the same variables toward
# stricter/safer choices.
# =============================================================================

variable "databricks_host" {
  description = "Dev Azure Databricks workspace URL."
  type        = string
  default     = ""
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

variable "provision_workspace" {
  type    = bool
  default = false
}

variable "enable_network_hardening" {
  description = "Defaults ON in prod -- VNet injection + No Public IP is treated as required, not optional. Set vnet_id/public_subnet_name/private_subnet_name in terraform.tfvars or `terraform plan` will fail with a clear precondition error rather than silently deploying a publicly-reachable prod workspace."
  type        = bool
  default     = true
}

variable "vnet_id" {
  type    = string
  default = ""
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
  type    = string
  default = ""
}

variable "bootstrap_unity_catalog" {
  type    = bool
  default = true
}

variable "catalog_name" {
  description = "Databricks best practice: one UC catalog per environment."
  type        = string
  default     = "prod"
}

variable "schema_name" {
  type    = string
  default = "rag_lab"
}

variable "volume_name" {
  type    = string
  default = "raw_docs"
}

variable "git_repo_url" {
  type    = string
  default = "https://github.com/Joseph-peemi/databricks_project_repo_01.git"
}

variable "git_provider" {
  type    = string
  default = "gitHub"
}

variable "git_branch" {
  description = "No default, deliberately -- prod must track an explicit release branch/tag (e.g. \"release/1.4.0\"), never whatever \"main\" happens to contain at apply time."
  type        = string
}

variable "workspace_repo_root" {
  type    = string
  default = "/Repos/rag-lab"
}

variable "num_workers" {
  type    = number
  default = 1 # prod runs a real (non single-node) job cluster
}

variable "run_as" {
  description = "Required -- no default. Set explicitly in terraform.tfvars."
  type        = string
}

variable "notification_emails" {
  type = list(string)
}

variable "reviewer_emails" {
  type = list(string)
}

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
  type    = bool
  default = false
}

variable "registered_model_name" {
  type    = string
  default = "databricks_docs_rag_model"
}

variable "serving_endpoint_name" {
  type    = string
  default = "databricks_docs_rag_endpoint"
}

variable "model_version" {
  type    = string
  default = ""
}

variable "workload_size" {
  type    = string
  default = "Medium"
}

variable "scale_to_zero_enabled" {
  type    = bool
  default = false # latency over cost in prod -- no cold starts on real user traffic
}

variable "create_pipeline_job" {
  type    = bool
  default = true
}

variable "job_name" {
  type    = string
  default = "rag-databricks-lab-pipeline"
}
