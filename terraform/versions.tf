# =============================================================================
# versions.tf
# Pins Terraform core and provider versions. Pin these deliberately: the
# Databricks provider's schema for newer resources (vector search, model
# serving) has changed across minor versions -- an unpinned "~> 1.0" can
# silently break `terraform plan` on `apply` day.
# =============================================================================

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.55"
    }
    # Only needed if you set provision_workspace = true (see azure_workspace.tf)
    # to have Terraform create the Azure Databricks workspace itself, rather
    # than deploying into an existing one.
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }
}
