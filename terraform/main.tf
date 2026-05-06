terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

module "storage" {
  source = "./storage"

  project_name = var.project_name
  environment  = var.environment
}

module "data_eng" {
  source = "./data-eng"

  project_name    = var.project_name
  environment     = var.environment
  raw_bucket_name = "${var.project_name}-${var.environment}-raw"
}
