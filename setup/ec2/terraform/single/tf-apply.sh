#!/usr/bin/env bash
set -euo pipefail

if ! command -v terraform >/dev/null 2>&1; then
  echo "Terraform CLI is not installed or not on PATH." >&2
  echo "Install Terraform from https://developer.hashicorp.com/terraform/install, then rerun this script." >&2
  echo "If your network intercepts TLS, install your organization root CA in the OS trust store first." >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is not installed or not on PATH." >&2
  echo "Install Node.js 18+; Terraform uses it to stage a clean deployment archive." >&2
  exit 1
fi

terraform init
terraform apply

