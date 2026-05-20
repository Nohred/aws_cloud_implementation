provider "aws" {
  region = var.aws_region

  access_key = var.use_localstack ? "test" : null
  secret_key = var.use_localstack ? "test" : null

  skip_credentials_validation = var.use_localstack
  skip_metadata_api_check     = var.use_localstack
  skip_requesting_account_id  = var.use_localstack
  s3_use_path_style           = var.use_localstack

  dynamic "endpoints" {
    for_each = var.use_localstack ? [1] : []
    content {
      s3     = "http://localhost:4566"
      glue   = "http://localhost:4566"
      iam    = "http://localhost:4566"
      sts    = "http://localhost:4566"
      sqs    = "http://localhost:4566"     # NUEVO: endpoint SQS para LocalStack
      lambda = "http://localhost:4566"     # NUEVO: endpoint Lambda para LocalStack
    }
  }
}

# NUEVO: empaqueta la Lambda como ZIP sin salir de Terraform
provider "archive" {}
