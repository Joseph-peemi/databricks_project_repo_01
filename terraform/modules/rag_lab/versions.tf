# =============================================================================
# versions.tf (module)
#
# A child module declares `required_providers` for documentation/constraint
# purposes but MUST NOT declare `provider` blocks with configuration --
# that's an anti-pattern (implicit or explicit provider passing belongs at
# the root). Auth, host, and subscription live in each
# environments/<env>/providers.tf instead. This is what lets the SAME module
# be planned against dev, staging, and prod with three different provider
# configurations without touching a single line in here.
# =============================================================================

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
