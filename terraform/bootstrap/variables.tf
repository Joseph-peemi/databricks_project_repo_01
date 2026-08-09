variable "azure_subscription_id" {
  type    = string
  default = ""
}

variable "resource_group_name" {
  type    = string
  default = "rg-terraform-state"
}

variable "location" {
  type    = string
  default = "eastus2"
}

variable "storage_account_name" {
  description = "Globally unique across ALL of Azure, 3-24 lowercase alphanumeric characters. Pick something org-specific -- 'sttfstateraglab' is a placeholder, not guaranteed available."
  type        = string
  default     = "sttfstateraglab"

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must be 3-24 lowercase letters/digits (Azure Storage naming rules)."
  }
}
