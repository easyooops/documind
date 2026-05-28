variable "aws_region" {
  type        = string
  description = "AWS region for EC2, S3, and optional RDS."
  default     = "ap-northeast-2"
}

variable "project_name" {
  type        = string
  description = "Prefix for resource names."
  default     = "documind"
}

variable "environment" {
  type        = string
  description = "Environment label."
  default     = "dev"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR for the VPC."
  default     = "10.72.0.0/16"
}

variable "public_subnet_cidr" {
  type        = string
  description = "CIDR for the public EC2 subnet."
  default     = "10.72.1.0/24"
}

variable "private_subnet_cidrs" {
  type        = list(string)
  description = "Private subnets for optional RDS. Must contain at least two CIDRs."
  default     = ["10.72.11.0/24", "10.72.12.0/24"]
}

variable "instance_type" {
  type        = string
  description = "EC2 instance type."
  default     = "t3.large"
}

variable "ubuntu_codename" {
  type        = string
  description = "Ubuntu LTS codename for AMI lookup. Use jammy or noble."
  default     = "jammy"
}

variable "key_name" {
  type        = string
  description = "EC2 key pair name for SSH. Empty means no SSH key; use SSM."
  default     = ""
}

variable "allow_ssh_cidr" {
  type        = string
  description = "CIDR allowed for SSH when key_name is set."
  default     = "203.0.113.10/32"
}

variable "allow_documind_cidr" {
  type        = string
  description = "CIDR allowed to access DocuMind web/API ports."
  default     = "0.0.0.0/0"
}

variable "documind_api_port" {
  type        = number
  description = "Host port for DocuMind API."
  default     = 8000
}

variable "documind_web_port" {
  type        = number
  description = "Host port for DocuMind Web."
  default     = 3000
}

variable "documind_api_image_tag" {
  type        = string
  description = "Docker image tag built on EC2 for the API."
  default     = "documind/api:0.2.0"
}

variable "documind_web_image_tag" {
  type        = string
  description = "Docker image tag built on EC2 for the Web."
  default     = "documind/web:0.2.0"
}

variable "root_volume_size_gb" {
  type        = number
  description = "Root EBS volume size."
  default     = 80
}

variable "documind_source_dir" {
  type        = string
  description = "DocuMind source root path relative to this Terraform directory."
  default     = "../../../.."
}

variable "database_type" {
  type        = string
  description = "sqlite or postgresql. postgresql creates RDS PostgreSQL."
  default     = "sqlite"

  validation {
    condition     = contains(["sqlite", "postgresql"], var.database_type)
    error_message = "database_type must be sqlite or postgresql."
  }
}

variable "storage_type" {
  type        = string
  description = "local or s3. s3 creates an S3 bucket for DocuMind storage."
  default     = "local"

  validation {
    condition     = contains(["local", "s3"], var.storage_type)
    error_message = "storage_type must be local or s3."
  }
}

variable "db_name" {
  type        = string
  description = "PostgreSQL database name."
  default     = "documind"
}

variable "db_user" {
  type        = string
  description = "PostgreSQL database user."
  default     = "documind"
}

variable "db_instance_class" {
  type        = string
  description = "RDS instance class when database_type is postgresql."
  default     = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  type        = number
  description = "RDS allocated storage in GB."
  default     = 20
}

variable "db_backup_retention_days" {
  type        = number
  description = "RDS backup retention days."
  default     = 7
}

variable "llm_provider" {
  type        = string
  description = "DocuMind LLM provider."
  default     = "bedrock"
}

variable "aws_bedrock_region" {
  type        = string
  description = "AWS region used by Bedrock SDK calls inside DocuMind."
  default     = "us-east-1"
}

variable "attach_bedrock_policy" {
  type        = bool
  description = "Attach Bedrock invoke permissions to the EC2 role."
  default     = true
}

variable "use_default_models" {
  type        = bool
  description = "Set USE_DEFAULT_MODELS."
  default     = true
}

variable "default_llm_model" {
  type        = string
  description = "Default LLM model."
  default     = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "default_vlm_model" {
  type        = string
  description = "Default VLM model."
  default     = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "default_image_model" {
  type        = string
  description = "Default image model."
  default     = "amazon.titan-image-generator-v2:0"
}

variable "openai_api_key" {
  type        = string
  description = "Optional OpenAI API key."
  default     = ""
  sensitive   = true
}

variable "anthropic_api_key" {
  type        = string
  description = "Optional Anthropic API key."
  default     = ""
  sensitive   = true
}

variable "google_api_key" {
  type        = string
  description = "Optional Google Gemini API key."
  default     = ""
  sensitive   = true
}

variable "custom_llm_base_url" {
  type        = string
  description = "OpenAI-compatible custom base URL."
  default     = "http://localhost:11434/v1"
}

variable "custom_llm_api_key" {
  type        = string
  description = "Optional custom LLM API key."
  default     = ""
  sensitive   = true
}

