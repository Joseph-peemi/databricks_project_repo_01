# =============================================================================
# azure_workspace.tf (module)
# OPTIONAL. Only used when var.provision_workspace = true.
#
# IMPORTANT Terraform limitation, unchanged from the flat module: provider
# blocks are evaluated before resources, so if an environment's own
# `provider "databricks"` block points at this not-yet-created workspace,
# that environment needs a two-phase apply:
#   1) terraform apply -target=module.rag_lab.azurerm_databricks_workspace.this
#   2) terraform apply
# Most environments will instead set provision_workspace = false and deploy
# into a workspace a platform team already stood up -- read
# environments/<env>/terraform.tfvars.example before assuming you need this.
# =============================================================================

resource "azurerm_resource_group" "this" {
  count    = var.provision_workspace ? 1 : 0
  name     = var.azure_resource_group_name
  location = var.azure_location
  tags     = local.common_tags
}

resource "azurerm_databricks_workspace" "this" {
  count               = var.provision_workspace ? 1 : 0
  name                = "dbw-rag-lab-${var.environment}"
  resource_group_name = azurerm_resource_group.this[0].name
  location            = azurerm_resource_group.this[0].location
  sku                 = "premium" # Unity Catalog + Vector Search + Model Serving all require Premium
  tags                = local.common_tags

  # Network hardening: VNet injection + Secure Cluster Connectivity (No
  # Public IP). Off by default so the lab path stays simple; treat this as
  # REQUIRED before staging/prod in any Azure landing zone with network
  # policy requirements (most enterprise subscriptions have one).
  dynamic "custom_parameters" {
    for_each = var.enable_network_hardening ? [1] : []
    content {
      no_public_ip                                         = true
      virtual_network_id                                   = var.vnet_id
      public_subnet_name                                   = var.public_subnet_name
      public_subnet_network_security_group_association_id  = var.public_subnet_nsg_association_id
      private_subnet_name                                  = var.private_subnet_name
      private_subnet_network_security_group_association_id = var.private_subnet_nsg_association_id
    }
  }

  lifecycle {
    precondition {
      condition = !var.enable_network_hardening || (
        var.vnet_id != "" && var.public_subnet_name != "" && var.private_subnet_name != ""
      )
      error_message = "enable_network_hardening = true requires vnet_id, public_subnet_name, and private_subnet_name to all be set."
    }
  }
}
