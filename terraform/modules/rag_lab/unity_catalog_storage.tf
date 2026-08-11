# =============================================================================
# unity_catalog_storage.tf (module)
#
# OPTIONAL, tied to var.bootstrap_unity_catalog. Provisions the ADLS Gen2
# container + Databricks Access Connector that back this environment's
# catalog. Needed because a freshly created Azure Databricks account has a
# metastore but no default storage root configured -- databricks_catalog
# fails with "Metastore storage root URL does not exist" unless the catalog
# specifies its own storage_root under a registered external location.
# =============================================================================

locals {
  uc_resource_group_name = var.provision_workspace ? azurerm_resource_group.this[0].name : var.azure_resource_group_name
  uc_location             = var.provision_workspace ? azurerm_resource_group.this[0].location : var.azure_location
  uc_container_name       = "unity-catalog"
}

resource "azurerm_storage_account" "unity_catalog" {
  count                    = var.bootstrap_unity_catalog ? 1 : 0
  name                     = "stuc${var.environment}raglab"
  resource_group_name      = local.uc_resource_group_name
  location                 = local.uc_location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"
  is_hns_enabled           = true # required for ADLS Gen2 / Unity Catalog managed storage

  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  tags = local.common_tags
}

resource "azurerm_storage_container" "unity_catalog" {
  count                 = var.bootstrap_unity_catalog ? 1 : 0
  name                  = local.uc_container_name
  storage_account_name  = azurerm_storage_account.unity_catalog[0].name
}

resource "azurerm_databricks_access_connector" "unity_catalog" {
  count               = var.bootstrap_unity_catalog ? 1 : 0
  name                = "access-connector-rag-lab-${var.environment}"
  resource_group_name = local.uc_resource_group_name
  location             = local.uc_location
  tags                 = local.common_tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_role_assignment" "unity_catalog_storage" {
  count                = var.bootstrap_unity_catalog ? 1 : 0
  scope                = azurerm_storage_account.unity_catalog[0].id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_databricks_access_connector.unity_catalog[0].identity[0].principal_id
}

resource "databricks_storage_credential" "unity_catalog" {
  count = var.bootstrap_unity_catalog ? 1 : 0
  name  = "uc-storage-credential-${var.environment}"

  azure_managed_identity {
    access_connector_id = azurerm_databricks_access_connector.unity_catalog[0].id
  }

  depends_on = [azurerm_role_assignment.unity_catalog_storage]
}

resource "databricks_external_location" "unity_catalog" {
  count           = var.bootstrap_unity_catalog ? 1 : 0
  name            = "uc-external-location-${var.environment}"
  url             = "abfss://${local.uc_container_name}@${azurerm_storage_account.unity_catalog[0].name}.dfs.core.windows.net/"
  credential_name = databricks_storage_credential.unity_catalog[0].name
}
