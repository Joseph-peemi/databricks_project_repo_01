# =============================================================================
# network.tf (module)
# Diagnostic logging for a Terraform-provisioned workspace -- ships
# notebook/cluster/Unity Catalog audit logs to Log Analytics. Strongly
# recommended for staging/prod: without this, "who ran what notebook and
# when" is only reconstructable from the Databricks account console's
# retention window, not queryable centrally with the rest of your org's logs.
#
# Only created when both provision_workspace = true AND a Log Analytics
# workspace ID is supplied -- this module does not create the Log Analytics
# workspace itself (that's shared org infrastructure, not lab-specific).
# =============================================================================

resource "azurerm_monitor_diagnostic_setting" "workspace" {
  count                      = var.provision_workspace && var.log_analytics_workspace_id != "" ? 1 : 0
  name                       = "rag-lab-${var.environment}-diagnostics"
  target_resource_id         = azurerm_databricks_workspace.this[0].id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "notebook"
  }
  enabled_log {
    category = "clusters"
  }
  enabled_log {
    category = "unityCatalog"
  }
  enabled_log {
    category = "jobs"
  }

  metric {
    category = "AllMetrics"
  }
}
