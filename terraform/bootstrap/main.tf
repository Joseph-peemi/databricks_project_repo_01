# =============================================================================
# bootstrap/main.tf
#
# Creates the Azure Storage account that holds Terraform remote state for
# every environment (dev/staging/prod). This is the one piece of the whole
# module that CANNOT use a remote backend itself -- you can't store the
# state for "the thing that creates remote state storage" in that same
# storage account before it exists. Chicken-and-egg; every org's Terraform
# setup has exactly one bootstrap module like this with local state.
#
# Run this ONCE per Azure subscription (not per environment), by a platform
# admin, and treat its local terraform.tfstate file itself as precious --
# back it up (git-encrypted, or a separate protected storage location)
# since losing it means losing track of the storage account Terraform
# resource without losing the storage account itself.
#
# Usage:
#   cd bootstrap
#   terraform init
#   terraform apply
#   terraform output -raw backend_config > ../environments/dev/backend.hcl
#   # repeat the copy (with key= adjusted) for staging/prod
# =============================================================================

terraform {
  required_version = ">= 1.7.0"

  required_providers {
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

resource "azurerm_resource_group" "tfstate" {
  name     = var.resource_group_name
  location = var.location

  tags = {
    project    = "rag-databricks-lab"
    purpose    = "terraform-remote-state"
    managed_by = "terraform-bootstrap"
  }
}

resource "azurerm_storage_account" "tfstate" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.tfstate.name
  location                 = azurerm_resource_group.tfstate.location
  account_tier             = "Standard"
  account_replication_type = "GRS" # geo-redundant: state loss from a regional outage is not an acceptable risk

  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  blob_properties {
    versioning_enabled = true # every state write keeps prior versions -- your undo button for a bad apply

    delete_retention_policy {
      days = 30
    }
    container_delete_retention_policy {
      days = 30
    }
  }

  tags = {
    project    = "rag-databricks-lab"
    purpose    = "terraform-remote-state"
    managed_by = "terraform-bootstrap"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "tfstate" {
  name                 = "tfstate"
  storage_account_name = azurerm_storage_account.tfstate.name
  # Note: default is "container_access_type = private", i.e. no anonymous
  # access. State files can contain sensitive values (e.g. a
  # DATABRICKS_TOKEN if one was ever passed as a variable rather than an
  # env var) -- keep this container private, full stop.
}
