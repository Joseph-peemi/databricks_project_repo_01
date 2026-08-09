# Partial backend config -- deliberately no values here. Real values (which
# storage account, which state key) live in an untracked backend.hcl, kept
# separate from versioned code because they're subscription/account-specific
# and you don't want switching Azure subscriptions to require a code change
# and PR. Initialize with:
#
#   cp backend.hcl.example backend.hcl   # then fill in real values
#   terraform init -backend-config=backend.hcl
#
# See ../../bootstrap for how the storage account itself gets created.

terraform {
  backend "azurerm" {}
}
