locals {
  name_prefix = "${var.project_name}-${var.environment}"
  tags = {
    Project     = var.project_name
    Environment = var.environment
    Service     = "documind"
  }

  source_dir  = abspath("${path.module}/${var.documind_source_dir}")
  product_dir = abspath("${path.module}/../../..")
  staging_dir = abspath("${path.module}/.terraform-staging")

  archive_script  = replace("${path.module}/scripts/prepare-archive.mjs", "\\", "/")
  source_dir_cli  = replace(local.source_dir, "\\", "/")
  product_dir_cli = replace(local.product_dir, "\\", "/")
  staging_dir_cli = replace(local.staging_dir, "\\", "/")

  use_postgresql = var.database_type == "postgresql"
  use_s3_storage = var.storage_type == "s3"
}

resource "random_password" "postgres" {
  count   = local.use_postgresql ? 1 : 0
  length  = 24
  special = false
}

resource "terraform_data" "stage_documind_source" {
  triggers_replace = [timestamp()]

  provisioner "local-exec" {
    command = "node ${local.archive_script} ${local.source_dir_cli} ${local.staging_dir_cli}/source source"
  }
}

resource "terraform_data" "stage_documind_product" {
  triggers_replace = [timestamp()]

  provisioner "local-exec" {
    command = "node ${local.archive_script} ${local.product_dir_cli} ${local.staging_dir_cli}/product product"
  }
}

data "archive_file" "documind_source" {
  type        = "zip"
  source_dir  = "${local.staging_dir}/source"
  output_path = "${local.staging_dir}/documind-source.zip"
  depends_on  = [terraform_data.stage_documind_source]

  excludes = [
    "**/.git",
    "**/.venv",
    "**/.cache",
    "**/.pytest_cache",
    "**/.ruff_cache",
    "**/node_modules",
    "**/.next",
    "**/__pycache__",
    "**/data",
    "**/*.pyc",
    "**/.env",
    "**/.env.*",
    "**/.terraform",
    "**/.terraform-staging",
    "**/.terraform.lock.hcl",
    "**/terraform.tfstate",
    "**/terraform.tfstate.*",
    "**/tfplan",
    "setup",
    "setup/**",
  ]
}

data "archive_file" "documind_product" {
  type        = "zip"
  source_dir  = "${local.staging_dir}/product"
  output_path = "${local.staging_dir}/documind-product.zip"
  depends_on  = [terraform_data.stage_documind_product]

  excludes = [
    "**/.terraform",
    "**/.terraform-staging",
    "**/.terraform.lock.hcl",
    "**/terraform.tfstate",
    "**/terraform.tfstate.*",
    "**/tfplan",
  ]
}

resource "aws_s3_bucket" "stage" {
  bucket_prefix = "${local.name_prefix}-stage-"
  force_destroy = true
  tags          = merge(local.tags, { Purpose = "bootstrap-stage" })
}

resource "aws_s3_bucket_public_access_block" "stage" {
  bucket                  = aws_s3_bucket.stage.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "documind_source" {
  bucket = aws_s3_bucket.stage.id
  key    = "documind-source.zip"
  source = data.archive_file.documind_source.output_path
  etag   = data.archive_file.documind_source.output_md5
}

resource "aws_s3_object" "documind_product" {
  bucket = aws_s3_bucket.stage.id
  key    = "documind-product.zip"
  source = data.archive_file.documind_product.output_path
  etag   = data.archive_file.documind_product.output_md5
}

resource "aws_s3_bucket" "storage" {
  count         = local.use_s3_storage ? 1 : 0
  bucket_prefix = "${local.name_prefix}-storage-"
  force_destroy = false
  tags          = merge(local.tags, { Purpose = "documind-storage" })
}

resource "aws_s3_bucket_public_access_block" "storage" {
  count                   = local.use_s3_storage ? 1 : 0
  bucket                  = aws_s3_bucket.storage[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "storage" {
  count  = local.use_s3_storage ? 1 : 0
  bucket = aws_s3_bucket.storage[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-${var.ubuntu_codename}-*-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "${local.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "${local.name_prefix}-public" })
}

resource "aws_subnet" "private" {
  count             = local.use_postgresql ? length(var.private_subnet_cidrs) : 0
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = merge(local.tags, { Name = "${local.name_prefix}-private-${count.index + 1}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-public-rt" })
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "documind" {
  name        = "${local.name_prefix}-sg"
  description = "DocuMind API, Web, and optional SSH"
  vpc_id      = aws_vpc.this.id

  egress {
    description      = "all"
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  ingress {
    description = "DocuMind Web"
    from_port   = var.documind_web_port
    to_port     = var.documind_web_port
    protocol    = "tcp"
    cidr_blocks = [var.allow_documind_cidr]
  }

  ingress {
    description = "DocuMind API"
    from_port   = var.documind_api_port
    to_port     = var.documind_api_port
    protocol    = "tcp"
    cidr_blocks = [var.allow_documind_cidr]
  }

  dynamic "ingress" {
    for_each = var.key_name != "" ? [1] : []
    content {
      description = "SSH"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = [var.allow_ssh_cidr]
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-sg" })
}

resource "aws_security_group" "postgres" {
  count       = local.use_postgresql ? 1 : 0
  name        = "${local.name_prefix}-postgres-sg"
  description = "PostgreSQL access from DocuMind EC2"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "PostgreSQL from DocuMind EC2"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.documind.id]
  }

  egress {
    description = "all"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-postgres-sg" })
}

resource "aws_db_subnet_group" "postgres" {
  count      = local.use_postgresql ? 1 : 0
  name       = "${local.name_prefix}-postgres-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = local.tags
}

resource "aws_db_instance" "postgres" {
  count                   = local.use_postgresql ? 1 : 0
  identifier              = "${local.name_prefix}-postgres"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = var.db_instance_class
  allocated_storage       = var.db_allocated_storage_gb
  storage_encrypted       = true
  db_name                 = var.db_name
  username                = var.db_user
  password                = random_password.postgres[0].result
  db_subnet_group_name    = aws_db_subnet_group.postgres[0].name
  vpc_security_group_ids  = [aws_security_group.postgres[0].id]
  publicly_accessible     = false
  skip_final_snapshot     = true
  backup_retention_period = var.db_backup_retention_days
  deletion_protection     = false
  tags                    = local.tags
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "documind" {
  name               = "${local.name_prefix}-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.documind.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "stage_read" {
  statement {
    sid     = "ReadStageObjects"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = [
      "${aws_s3_bucket.stage.arn}/*",
    ]
  }

  statement {
    sid       = "ListStageBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.stage.arn]
  }
}

resource "aws_iam_role_policy" "stage_read" {
  name   = "${local.name_prefix}-stage-read"
  role   = aws_iam_role.documind.id
  policy = data.aws_iam_policy_document.stage_read.json
}

data "aws_iam_policy_document" "documind_storage_rw" {
  count = local.use_s3_storage ? 1 : 0

  statement {
    sid    = "DocuMindStorageObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = [
      "${aws_s3_bucket.storage[0].arn}/*",
    ]
  }

  statement {
    sid       = "DocuMindStorageList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.storage[0].arn]
  }
}

resource "aws_iam_role_policy" "documind_storage_rw" {
  count  = local.use_s3_storage ? 1 : 0
  name   = "${local.name_prefix}-storage-rw"
  role   = aws_iam_role.documind.id
  policy = data.aws_iam_policy_document.documind_storage_rw[0].json
}

data "aws_iam_policy_document" "bedrock_invoke" {
  count = var.attach_bedrock_policy ? 1 : 0

  statement {
    sid    = "BedrockInvoke"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "bedrock_invoke" {
  count  = var.attach_bedrock_policy ? 1 : 0
  name   = "${local.name_prefix}-bedrock-invoke"
  role   = aws_iam_role.documind.id
  policy = data.aws_iam_policy_document.bedrock_invoke[0].json
}

resource "aws_iam_instance_profile" "documind" {
  name = "${local.name_prefix}-ec2-profile"
  role = aws_iam_role.documind.name
  tags = local.tags
}

resource "aws_instance" "documind" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.documind.id]
  iam_instance_profile   = aws_iam_instance_profile.documind.name
  key_name               = var.key_name != "" ? var.key_name : null

  user_data = base64encode(templatefile("${path.module}/templates/bootstrap.sh.tpl", {
    aws_region             = var.aws_region
    aws_bedrock_region     = var.aws_bedrock_region
    stage_bucket           = aws_s3_bucket.stage.bucket
    source_object_key      = aws_s3_object.documind_source.key
    product_object_key     = aws_s3_object.documind_product.key
    source_archive_etag    = data.archive_file.documind_source.output_md5
    product_archive_etag   = data.archive_file.documind_product.output_md5
    documind_api_port      = var.documind_api_port
    documind_web_port      = var.documind_web_port
    expected_public_ip     = aws_eip.documind.public_ip
    documind_api_image_tag = var.documind_api_image_tag
    documind_web_image_tag = var.documind_web_image_tag
    database_type          = var.database_type
    storage_type           = var.storage_type
    db_host                = local.use_postgresql ? aws_db_instance.postgres[0].address : ""
    db_port                = local.use_postgresql ? aws_db_instance.postgres[0].port : 5432
    db_name                = var.db_name
    db_user                = var.db_user
    db_password            = local.use_postgresql ? random_password.postgres[0].result : ""
    s3_bucket              = local.use_s3_storage ? aws_s3_bucket.storage[0].bucket : ""
    llm_provider           = var.llm_provider
    use_default_models     = tostring(var.use_default_models)
    default_llm_model      = var.default_llm_model
    default_vlm_model      = var.default_vlm_model
    default_image_model    = var.default_image_model
    openai_api_key         = var.openai_api_key
    anthropic_api_key      = var.anthropic_api_key
    google_api_key         = var.google_api_key
    custom_llm_base_url    = var.custom_llm_base_url
    custom_llm_api_key     = var.custom_llm_api_key
  }))

  user_data_replace_on_change = true

  root_block_device {
    volume_size           = var.root_volume_size_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-host" })

  lifecycle {
    ignore_changes = [ami]
  }

  depends_on = [
    aws_s3_object.documind_source,
    aws_s3_object.documind_product,
    aws_db_instance.postgres,
  ]
}

resource "aws_eip" "documind" {
  domain = "vpc"
  tags   = merge(local.tags, { Name = "${local.name_prefix}-eip" })
}

resource "aws_eip_association" "documind" {
  instance_id   = aws_instance.documind.id
  allocation_id = aws_eip.documind.id
}
