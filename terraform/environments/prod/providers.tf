terraform {
  required_version = ">= 1.7.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.55"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.azure_subscription_id != "" ? var.azure_subscription_id : null
}

provider "databricks" {
  host = var.databricks_host != "" ? var.databricks_host : null
  # Auth resolved via environment variables, never committed here:
  #   az login                                          (interactive/dev)
  #   ARM_CLIENT_ID / ARM_CLIENT_SECRET / ARM_TENANT_ID  (CI service principal)
  #   DATABRICKS_TOKEN                                    (PAT, least preferred)
}
