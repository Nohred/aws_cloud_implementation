variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "code_bucket_name" {
  type = string
}

variable "model_data_url" {
  type = string
}

variable "sagemaker_execution_role_arn" {
  type = string
}

variable "endpoint_instance_type" {
  type    = string
  default = "ml.g4dn.xlarge"
}

variable "inference_bucket_name" {
  type        = string
  description = "Bucket S3 donde los usuarios suben imágenes para clasificar"
}

variable "sagemaker_endpoint_name" {
  type        = string
  description = "Nombre del endpoint de SageMaker que hace la inferencia"
}

variable "notification_emails" {
  description = "Lista de correos para recibir notificaciones"
  type        = list(string)
  default     = []
}