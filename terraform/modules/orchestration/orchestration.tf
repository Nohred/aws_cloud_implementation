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
  ssm_prefix   = "/${var.project_name}/${var.environment}"
}

# ─────────────────────────────────────────────
# STEP FUNCTIONS 
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

  definition = jsonencode({
    Comment = "Run Glue crawler, wait for catalog refresh, then run the Glue job and trigger SageMaker training"
    StartAt = "StartCrawler"

    States = merge(
      jsondecode(templatefile("${path.module}/glue_flow.json", {
        crawler_name = local.crawler_name
        job_name     = local.job_name
      })),
      jsondecode(templatefile("${path.module}/sagemaker_flow.json", {
        project_name     = var.project_name
        environment      = var.environment
        account_id       = data.aws_caller_identity.current.account_id
        code_bucket_name = var.code_bucket_name
      }))
    )
  })
}

data "aws_caller_identity" "current" {}

# ─────────────────────────────────────────────
# SQS — buffer between EventBridge and Lambda
# ─────────────────────────────────────────────

resource "aws_sqs_queue" "etl_trigger" {
  name                       = "${var.project_name}-${var.environment}-etl-trigger"
  visibility_timeout_seconds = 600
  message_retention_seconds  = 3600
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
# Lambda deduplicator + 3-condition gate
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
      # ── Original permissions ──────────────────────────────────
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
      },
      # ── S3 list — count images in raw bucket for condition 2 ──────────
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = "arn:aws:s3:::${var.raw_bucket_name}"
      },
      # ── SSM read/write — time gate and image count tracking ──────────
      {
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:PutParameter"]
        Resource = "arn:aws:ssm:*:*:parameter/${var.project_name}/${var.environment}/*"
      },
      # ── CloudWatch read — average confidence query for condition 3 ───
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:GetMetricStatistics"]
        Resource = "*"
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
  # Increased from 30s: S3 paginator over a large bucket can take several seconds
  timeout          = 60

  environment {
    variables = {
      # ── Original ───────────────────────────────────────────────────────────
      STATE_MACHINE_ARN     = aws_sfn_state_machine.etl_orchestration.arn
      # ── 3-condition gate variables ───────────────────────────────────
      RAW_BUCKET            = var.raw_bucket_name
      SSM_PREFIX            = local.ssm_prefix
      CW_NAMESPACE          = "WasteClassifier/Metrics"
      MIN_DAYS_BETWEEN_RUNS = "15" 
      MIN_NEW_IMAGES        = "1000" 
      MIN_AVG_CONFIDENCE    = "0.10"
    }
  }
}

resource "aws_lambda_event_source_mapping" "sqs_to_lambda" {
  event_source_arn                   = aws_sqs_queue.etl_trigger.arn
  function_name                      = aws_lambda_function.etl_deduplicator.arn
  batch_size                         = 100
  maximum_batching_window_in_seconds = 300
}

# ─────────────────────────────────────────────
# EVENTBRIDGE — routing to SQS
# ─────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "s3_raw_upload" {
  name        = "${var.project_name}-${var.environment}-s3-raw-upload"
  description = "Trigger ETL workflow on new file upload to raw bucket"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail      = { bucket = { name = [var.raw_bucket_name] } }
  })
}

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
# Step Functions — SageMaker integration
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
          "sagemaker:AddTags",
          "sagemaker:UpdateTrainingJob",
          "sagemaker:CreateModel",
          "sagemaker:DeleteModel",
          "sagemaker:CreateEndpointConfig",
          "sagemaker:DeleteEndpointConfig",
          "sagemaker:UpdateEndpoint",
          "sagemaker:DescribeEndpoint",
          "sagemaker:DescribeEndpointConfig"
        ]
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
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:PutParameter", "ssm:GetParameter"]
        Resource = "arn:aws:ssm:*:*:parameter/${var.project_name}/${var.environment}/*"
      }
    ]
  })
}
