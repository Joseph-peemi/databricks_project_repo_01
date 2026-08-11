# =============================================================================
# main.tf (module: rag_lab)
#
# Reusable module instantiated once per environment (see
# ../../environments/{dev,staging,prod}/main.tf). No provider blocks here --
# see versions.tf for why. Every environment root passes its own
# provider config, backend, and tfvars, so the SAME reviewed module code
# runs in dev, staging, and prod; only inputs differ.
#
# What this module DOES manage declaratively:
#   - Unity Catalog catalog / schema / volume (deletion-protected)
#   - The Databricks Repo (git checkout of this project into the workspace)
#   - The Vector Search endpoint (compute layer)
#   - The Vector Search index itself (opt-in, second-phase)
#   - A Unity Catalog registered-model "container" (deletion-protected, no version yet)
#   - A Databricks Job that runs notebooks 00-06 in sequence
#   - A Model Serving endpoint (opt-in, second-phase)
#   - Permissions/grants across all of the above
#
# What this module does NOT and CANNOT manage (runtime/code actions, not
# declarative infra -- see ../../../notebooks + ../../../src):
#   - Scraping/chunking documents and populating the Delta tables
#   - Logging + registering an actual MLflow model VERSION
#   - Running evaluation and deciding whether to promote an alias
#   - Provisioning the Review App (no Terraform resource exists for it)
# =============================================================================

locals {
  full_chunked_table   = "${var.catalog_name}.${var.schema_name}.${var.chunked_docs_table}"
  full_index_name      = "${var.catalog_name}.${var.schema_name}.${var.vector_index_name}"
  full_registered_name = "${var.catalog_name}.${var.schema_name}.${var.registered_model_name}"
  repo_path            = "${var.workspace_repo_root}/rag-databricks-lab"

  # Every environment-facing resource name carries the environment suffix so
  # dev/staging/prod can coexist safely even if they ever land in the same
  # workspace (not recommended long-term, but a real transitional state most
  # orgs pass through).
  vs_endpoint_name      = "${var.vector_search_endpoint_name}_${var.environment}"
  job_name              = "${var.job_name}-${var.environment}"
  serving_endpoint_name = "${var.serving_endpoint_name}_${var.environment}"

  common_tags = {
    environment = var.environment
    project     = "rag-databricks-lab"
    owner       = var.owner
    managed_by  = "terraform"
  }
}

data "databricks_spark_version" "ml_lts" {
  long_term_support = true
  ml                = true
}

data "databricks_node_type" "smallest" {
  local_disk = true
}

# =============================================================================
# Unity Catalog: catalog / schema / volume
#
# prevent_destroy is intentionally UNCONDITIONAL (Terraform's lifecycle
# meta-arguments only accept literal booleans, never a variable), and
# intentionally applies in every environment including dev: these resources
# hold real ingested documentation data. Tearing one down requires a human
# to deliberately delete this block (or `terraform state rm` + manual
# deletion in the UI) -- friction is the point.
# =============================================================================

resource "databricks_catalog" "rag_lab" {
  count        = var.bootstrap_unity_catalog ? 1 : 0
  name         = var.catalog_name
  comment      = "RAG lab catalog (${var.environment}): Databricks documentation Q&A pipeline"
  storage_root = "${databricks_external_location.unity_catalog[0].url}${var.catalog_name}/"

  properties = local.common_tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "databricks_schema" "rag_lab" {
  count        = var.bootstrap_unity_catalog ? 1 : 0
  catalog_name = var.catalog_name
  name         = var.schema_name
  comment      = "Tables, vector index, and registered model for the RAG lab (${var.environment})"
  depends_on   = [databricks_catalog.rag_lab]

  lifecycle {
    prevent_destroy = true
  }
}

resource "databricks_volume" "raw_docs" {
  count        = var.bootstrap_unity_catalog ? 1 : 0
  catalog_name = var.catalog_name
  schema_name  = var.schema_name
  name         = var.volume_name
  volume_type  = "MANAGED"
  comment      = "Landing zone for scraped Databricks documentation (notebooks/01)"
  depends_on   = [databricks_schema.rag_lab]

  lifecycle {
    prevent_destroy = true
  }
}

# =============================================================================
# Git integration
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
  name          = local.vs_endpoint_name
  endpoint_type = "STANDARD"
}

# Second-phase resource -- see root README "Two-phase resources".
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
# Unity Catalog registered model (empty container; notebooks/04 logs actual
# versions into it via MLflow). Deletion-protected -- losing this loses the
# entire version history and audit trail, not just a config object.
# =============================================================================

resource "databricks_registered_model" "rag_model" {
  catalog_name = var.catalog_name
  schema_name  = var.schema_name
  name         = var.registered_model_name
  comment      = "RAG chain over Databricks documentation (${var.environment})"
  depends_on   = [databricks_schema.rag_lab]

  lifecycle {
    prevent_destroy = true
  }
}

# =============================================================================
# Job: orchestrates notebooks 00-06 end to end. Task 07 (Review App testing)
# is intentionally excluded -- it's a manual/human step.
# =============================================================================

resource "databricks_job" "rag_pipeline" {
  count = var.create_pipeline_job ? 1 : 0
  name  = local.job_name
  tags  = local.common_tags

  job_cluster {
    job_cluster_key = "rag_cluster"
    new_cluster {
      spark_version      = data.databricks_spark_version.ml_lts.id
      node_type_id       = var.node_type_id != "" ? var.node_type_id : data.databricks_node_type.smallest.id
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
# Model Serving endpoint -- second-phase resource, see root README.
# =============================================================================

resource "databricks_model_serving" "rag_endpoint" {
  count = var.model_version != "" ? 1 : 0
  name  = local.serving_endpoint_name

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

  tags {
    key   = "environment"
    value = var.environment
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

  # The job's run_as user must always keep IS_OWNER -- Databricks rejects a
  # permissions update that would strip management access from the creator.
  # This can otherwise collide with the reviewer_emails loop below when
  # run_as also appears in reviewer_emails (a real, expected overlap for a
  # single-person dev environment).
  access_control {
    user_name        = var.run_as
    permission_level = "IS_OWNER"
  }

  dynamic "access_control" {
    for_each = toset([for email in var.reviewer_emails : email if email != var.run_as])
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
