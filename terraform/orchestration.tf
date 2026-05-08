locals {
  crawler_name = "${var.project_name}-${var.environment}-raw-crawler"
  job_name     = "${var.project_name}-${var.environment}-image-resize"
  log_group    = "/aws/states/${var.project_name}-${var.environment}-etl-orchestration"
}

resource "aws_iam_role" "step_functions_role" {
  name = "${var.project_name}-${var.environment}-step-functions-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
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
    Comment = "Run Glue crawler, wait for catalog refresh, then run the Glue job"
    StartAt = "StartCrawler"
    States = {
      StartCrawler = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:glue:startCrawler"
        Parameters = {
          Name = local.crawler_name
        }
        ResultPath = "$.CrawlerStart"
        Next       = "WaitForCrawler"
      }

      WaitForCrawler = {
        Type    = "Wait"
        Seconds = 30
        Next    = "GetCrawlerStatus"
      }

      GetCrawlerStatus = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:glue:getCrawler"
        Parameters = {
          Name = local.crawler_name
        }
        ResultPath = "$.CrawlerStatus"
        Next       = "CrawlerRunning"
      }

      CrawlerRunning = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.CrawlerStatus.Crawler.State"
            StringEquals = "RUNNING"
            Next         = "WaitForCrawler"
          },
          {
            Variable     = "$.CrawlerStatus.Crawler.State"
            StringEquals = "STOPPING"
            Next         = "WaitForCrawler"
          }
        ]
        Default = "CrawlerFinished"
      }

      CrawlerFinished = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.CrawlerStatus.Crawler.State"
            StringEquals = "READY"
            Next         = "StartGlueJob"
          }
        ]
        Default = "CrawlerFailed"
      }

      CrawlerFailed = {
        Type  = "Fail"
        Error = "CrawlerFailed"
        Cause = "Glue crawler did not succeed"
      }

      StartGlueJob = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:glue:startJobRun"
        Parameters = {
          JobName = local.job_name
        }
        ResultPath = "$.JobStart"
        Next       = "WaitForJob"
      }

      WaitForJob = {
        Type    = "Wait"
        Seconds = 60
        Next    = "GetJobStatus"
      }

      GetJobStatus = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:glue:getJobRun"
        Parameters = {
          JobName              = local.job_name
          "RunId.$"           = "$.JobStart.JobRunId"
          PredecessorsIncluded = false
        }
        ResultPath = "$.JobStatus"
        Next       = "JobRunning"
      }

      JobRunning = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.JobStatus.JobRun.JobRunState"
            StringEquals = "RUNNING"
            Next         = "WaitForJob"
          },
          {
            Variable     = "$.JobStatus.JobRun.JobRunState"
            StringEquals = "STARTING"
            Next         = "WaitForJob"
          }
        ]
        Default = "JobSucceeded"
      }

      JobSucceeded = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.JobStatus.JobRun.JobRunState"
            StringEquals = "SUCCEEDED"
            Next         = "PipelineSucceeded"
          }
        ]
        Default = "JobFailed"
      }

      JobFailed = {
        Type  = "Fail"
        Error = "JobFailed"
        Cause = "Glue job did not succeed"
      }

      PipelineSucceeded = {
        Type = "Succeed"
      }
    }
  })
}

# EventBridge rule to trigger the state machine when files are uploaded to the raw bucket
resource "aws_cloudwatch_event_rule" "s3_raw_upload" {
  name        = "${var.project_name}-${var.environment}-s3-raw-upload"
  description = "Trigger ETL workflow on new file upload to raw bucket"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = ["waste-classifier-dev-raw"]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "step_functions" {
  rule      = aws_cloudwatch_event_rule.s3_raw_upload.name
  target_id = "StepFunctionsExecutionTarget"
  arn       = aws_sfn_state_machine.etl_orchestration.arn
  role_arn  = aws_iam_role.eventbridge_role.arn

  # Pass the S3 event details to the state machine
  input = jsonencode({
    source = "eventbridge"
    detail = {}
  })
}

# IAM role for EventBridge to invoke Step Functions
resource "aws_iam_role" "eventbridge_role" {
  name = "${var.project_name}-${var.environment}-eventbridge-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "eventbridge_policy" {
  name = "${var.project_name}-${var.environment}-eventbridge-policy"
  role = aws_iam_role.eventbridge_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "states:StartExecution"
        ]
        Resource = aws_sfn_state_machine.etl_orchestration.arn
      }
    ]
  })
}

# Enable EventBridge notifications on S3 bucket
resource "aws_s3_bucket_notification" "raw_bucket_notifications" {
  bucket      = "waste-classifier-dev-raw"
  eventbridge = true
}
