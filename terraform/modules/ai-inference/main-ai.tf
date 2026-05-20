resource "terraform_data" "pack_inference_tar" {
  triggers_replace = [
    filemd5("${path.root}/../scripts/inference.py")
  ]

  provisioner "local-exec" {
    command = "mkdir -p ${path.root}/../build && tar -czf ${path.root}/../build/inference-sourcedir.tar.gz -C ${path.root}/../scripts inference.py"
  }
}

resource "aws_s3_object" "inference_script" {
  depends_on = [terraform_data.pack_inference_tar]

  bucket = var.code_bucket_name
  key    = "inference/sourcedir.tar.gz"
  source = "${path.root}/../build/inference-sourcedir.tar.gz"
#   etag   = filemd5("${path.root}/../build/inference-sourcedir.tar.gz")

  

}

resource "aws_sagemaker_model" "classifier" {
  name               = "${var.project_name}-${var.environment}-classifier-model"
  execution_role_arn = var.sagemaker_execution_role_arn

  primary_container {
    image          = "763104351884.dkr.ecr.us-east-1.amazonaws.com/pytorch-inference:2.0.1-gpu-py310-cu118-ubuntu20.04-sagemaker"
    model_data_url = var.model_data_url

    environment = {
      SAGEMAKER_PROGRAM          = "inference.py"
      SAGEMAKER_SUBMIT_DIRECTORY = "s3://${var.code_bucket_name}/inference/sourcedir.tar.gz"
    }
  }

  depends_on = [aws_s3_object.inference_script]
}

resource "aws_sagemaker_endpoint_configuration" "classifier" {
  name = "${var.project_name}-${var.environment}-classifier-endpoint-config"

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.classifier.name
    initial_instance_count = 1
    instance_type          = var.endpoint_instance_type
    initial_variant_weight = 1
  }
}

resource "aws_sagemaker_endpoint" "classifier" {
  name                 = "${var.project_name}-${var.environment}-classifier-endpoint"
  endpoint_config_name = aws_sagemaker_endpoint_configuration.classifier.name
}