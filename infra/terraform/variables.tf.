variable "aws_region" {
  description = "Região AWS onde os recursos serão criados."
  type        = string
  default     = "eu-west-1"
}

variable "project_name" {
  description = "Nome curto usado nos nomes dos recursos."
  type        = string
  default     = "cloudops-lab"
}

variable "environment" {
  description = "Ambiente lógico da infraestrutura."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment deve ser dev, staging ou prod."
  }
}

variable "log_retention_days" {
  description = "Número de dias para conservar os registos no CloudWatch."
  type        = number
  default     = 14

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90], var.log_retention_days)
    error_message = "Escolha um período de retenção suportado pelo CloudWatch."
  }
}
