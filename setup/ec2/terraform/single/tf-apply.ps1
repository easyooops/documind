$ErrorActionPreference = "Stop"

if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
    throw "Terraform CLI is not installed or not on PATH. Install Terraform from https://developer.hashicorp.com/terraform/install. If your network intercepts TLS, install your organization root CA in the Windows trust store first."
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is not installed or not on PATH. Install Node.js 18+; Terraform uses it to stage a clean deployment archive."
}

terraform init
terraform apply
