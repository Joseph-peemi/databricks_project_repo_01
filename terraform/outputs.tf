# =============================================================================
# outputs.tf
# =============================================================================

output "databricks_host" {
  description = "Workspace URL Terraform ended up authenticating against."
  value       = local.databricks_host
}

output "repo_path" {
  description = "Workspace path the project was checked out to -- feed this into notebook sys.path assumptions if you move it."
  value       = databricks_repo.rag_lab.path
}

output "catalog_schema" {
  value = "${var.catalog_name}.${var.schema_name}"
}

output "vector_search_endpoint_name" {
  value = databricks_vector_search_endpoint.rag_lab.name
}

output "vector_index_full_name" {
  value = local.full_index_name
}

output "registered_model_full_name" {
  value = local.full_registered_name
}

output "pipeline_job_id" {
  description = "Run this job (via UI or `databricks jobs run-now`) to execute notebooks 00-06 end to end."
  value       = var.create_pipeline_job ? databricks_job.rag_pipeline[0].id : null
}

output "pipeline_job_url" {
  value = var.create_pipeline_job ? "${local.databricks_host}/jobs/${databricks_job.rag_pipeline[0].id}" : null
}

output "serving_endpoint_name" {
  description = "null until var.model_version is set and the second-phase apply runs."
  value       = var.model_version != "" ? databricks_model_serving.rag_endpoint[0].name : null
}

output "next_steps" {
  value = var.model_version == "" ? (
    "Serving endpoint not yet created. Run the pipeline job, note the version notebooks/04 registers, set model_version in terraform.tfvars, then re-run `terraform apply`."
  ) : "Serving endpoint '${var.serving_endpoint_name}' managed. Run notebooks/07_review_app_testing.py manually to provision/test the Review App (no Terraform resource exists for it)."
}
