variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "raw_bucket_name" {
  type = string
}

variable "processed_bucket_name" {
  type = string
}

variable "code_bucket_name" {
  type = string
}

resource "aws_iam_role" "glue_crawler_role" {
  name = "${var.project_name}-${var.environment}-glue-crawler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "glue.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "glue_crawler_policy" {
  name = "${var.project_name}-${var.environment}-glue-crawler-policy"
  role = aws_iam_role.glue_crawler_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.raw_bucket_name}",
          "arn:aws:s3:::${var.raw_bucket_name}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:CreateTable",
          "glue:GetTable",
          "glue:GetTables",
          "glue:UpdateTable",
          "glue:BatchCreatePartition",
          "glue:CreatePartition",
          "glue:UpdatePartition",
          "glue:GetPartition",
          "glue:GetPartitions"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_glue_catalog_database" "main" {
  name = "${var.project_name}_${var.environment}_catalog"
}

resource "aws_glue_crawler" "raw_crawler" {
  database_name = aws_glue_catalog_database.main.name
  name          = "${var.project_name}-${var.environment}-raw-crawler"
  role          = aws_iam_role.glue_crawler_role.arn

  s3_target {
    path = "s3://${var.raw_bucket_name}/"
  }
}

#### Glue Job


resource "aws_iam_role" "glue_job_role" {
  name = "${var.project_name}-${var.environment}-glue-job-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "glue.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "glue_job_policy" {
  name = "${var.project_name}-${var.environment}-glue-job-policy"
  role = aws_iam_role.glue_job_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.raw_bucket_name}",
          "arn:aws:s3:::${var.raw_bucket_name}/*",
          "arn:aws:s3:::${var.processed_bucket_name}",
          "arn:aws:s3:::${var.processed_bucket_name}/*",
          "arn:aws:s3:::${var.code_bucket_name}",
          "arn:aws:s3:::${var.code_bucket_name}/*"
        ]
      }
    ]
  })
}

resource "aws_glue_job" "image_resize" {
  name     = "${var.project_name}-${var.environment}-image-resize"
  role_arn = aws_iam_role.glue_job_role.arn

  command {
    name            = "pythonshell"
    python_version  = "3.9"
    script_location = "s3://${var.code_bucket_name}/scripts/data_transformation.py"
  }

 
  default_arguments = {
  "--job-language" = "python"
  "--TempDir"      = "s3://${var.code_bucket_name}/temp/"
  "--extra-py-files" = "s3://${var.code_bucket_name}/libs/opencv-python-headless.whl s3://${var.code_bucket_name}/libs/numpy-python.whl"

}
}