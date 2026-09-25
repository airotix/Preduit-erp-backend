output "alb_dns" {
  value       = aws_lb.main.dns_name
  description = "ALB DNS name — point your domain CNAME here"
}

output "rds_endpoint" {
  value = aws_rds_cluster.main.endpoint
}

output "rds_reader_endpoint" {
  value = aws_rds_cluster.main.reader_endpoint
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "s3_bucket" {
  value = aws_s3_bucket.docs.id
}

output "ecr_backend" {
  value = aws_ecr_repository.backend.repository_url
}

output "ecr_frontend" {
  value = aws_ecr_repository.frontend.repository_url
}

output "secrets_arn" {
  value = aws_secretsmanager_secret.app.arn
}
