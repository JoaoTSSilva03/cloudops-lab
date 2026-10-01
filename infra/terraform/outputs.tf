output "ecr_repository_url" {
  description = "URL do repositório ECR para a imagem da API."
  value       = aws_ecr_repository.api.repository_url
}

output "log_group_name" {
  description = "Nome do grupo de registos da API."
  value       = aws_cloudwatch_log_group.api.name
}
