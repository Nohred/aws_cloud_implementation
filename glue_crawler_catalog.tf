# Agrega esto al inicio de tu archivo .tf
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# Paso 1: Crear el role Glue y asignar la politica de servicio definiendo los permisos necesarios
# para acceder a los buckets de S3 y ejecutar tareas de Glue

resource "aws_iam_role" "glue_role" {
  name = "GarbageClassificationGlueRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
    }]
  })
}

# Adjuntamos la política básica de Glue y acceso a S3
resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

# Permiso para que Glue lea y escriba en tus buckets específicos
resource "aws_iam_role_policy" "s3_access" {
  name = "S3AccessForGlue"
  role = aws_iam_role.glue_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
      Resource = [
        "arn:aws:s3:::bucket-source-garbage-classification",
        "arn:aws:s3:::bucket-source-garbage-classification/*",
        "arn:aws:s3:::bucket-target-garbage-classification",
        "arn:aws:s3:::bucket-target-garbage-classification/*"
      ] 
    }]
  })
}

# Paso 2: Crear la base de datos del catálogo de Glue y el crawler para que detecte los datos 
# en el bucket de origen y cree las tablas automáticamente.

# La Base de Datos (Contenedor lógico)
resource "aws_glue_catalog_database" "garbage_db" {
  name = "garbage_classification_db"
}

# El Crawler (El que creará la tabla automáticamente)
resource "aws_glue_crawler" "garbage_crawler" {
  database_name = aws_glue_catalog_database.garbage_db.name
  name          = "garbage-crawler"
  role          = aws_iam_role.glue_role.arn

  s3_target {
    path = "s3://${aws_s3_bucket.source.bucket}/"
  }

  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Partitions = { AddOrUpdateBehavior = "InheritFromTable" }
    }
  })
}

# Paso 3: Crear el Workflow de Glue y un Trigger para que se ejecute el Crawler cuando llegue un evento 
# configurado en EventBridge (Paso 4)

# El director de orquesta
resource "aws_glue_workflow" "garbage_wf" {
  name = "garbage-classification-workflow"
}

# Trigger que inicia el Crawler cuando llega el evento
resource "aws_glue_trigger" "start_crawler_trigger" {
  name          = "s3-event-starts-crawler"
  type          = "EVENT" # <--- Esto indica que espera a EventBridge
  workflow_name = aws_glue_workflow.garbage_wf.name

  actions {
    crawler_name = aws_glue_crawler.garbage_crawler.name
  }
}

# paso 4: Crear un role específico para EventBridge que permita iniciar el Workflow de Glue 
# cuando se suban archivos al bucket de origen.
# 1. El Rol de confianza (Trust Policy)
resource "aws_iam_role" "eventbridge_role" {
  name = "EventBridgeToGlueRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com" # EventBridge es quien asume este rol
        }
      }
    ]
  })
}

# 2. La política de permisos (Inline Policy)
resource "aws_iam_role_policy" "eventbridge_glue_policy" {
  name = "AllowEventBridgeToNotifyGlue"
  role = aws_iam_role.eventbridge_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "glue:NotifyEvent" # Acción necesaria para activar triggers de tipo EVENT
        Resource = [
          "arn:aws:glue:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:workflow/${aws_glue_workflow.garbage_wf.name}"
        ]
      }
    ]
  })
}


# Paso 5: Crear una regla de EventBridge que detecte cuando se suben archivos al bucket de origen
# y dispare el Trigger del Workflow de Glue

# Regla de EventBridge: ¿Qué estamos buscando?
resource "aws_cloudwatch_event_rule" "s3_upload_rule" {
  name        = "notify-glue-on-s3-upload"
  description = "Dispara Glue cuando se suben archivos al bucket source"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail_type = ["Object Created"]
    detail = {
      bucket = {
        name = [aws_s3_bucket.source.id]
      }
    }
  })
}

# Objetivo: ¿A quién le avisamos? (Al Workflow de Glue)
resource "aws_cloudwatch_event_target" "glue_target" {
  rule      = aws_cloudwatch_event_rule.s3_upload_rule.name
  target_id = "SendToGlueWorkflow"
  arn       = "arn:aws:glue:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:workflow/${aws_glue_workflow.garbage_wf.name}"
  role_arn  = aws_iam_role.eventbridge_role.arn # Necesitas un rol pequeño para que EventBridge inicie Glue
}

