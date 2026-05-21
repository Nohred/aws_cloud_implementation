data "aws_ssm_parameter" "latest_model_url" {
  name = "/${var.project_name}/${var.environment}/latest-model-url"
}

resource "aws_s3_object" "inference_script" {
  bucket       = var.code_bucket_name
  key          = "inference/inference.py"
  source       = "${path.module}/../../../scripts/inference.py"
  etag         = filemd5("${path.module}/../../../scripts/inference.py")
  content_type = "text/x-python"
}

resource "aws_sagemaker_model" "classifier" {
  name               = "${var.project_name}-${var.environment}-classifier-model-v9"
  execution_role_arn = var.sagemaker_execution_role_arn

  primary_container {
    image          = "763104351884.dkr.ecr.us-east-1.amazonaws.com/pytorch-inference:2.0.1-cpu-py310-ubuntu20.04-sagemaker"
    model_data_url = data.aws_ssm_parameter.latest_model_url.value  # ← siempre el último

    environment = {
      SAGEMAKER_PROGRAM = "inference.py"
    }
  }
}

resource "aws_sagemaker_endpoint_configuration" "classifier" {
  name = "${var.project_name}-${var.environment}-classifier-endpoint-config-v9"

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.classifier.name
    initial_instance_count = 1
    instance_type          = var.endpoint_instance_type
    initial_variant_weight = 1
  }

  lifecycle { create_before_destroy = true }
}


# ─────────────────────────────────────────────────────────────────────────────
# Módulo: inference-trigger
#
# Flujo:  S3 ──► SQS ──► Lambda ──► SageMaker ──► SNS
#                  └──(3 fallos)──► DLQ ──► Alarma CloudWatch
#
# Sin EventBridge: S3 notifica directamente a SQS.
# EventBridge solo se justifica cuando hay múltiples destinos o filtros
# complejos; aquí es un flujo lineal y directo.
# ─────────────────────────────────────────────────────────────────────────────

# ── DLQ ──────────────────────────────────────────────────────────────────────
resource "aws_sqs_queue" "inference_dlq" {
  name                      = "${var.project_name}-${var.environment}-inference-dlq"
  message_retention_seconds = 1209600   # 14 días para revisar fallos
}

# ── Cola principal ────────────────────────────────────────────────────────────
resource "aws_sqs_queue" "inference_queue" {
  name                       = "${var.project_name}-${var.environment}-inference-queue"
  visibility_timeout_seconds = 120        # debe ser >= timeout de la Lambda
  message_retention_seconds  = 3600       # 1 hora; si no se procesa, se descarta

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.inference_dlq.arn
    maxReceiveCount     = 3               # 3 intentos antes de mover a DLQ
  })
}

# Política: permite a S3 escribir en la cola
resource "aws_sqs_queue_policy" "inference_queue" {
  queue_url = aws_sqs_queue.inference_queue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.inference_queue.arn
      Condition = {
        ArnLike = { "aws:SourceArn" = "arn:aws:s3:::${var.inference_bucket_name}" }
      }
    }]
  })
}

# Notificación directa S3 → SQS (sin EventBridge)
# S3 dispara la notificación en cuanto se sube un objeto con extensión de imagen.
resource "aws_s3_bucket_notification" "inference_upload" {
  bucket = var.inference_bucket_name

  queue {
    queue_arn     = aws_sqs_queue.inference_queue.arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".jpg"
  }

  queue {
    queue_arn     = aws_sqs_queue.inference_queue.arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".jpeg"
  }

  queue {
    queue_arn     = aws_sqs_queue.inference_queue.arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".png"
  }

  depends_on = [aws_sqs_queue_policy.inference_queue]
}

# ── SNS topic ─────────────────────────────────────────────────────────────────
resource "aws_sns_topic" "predictions" {
  name = "${var.project_name}-${var.environment}-predictions"
}

# Suscripción por email múltiple
resource "aws_sns_topic_subscription" "email" {
  for_each  = toset(var.notification_emails)
  
  topic_arn = aws_sns_topic.predictions.arn
  protocol  = "email"
  endpoint  = each.value
}

# ── IAM role para Lambda ──────────────────────────────────────────────────────
resource "aws_iam_role" "lambda_inference" {
  name = "${var.project_name}-${var.environment}-lambda-inference-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_inference" {
  name = "${var.project_name}-${var.environment}-lambda-inference-policy"
  role = aws_iam_role.lambda_inference.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Leer imágenes del bucket de inferencia
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${var.inference_bucket_name}/*"
      },
      # Invocar el endpoint de SageMaker
      {
        Effect   = "Allow"
        Action   = ["sagemaker:InvokeEndpoint"]
        Resource = "*"
      },
      # Publicar predicciones en SNS
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.predictions.arn
      },
      # Consumir mensajes de SQS (necesario para el trigger)
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.inference_queue.arn
      },
      # Logs en CloudWatch
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

# ── Lambda ────────────────────────────────────────────────────────────────────
data "archive_file" "lambda_inference" {
  type        = "zip"
  source_file = "${path.module}/../../../scripts/lambda_inference.py"
  output_path = "${path.module}/../../../build/lambda_inference.zip"
}

resource "aws_lambda_function" "inference" {
  function_name    = "${var.project_name}-${var.environment}-inference"
  role             = aws_iam_role.lambda_inference.arn
  runtime          = "python3.12"
  handler          = "lambda_inference.handler"
  filename         = data.archive_file.lambda_inference.output_path
  source_code_hash = data.archive_file.lambda_inference.output_base64sha256
  timeout          = 60    # SageMaker puede tardar; visibility_timeout debe ser >= este valor

  environment {
    variables = {
      SAGEMAKER_ENDPOINT_NAME = var.sagemaker_endpoint_name
      SNS_TOPIC_ARN           = aws_sns_topic.predictions.arn
    }
  }
}

# Trigger: SQS dispara Lambda con una imagen a la vez
resource "aws_lambda_event_source_mapping" "sqs_to_lambda" {
  event_source_arn = aws_sqs_queue.inference_queue.arn
  function_name    = aws_lambda_function.inference.arn
  batch_size       = 1    # una imagen por invocación; evita timeouts por acumulación
}

# ── Alarma CloudWatch: mensajes en DLQ ───────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${var.project_name}-${var.environment}-inference-dlq-alarm"
  alarm_description   = "Hay mensajes en la DLQ de inferencia — revisar logs de Lambda"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = { QueueName = aws_sqs_queue.inference_dlq.name }

  # Notifica al mismo SNS topic para centralizar alertas
  alarm_actions = [aws_sns_topic.predictions.arn]
}


resource "aws_sagemaker_endpoint" "classifier" {
  name                 = "${var.project_name}-${var.environment}-classifier-endpoint"
  endpoint_config_name = aws_sagemaker_endpoint_configuration.classifier.name
}
output "endpoint_name" {
  description = "El nombre del endpoint de SageMaker creado por el módulo"
  value       = aws_sagemaker_endpoint.classifier.name
}

