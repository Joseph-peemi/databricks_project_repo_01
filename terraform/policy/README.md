# Policy / security scanning

`tfsec` runs on every PR via `.github/workflows/terraform.yml` (`static-checks`
job) with `soft_fail: false` — a finding blocks the merge, it isn't advisory.

If a finding is a deliberate, reviewed exception (not a bug), suppress it at the
resource with an inline comment rather than disabling the check globally, so the
justification travels with the code:

```hcl
resource "azurerm_storage_account" "tfstate" {
  # tfsec:ignore:azure-storage-default-action-deny -- justification here, and who approved it
  ...
}
```

Never add a blanket `.tfsecignore` or `soft_fail: true` to make a red build green
without fixing or explicitly justifying the finding — that defeats the point of
gating on it in the first place.

`tflint` (`.tflint.hcl` in this directory's parent) catches Terraform-specific
correctness issues (naming, unused variables, provider-specific argument
mistakes) that `terraform validate` doesn't — it checks style/convention, not
just syntax + schema.
