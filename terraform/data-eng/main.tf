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
          "glue:DeleteTable",
          "glue:BatchCreatePartition",
          "glue:CreatePartition",
          "glue:UpdatePartition",
          "glue:DeletePartition",
          "glue:GetPartition",
          "glue:GetPartitions"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
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
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*" # Permitir acceso a CloudWatch Logs para cualquier recurso
      },
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
    name            = "glueetl" # Pyspark Job instead of Python Shell 
    python_version  = "3"
    script_location = "s3://${var.code_bucket_name}/scripts/glue_etl.py"
  }

  number_of_workers = 2     # Numero de workers para el job, paralelismo
  worker_type       = "G.1X" # Tipo de worker, G.1X es un worker general con 1 vCPU y 4 GB de RAM

  default_arguments = {
    "--TempDir"       = "s3://${var.code_bucket_name}/temp/"
    "--input_bucket"  = "${var.raw_bucket_name}"
    "--output_bucket" = "${var.processed_bucket_name}"
    "--output_prefix" = "resized/"
    "--size"          = "256"
    # If you upload wheels to the code bucket, list them here as space-separated S3 paths
    # "--extra-py-files" = "s3://${var.code_bucket_name}/libs/numpy-2.2.6-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl s3://${var.code_bucket_name}/libs/Pillow-9.5.0.whl"
  }
}