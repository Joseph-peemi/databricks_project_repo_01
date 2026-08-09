# =============================================================================
# azure_workspace.tf
# OPTIONAL. Only used when var.provision_workspace = true -- i.e. you want
# Terraform to stand up the Azure Databricks workspace itself, not just
# deploy the RAG pipeline into one that already exists.
#
# Most lab / Academy environments (like this one) already have a workspace
# provisioned for you -- leave provision_workspace at its default (false)
# and skip straight to main.tf. This file exists for teams that want a
# single `terraform apply` to go from a bare Azure subscription to a
# running pipeline.
#
# IMPORTANT Terraform limitation: provider blocks are evaluated before
# resources, so the `databricks` provider in main.tf cannot dynamically wait
# for `azurerm_databricks_workspace.this` to exist within a single apply.
# If provision_workspace = true, run in two passes:
#   1) terraform apply -target=azurerm_databricks_workspace.this
#   2) terraform apply   (now local.databricks_host resolves and the
#      databricks provider can authenticate against the new workspace)
# This is a well-known Terraform pattern for "provision the platform your
# own provider depends on" -- not a bug in this module.
# =============================================================================

resource "azurerm_resource_group" "this" {
  count    = var.provision_workspace ? 1 : 0
  name     = var.azure_resource_group_name
  location = var.azure_location
}

resource "azurerm_databricks_workspace" "this" {
  count               = var.provision_workspace ? 1 : 0
  name                = "dbw-rag-lab"
  resource_group_name = azurerm_resource_group.this[0].name
  location            = azurerm_resource_group.this[0].location
  sku                 = "premium" # Unity Catalog + Vector Search + Model Serving all require Premium

  # Confirms Unity Catalog can be attached: Premium tier + (if your org
  # requires it) a Vnet-injected workspace with the "no public IP" and
  # secure cluster connectivity settings your network team mandates.
  # Left as platform defaults here -- override via custom_parameters {}
  # if your Azure landing zone requires VNet injection.

  tags = {
    project = "rag-databricks-lab"
  }
}
