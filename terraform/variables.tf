variable "project_name" {
  description = "Nombre base del proyecto"
  type        = string
}

variable "environment" {
  description = "Ambiente de despliegue, por ejemplo dev, test o prod"
  type        = string
}

variable "aws_region" {
  description = "Región de AWS"
  type        = string
  default     = "us-east-1"
}

variable "use_localstack" {
  description = "Si es true, Terraform apunta a LocalStack; si es false, apunta a AWS real"
  type        = bool
  default     = true
}

variable "common_tags" {
  description = "Tags comunes para todos los recursos"
  type        = map(string)
  default = {
    managed_by = "terraform"
    project    = "waste-classifier"
  }
}