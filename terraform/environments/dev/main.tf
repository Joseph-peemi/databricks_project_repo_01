module "rag_lab" {
  source = "../../modules/rag_lab"

  environment = "dev"
  owner       = "peemijoe9522@gmail.com"

  provision_workspace       = var.provision_workspace
  azure_subscription_id     = var.azure_subscription_id
  azure_resource_group_name = var.azure_resource_group_name
  azure_location            = var.azure_location

  enable_network_hardening          = var.enable_network_hardening
  vnet_id                           = var.vnet_id
  public_subnet_name                = var.public_subnet_name
  public_subnet_nsg_association_id  = var.public_subnet_nsg_association_id
  private_subnet_name               = var.private_subnet_name
  private_subnet_nsg_association_id = var.private_subnet_nsg_association_id
  log_analytics_workspace_id        = var.log_analytics_workspace_id

  bootstrap_unity_catalog = var.bootstrap_unity_catalog
  catalog_name            = var.catalog_name
  schema_name             = var.schema_name
  volume_name             = var.volume_name

  git_repo_url        = var.git_repo_url
  git_provider        = var.git_provider
  git_branch          = var.git_branch
  workspace_repo_root = var.workspace_repo_root

  num_workers         = var.num_workers
  run_as              = var.run_as
  notification_emails = var.notification_emails
  reviewer_emails     = var.reviewer_emails

  vector_search_endpoint_name = var.vector_search_endpoint_name
  vector_index_name           = var.vector_index_name
  chunked_docs_table          = var.chunked_docs_table
  embedding_model_endpoint    = var.embedding_model_endpoint
  manage_vector_index         = var.manage_vector_index

  registered_model_name = var.registered_model_name
  serving_endpoint_name = var.serving_endpoint_name
  model_version         = var.model_version
  workload_size         = var.workload_size
  scale_to_zero_enabled = var.scale_to_zero_enabled

  create_pipeline_job = var.create_pipeline_job
  job_name            = var.job_name
}
