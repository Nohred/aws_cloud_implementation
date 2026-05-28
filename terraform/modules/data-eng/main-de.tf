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

# Asegúrate de que el rol tenga esta política adjunta
resource "aws_iam_role_policy_attachment" "glue_crawler_service" {
  role       = aws_iam_role.glue_crawler_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
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
        "Effect" : "Allow",
        "Action" : [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "cloudwatch:PutMetricData"  # ← Agrega esta línea
        ],
        "Resource" : "*" # Cambia esto a "*" para que pueda escribir las métricas
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

  number_of_workers = 8      # Numero de workers para el job, paralelismo
  worker_type       = "G.2X" # Tipo de worker, G.1X es un worker general con 1 vCPU y 4 GB de RAM

  default_arguments = {
    "--TempDir"       = "s3://${var.code_bucket_name}/temp/"
    "--input_bucket"  = "${var.raw_bucket_name}"
    "--output_bucket" = "${var.processed_bucket_name}"
    "--output_prefix" = "resized/"
    "--size"          = "300"
    "--additional-python-modules" = "Pillow==10.3.0,numpy>=1.24"
    "--enable-metrics" = "true" 
  }
}

resource "aws_s3_object" "glue_script" {
  bucket = var.code_bucket_name
  key    = "scripts/glue_etl.py"

  # Subimos dos niveles: salimos del módulo, salimos de terraform y entramos a scripts
  # source = "${path.module}/../../scripts/glue_etl.py"
  # etag   = filemd5("${path.module}/../../scripts/glue_etl.py")
  # Subimos tres niveles: entramos a modules/data-eng, salimos de terraform y entramos a scripts
  source = "${path.module}/../../../scripts/glue_etl.py"
  etag   = filemd5("${path.module}/../../../scripts/glue_etl.py")
}



# ── 1. COMPRESIÓN AUTOMÁTICA DEL SCRIPT EN TAR.GZ ────────────────────────────
resource "terraform_data" "pack_train_tar" {
  # Se re-empaqueta si cambia train.py O requirements.txt
  triggers_replace = [
    filemd5("${path.module}/../../../scripts/train.py"),
    filemd5("${path.module}/../../../scripts/requirements.txt")
  ]

  provisioner "local-exec" {
    # Incluye requirements.txt en el tar — SageMaker lo detecta automáticamente
    # y ejecuta "pip install -r requirements.txt" antes de correr train.py
    command = "tar -czf ${path.module}/../../../scripts/sourcedir.tar.gz -C ${path.module}/../../../scripts train.py requirements.txt"
  }
}

# ── 2. SUBIDA DEL ARCHIVO COMPRIMIDO A S3 ─────────────────────────────────────
resource "aws_s3_object" "train_script" {
  depends_on = [terraform_data.pack_train_tar]

  bucket = var.code_bucket_name
  key    = "training/sourcedir.tar.gz"
  source = "${path.module}/../../../scripts/sourcedir.tar.gz"

  # etag cambia si cambia train.py o requirements.txt → fuerza re-subida a S3
  etag = md5(join("", [
    filemd5("${path.module}/../../../scripts/train.py"),
    filemd5("${path.module}/../../../scripts/requirements.txt")
  ]))
}
# ─────────────────────────────────────────────
# SAGEMAKER — Definiendo ROLES para Glue y SageMaker con permisos mínimos necesarios
# ─────────────────────────────────────────────

resource "aws_iam_role" "sagemaker_execution_role" {
  name = "${var.project_name}-${var.environment}-sagemaker-execution-role"

  # AQUÍ SOLO QUEDA EL ASSUME ROLE (TRUST POLICY)
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "sagemaker.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "sagemaker_execution_policy" {
  name = "${var.project_name}-${var.environment}-sagemaker-execution-policy"
  role = aws_iam_role.sagemaker_execution_role.id

  # AQUÍ VAN TODOS LOS PERMISOS, INCLUYENDO SSM
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
        # Limitado estrictamente a tus buckets de datos procesados y código
        Resource = [
          "arn:aws:s3:::${var.processed_bucket_name}",
          "arn:aws:s3:::${var.processed_bucket_name}/*",
          "arn:aws:s3:::${var.code_bucket_name}",
          "arn:aws:s3:::${var.code_bucket_name}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability"
        ]
        # Permite descargar las imágenes oficiales de TensorFlow/PyTorch desde el registro público de AWS
        Resource = "*"
      },
      {
        # --- AQUÍ AÑADIMOS EL PERMISO DE SSM ---
        Effect   = "Allow"
        Action   = [
          "ssm:PutParameter", 
          "ssm:GetParameter"
        ]
        Resource = "arn:aws:ssm:us-east-1:${var.account_id}:parameter/${var.project_name}/${var.environment}/*"
      }
    ]
  })
}