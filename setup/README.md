# DocuMind Setup

Minimal cloud setup assets for deploying DocuMind on a single AWS EC2 host.

Contents:

- `compose/`: Docker Compose files for API and Web
- `images/`: Dockerfiles for API and Web
- `ec2/terraform/single/`: single-node EC2 Terraform

## Infrastructure switches

DocuMind selects database and file storage with environment variables.

| Purpose | Default | Alternative |
| --- | --- | --- |
| Database | `DATABASE_TYPE=sqlite` | `DATABASE_TYPE=postgresql` |
| File storage | `STORAGE_TYPE=local` | `STORAGE_TYPE=s3` |

The Terraform variables mirror those choices:

- `database_type = "sqlite"`: use SQLite on the EC2 host
- `database_type = "postgresql"`: create RDS PostgreSQL and inject connection env vars
- `storage_type = "local"`: use `/var/lib/documind/outputs` on the EC2 host
- `storage_type = "s3"`: create an S3 bucket and inject bucket env vars

## Quick start

```bash
cd setup/ec2/terraform/single
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform apply
```

After apply finishes, open the `web_url` and `api_url` Terraform outputs.

