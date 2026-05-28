output "web_url" {
  description = "DocuMind Web URL."
  value       = "http://${aws_eip.documind.public_ip}:${var.documind_web_port}"
}

output "api_url" {
  description = "DocuMind API URL."
  value       = "http://${aws_eip.documind.public_ip}:${var.documind_api_port}"
}

output "health_url" {
  description = "DocuMind API health check URL."
  value       = "http://${aws_eip.documind.public_ip}:${var.documind_api_port}/health"
}

output "instance_id" {
  description = "EC2 instance ID."
  value       = aws_instance.documind.id
}

output "ssm_start_session" {
  description = "Command to open an SSM session."
  value       = "aws ssm start-session --target ${aws_instance.documind.id} --region ${var.aws_region}"
}

output "storage_bucket" {
  description = "S3 bucket used when storage_type=s3."
  value       = local.use_s3_storage ? aws_s3_bucket.storage[0].bucket : null
}

output "postgres_endpoint" {
  description = "RDS endpoint used when database_type=postgresql."
  value       = local.use_postgresql ? aws_db_instance.postgres[0].address : null
}

