# =============================================================================
# main.tf
# Terraform module for the Azure Databricks RAG lab infrastructure.
#
# What this module DOES manage declaratively:
#   - Unity Catalog catalog / schema / volume
#   - The Databricks Repo (git checkout of this project into the workspace)
#   - The Vector Search endpoint (compute layer)
#   - The Vector Search index itself (opt-in, second-phase -- see variables.tf)
#   - A Unity Catalog registered-model "container" (no version yet)
#   - A Databricks Job that runs notebooks 00-06 in sequence
#   - A Model Serving endpoint (opt-in, second-phase, once a model version exists)
#   - Permissions/grants across all of the above
#
# What this module does NOT and CANNOT manage (these are runtime/code
# actions, not declarative infrastructure -- see the notebooks/ + src/ code):
#   - Scraping/chunking documents and populating the Delta tables
#   - Logging + registering an actual MLflow model VERSION
#   - Running evaluation and deciding whether to promote an alias
#   - Provisioning the Review App (no Terraform resource exists for it --
#     it's created by the `databricks.agents.deploy()` Python SDK call in
#     notebooks/06_deploy_model.py)
# =============================================================================

locals {
  databricks_host = var.provision_workspace ? azurerm_databricks_workspace.this[0].workspace_url : var.databricks_host

  full_chunked_table   = "${var.catalog_name}.${var.schema_name}.${var.chunked_docs_table}"
  full_index_name      = "${var.catalog_name}.${var.schema_name}.${var.vector_index_name}"
  full_registered_name = "${var.catalog_name}.${var.schema_name}.${var.registered_model_name}"
  repo_path            = "${var.workspace_repo_root}/rag-databricks-lab"
}

provider "azurerm" {
  features {}
  subscription_id = var.azure_subscription_id != "" ? var.azure_subscription_id : null
}

provider "databricks" {
  host = local.databricks_host != "" ? local.databricks_host : null
  # Auth is intentionally NOT hardcoded here. Resolve it via environment
  # variables, in order of preference for an Azure workspace:
  #   - Azure CLI (simplest for interactive use):  `az login`, then just
  #     set ARM_TENANT_ID / ARM_SUBSCRIPTION_ID if you have multiple tenants.
  #   - Service principal (CI/CD):  ARM_CLIENT_ID, ARM_CLIENT_SECRET,
  #     ARM_TENANT_ID, plus DATABRICKS_AZURE_RESOURCE_ID for the workspace.
  #   - Databricks PAT (quick/lab use only, least preferred): DATABRICKS_TOKEN.
  # Never put a token or client secret in this file or in terraform.tfvars.
}

# --- Cloud-agnostic lookups: resolves to a valid Azure SKU/runtime automatically ---
data "databricks_spark_version" "ml_lts" {
  long_term_support = true
  ml                = true
}

data "databricks_node_type" "smallest" {
  local_disk = true
  # On Azure this resolves to something like Standard_DS3_v2. Override with
  # an explicit node_type_id in the job_cluster block below if your
  # subscription's quota requires a specific family (e.g. Standard_D4ds_v5).
}

# =============================================================================
# Unity Catalog: catalog / schema / volume
# =============================================================================

resource "databricks_catalog" "rag_lab" {
  count   = var.bootstrap_unity_catalog ? 1 : 0
  name    = var.catalog_name
  comment = "RAG lab catalog: Databricks documentation Q&A pipeline"

  properties = {
    purpose = "rag-databricks-lab"
  }
}

resource "databricks_schema" "rag_lab" {
  count        = var.bootstrap_unity_catalog ? 1 : 0
  catalog_name = var.catalog_name
  name         = var.schema_name
  comment      = "Tables, vector index, and registered model for the RAG lab"
  depends_on   = [databricks_catalog.rag_lab]
}

resource "databricks_volume" "raw_docs" {
  count        = var.bootstrap_unity_catalog ? 1 : 0
  catalog_name = var.catalog_name
  schema_name  = var.schema_name
  name         = var.volume_name
  volume_type  = "MANAGED"
  comment      = "Landing zone for scraped Databricks documentation (notebooks/01)"
  depends_on   = [databricks_schema.rag_lab]
}

# =============================================================================
# Git integration: check this project out into the workspace
# =============================================================================

resource "databricks_repo" "rag_lab" {
  url          = var.git_repo_url
  git_provider = var.git_provider
  branch       = var.git_branch
  path         = local.repo_path
}

# =============================================================================
# Vector Search
# =============================================================================

resource "databricks_vector_search_endpoint" "rag_lab" {
  name          = var.vector_search_endpoint_name
  endpoint_type = "STANDARD"
}

# Second-phase resource: the source Delta table (main.rag_lab.databricks_docs_chunked)
# must already exist WITH Change Data Feed enabled before this can be created
# -- that happens at runtime in notebooks/01, not via Terraform. Run the
# pipeline job below once with manage_vector_index = false, THEN flip the
# variable to true and re-apply so Terraform adopts ownership of the index.
resource "databricks_vector_search_index" "rag_lab" {
  count         = var.manage_vector_index ? 1 : 0
  name          = local.full_index_name
  endpoint_name = databricks_vector_search_endpoint.rag_lab.name
  primary_key   = "chunk_id"
  index_type    = "DELTA_SYNC"

  delta_sync_index_spec {
    source_table  = local.full_chunked_table
    pipeline_type = "TRIGGERED"

    embedding_source_columns {
      name                          = "chunk_text"
      embedding_model_endpoint_name = var.embedding_model_endpoint
    }
  }
}

# =============================================================================
# Unity Catalog registered model (empty container -- notebooks/04 logs the
# actual versions into this via MLflow). Pre-creating it here means access
# control on the model is Terraform-managed from day one instead of
# whatever the first `mlflow.register_model()` caller happens to grant.
# =============================================================================

resource "databricks_registered_model" "rag_model" {
  catalog_name = var.catalog_name
  schema_name  = var.schema_name
  name         = var.registered_model_name
  comment      = "RAG chain over Databricks documentation"
  depends_on   = [databricks_schema.rag_lab]
}

# =============================================================================
# Job: orchestrates notebooks 00-06 end to end.
# Task 07 (Review App testing) is intentionally NOT included -- it's a
# manual/human step, not something you'd want on a schedule.
# =============================================================================

resource "databricks_job" "rag_pipeline" {
  count = var.create_pipeline_job ? 1 : 0
  name  = var.job_name

  job_cluster {
    job_cluster_key = "rag_cluster"
    new_cluster {
      spark_version      = data.databricks_spark_version.ml_lts.id
      node_type_id       = data.databricks_node_type.smallest.id
      num_workers        = var.num_workers
      data_security_mode = "SINGLE_USER"
      single_user_name   = var.run_as
    }
  }

  dynamic "task" {
    for_each = {
      "00_environment_setup"           = []
      "01_data_ingestion_and_chunking" = ["00_environment_setup"]
      "02_create_vector_search_index"  = ["01_data_ingestion_and_chunking"]
      "03_build_rag_pipeline"          = ["02_create_vector_search_index"]
      "04_register_model"              = ["03_build_rag_pipeline"]
      "05_evaluate_model"              = ["04_register_model"]
      "06_deploy_model"                = ["05_evaluate_model"]
    }
    content {
      task_key        = task.key
      job_cluster_key = "rag_cluster"

      notebook_task {
        notebook_path = "${local.repo_path}/notebooks/${task.key}"
      }

      dynamic "depends_on" {
        for_each = task.value
        content {
          task_key = depends_on.value
        }
      }
    }
  }

  email_notifications {
    on_failure = var.notification_emails
  }

  run_as {
    user_name = var.run_as
  }

  depends_on = [
    databricks_repo.rag_lab,
    databricks_vector_search_endpoint.rag_lab,
    databricks_registered_model.rag_model,
  ]
}

# =============================================================================
# Model Serving endpoint -- second-phase resource, same reasoning as the
# vector search index: needs a model VERSION to exist first (notebooks/04),
# which needs to have passed evaluation (notebooks/05). Leave
# var.model_version = "" until you have one; Terraform skips this resource
# until then.
# =============================================================================

resource "databricks_model_serving" "rag_endpoint" {
  count = var.model_version != "" ? 1 : 0
  name  = var.serving_endpoint_name

  config {
    served_entities {
      name                  = "${var.registered_model_name}-${var.model_version}"
      entity_name           = local.full_registered_name
      entity_version        = var.model_version
      workload_size         = var.workload_size
      scale_to_zero_enabled = var.scale_to_zero_enabled
    }

    auto_capture_config {
      catalog_name      = var.catalog_name
      schema_name       = var.schema_name
      table_name_prefix = "rag_endpoint_logs"
    }
  }

  depends_on = [databricks_registered_model.rag_model]
}

# =============================================================================
# Permissions
# =============================================================================

resource "databricks_grants" "catalog" {
  count   = var.bootstrap_unity_catalog ? 1 : 0
  catalog = var.catalog_name

  dynamic "grant" {
    for_each = var.reviewer_emails
    content {
      principal  = grant.value
      privileges = ["USE_CATALOG", "USE_SCHEMA"]
    }
  }

  depends_on = [databricks_catalog.rag_lab]
}

resource "databricks_permissions" "job" {
  count  = var.create_pipeline_job ? 1 : 0
  job_id = databricks_job.rag_pipeline[0].id

  dynamic "access_control" {
    for_each = var.reviewer_emails
    content {
      user_name        = access_control.value
      permission_level = "CAN_VIEW"
    }
  }
}

resource "databricks_permissions" "serving_endpoint" {
  count               = var.model_version != "" ? 1 : 0
  serving_endpoint_id = databricks_model_serving.rag_endpoint[0].serving_endpoint_id

  dynamic "access_control" {
    for_each = var.reviewer_emails
    content {
      user_name        = access_control.value
      permission_level = "CAN_QUERY"
    }
  }
}
