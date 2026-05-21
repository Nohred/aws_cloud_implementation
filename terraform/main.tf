terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }

    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.8"
    }
  }
}

module "storage" {
  source = "./modules/storage"

  project_name = var.project_name
  environment  = var.environment
}

data "aws_caller_identity" "current" {}

module "data_eng" {
  source = "./modules/data-eng"

  project_name          = var.project_name
  environment           = var.environment
  raw_bucket_name       = "${var.project_name}-${var.environment}-raw-v3"
  processed_bucket_name = "${var.project_name}-${var.environment}-processed-v3"
  code_bucket_name      = "${var.project_name}-${var.environment}-code-v3"
  account_id            = data.aws_caller_identity.current.account_id
  # inference_bucket_name = "${var.project_name}-${var.environment}-inference-v3"

  depends_on = [module.storage]
}

module "orchestration" {
  source = "./modules/orchestration"

  project_name          = var.project_name
  environment           = var.environment
  raw_bucket_name       = "${var.project_name}-${var.environment}-raw-v3"
  processed_bucket_name = "${var.project_name}-${var.environment}-processed-v3"
  code_bucket_name      = "${var.project_name}-${var.environment}-code-v3"
  # inference_bucket_name = "${var.project_name}-${var.environment}-inference-v3"

}

module "ai_inference" {
  source = "./modules/ai-inference"

  project_name                 = var.project_name
  environment                  = var.environment
  code_bucket_name             = "${var.project_name}-${var.environment}-code-v3"
  inference_bucket_name        = "${var.project_name}-${var.environment}-inference-v3"
  model_data_url               = "s3://waste-classifier-dev-code-v3/models/waste-classifier-b3-6f465468-bc56-4a72-b152-0b4f45d4a276/output/model.tar.gz"
  sagemaker_execution_role_arn = "arn:aws:iam::${var.account_id}:role/${var.project_name}-${var.environment}-sagemaker-execution-role"
  endpoint_instance_type       = "ml.m5.xlarge"
  sagemaker_endpoint_name      = module.ai_inference.endpoint_name
  notification_emails = ["julianro120404@gmail.com", "ucalderon2912@gmail.com"]
  depends_on = [module.data_eng, module.storage]
}

