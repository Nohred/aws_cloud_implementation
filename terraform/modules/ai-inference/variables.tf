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
  default = "ml.m5.xlarge"
}