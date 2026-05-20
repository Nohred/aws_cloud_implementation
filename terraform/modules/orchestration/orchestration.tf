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

locals {
  crawler_name = "${var.project_name}-${var.environment}-raw-crawler"
  job_name     = "${var.project_name}-${var.environment}-image-resize"
  log_group    = "/aws/states/${var.project_name}-${var.environment}-etl-orchestration"


}

# ─────────────────────────────────────────────
# STEP FUNCTIONS — sin cambios respecto al original
# ─────────────────────────────────────────────

resource "aws_iam_role" "step_functions_role" {
  name = "${var.project_name}-${var.environment}-step-functions-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "step_functions_policy" {
  name = "${var.project_name}-${var.environment}-step-functions-policy"
  role = aws_iam_role.step_functions_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "glue:StartCrawler",
          "glue:GetCrawler",
          "glue:StartJobRun",
          "glue:GetJobRun"
        ]
        Resource = [
          "arn:aws:glue:*:*:crawler/${local.crawler_name}",
          "arn:aws:glue:*:*:job/${local.job_name}"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "step_functions" {
  name              = local.log_group
  retention_in_days = 14
}


resource "aws_sfn_state_machine" "etl_orchestration" {
  name     = "${var.project_name}-${var.environment}-etl-orchestration"
  role_arn = aws_iam_role.step_functions_role.arn

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  # Combinamos los flujos independientes usando la función merge
  definition = jsonencode({
    Comment = "Run Glue crawler, wait for catalog refresh, then run the Glue job and trigger SageMaker training"
    StartAt = "StartCrawler"

    States = merge(
      jsondecode(templatefile("${path.module}/glue_flow.json", {
        crawler_name = local.crawler_name
        job_name     = local.job_name
      })),
      jsondecode(templatefile("${path.module}/sagemaker_flow.json", {
        project_name = var.project_name
        environment  = var.environment
        account_id   = data.aws_caller_identity.current.account_id
      }))
    )
  })
}



# Data helper indispensable para inyectar tu ID de cuenta AWS en tiempo de compilación
data "aws_caller_identity" "current" {}

# ─────────────────────────────────────────────
# NUEVO: SQS — buffer entre EventBridge y Lambda
# ─────────────────────────────────────────────

resource "aws_sqs_queue" "etl_trigger" {
  name                       = "${var.project_name}-${var.environment}-etl-trigger"
  visibility_timeout_seconds = 600  # >= timeout de la Lambda
  message_retention_seconds  = 3600 # Descartar mensajes tras 1 hora

  # SQS acumula los 10k eventos de S3 sin perder ninguno.
  # La Lambda los consume en lotes y decide si iniciar el pipeline.
}

resource "aws_sqs_queue_policy" "etl_trigger" {
  queue_url = aws_sqs_queue.etl_trigger.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.etl_trigger.arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.s3_raw_upload.arn }
      }
    }]
  })
}

# ─────────────────────────────────────────────
# NUEVO: Lambda deduplicadora
# ─────────────────────────────────────────────

data "archive_file" "lambda_dedup_zip" {
  type        = "zip"
  source_file = "${path.root}/../scripts/lambda_deduplicator.py"
  output_path = "${path.root}/../scripts/lambda_deduplicator.zip"
}

resource "aws_iam_role" "lambda_dedup_role" {
  name = "${var.project_name}-${var.environment}-lambda-dedup-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_dedup_policy" {
  name = "${var.project_name}-${var.environment}-lambda-dedup-policy"
  role = aws_iam_role.lambda_dedup_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.etl_trigger.arn
      },
      {
        Effect   = "Allow"
        Action   = ["states:ListExecutions", "states:StartExecution"]
        Resource = aws_sfn_state_machine.etl_orchestration.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_lambda_function" "etl_deduplicator" {
  function_name    = "${var.project_name}-${var.environment}-etl-deduplicator"
  role             = aws_iam_role.lambda_dedup_role.arn
  runtime          = "python3.12"
  handler          = "lambda_deduplicator.handler"
  filename         = data.archive_file.lambda_dedup_zip.output_path
  source_code_hash = data.archive_file.lambda_dedup_zip.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.etl_orchestration.arn
    }
  }
}

resource "aws_lambda_event_source_mapping" "sqs_to_lambda" {
  event_source_arn                   = aws_sqs_queue.etl_trigger.arn
  function_name                      = aws_lambda_function.etl_deduplicator.arn
  batch_size                         = 100
  maximum_batching_window_in_seconds = 300
  # Los 10k eventos de S3 se acumulan 60s → Lambda se invoca 1 vez con lote de 10
  # → verifica si el pipeline ya corre → arranca 1 sola ejecución de Step Functions
}

# ─────────────────────────────────────────────
# EVENTBRIDGE — cambia destino: de Step Functions a SQS
# ─────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "s3_raw_upload" {
  name        = "${var.project_name}-${var.environment}-s3-raw-upload"
  description = "Trigger ETL workflow on new file upload to raw bucket"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    # detail      = { bucket = { name = [module.storage.raw_bucket_id] } }
    detail      = { bucket = { name = [var.raw_bucket_name] } }
  })
}

# MODIFICADO: target ahora apunta a SQS (antes era aws_sfn_state_machine)
resource "aws_cloudwatch_event_target" "sqs" {
  rule      = aws_cloudwatch_event_rule.s3_raw_upload.name
  target_id = "SqsTriggerTarget"
  arn       = aws_sqs_queue.etl_trigger.arn
  role_arn  = aws_iam_role.eventbridge_role.arn
}

resource "aws_iam_role" "eventbridge_role" {
  name = "${var.project_name}-${var.environment}-eventbridge-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# MODIFICADO: antes states:StartExecution → ahora sqs:SendMessage
resource "aws_iam_role_policy" "eventbridge_policy" {
  name = "${var.project_name}-${var.environment}-eventbridge-policy"
  role = aws_iam_role.eventbridge_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.etl_trigger.arn
    }]
  })
}

resource "aws_s3_bucket_notification" "raw_bucket_notifications" {
  bucket      = var.raw_bucket_name
  eventbridge = true
}


# ─────────────────────────────────────────────
# Actualizar — Integración con SageMaker desde Step Functions
# ─────────────────────────────────────────────

resource "aws_iam_role_policy" "step_functions_sagemaker_policy" {
  name = "${var.project_name}-${var.environment}-sfn-sagemaker-policy"
  role = aws_iam_role.step_functions_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sagemaker:CreateTrainingJob",
          "sagemaker:DescribeTrainingJob",
          "sagemaker:StopTrainingJob",
          "sagemaker:AddTags",          # ⚠️ Requerido obligatoriamente por el modo .sync
          "sagemaker:UpdateTrainingJob" # Necesario para TensorBoard output config
        ]
        # ── BLINDAJE: Usamos "*" para evitar fallos por discrepancias de nombres dinámicos ──
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_name}-${var.environment}-sagemaker-execution-role"
        Condition = {
          StringEquals = { "iam:PassedToService" = "sagemaker.amazonaws.com" }
        }
      },
      {
        Effect = "Allow"
        Action = [
          "events:PutTargets",
          "events:PutRule",
          "events:DescribeRule",
          "events:DeleteRule",
          "events:RemoveTargets"
        ]
        Resource = "*"
      }
    ]
  })
}
