# =============================================================================
# outputs.tf (module)
# =============================================================================

output "workspace_url" {
  description = "Only non-null when provision_workspace = true; otherwise the calling environment already knows its own databricks_host."
  value       = var.provision_workspace ? azurerm_databricks_workspace.this[0].workspace_url : null
}

output "repo_path" {
  value = databricks_repo.rag_lab.path
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
  value = var.create_pipeline_job ? databricks_job.rag_pipeline[0].id : null
}

output "serving_endpoint_name" {
  description = "null until var.model_version is set and the second-phase apply runs."
  value       = var.model_version != "" ? databricks_model_serving.rag_endpoint[0].name : null
}
