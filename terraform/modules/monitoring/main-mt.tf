variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

# ─────────────────────────────────────────────────────────────────────────────
# 1. MÉTRICAS DE NEGOCIO / DATOS (Log Metric Filters)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_metric_filter" "inference_success" {
  name           = "${var.project_name}-${var.environment}-inference-success"
  pattern        = "\"SUCCESS\""
  log_group_name = "/aws/lambda/${var.project_name}-${var.environment}-inference"

  metric_transformation {
    name      = "InferenceSuccess"
    namespace = "WasteClassifier/Metrics"
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "inference_failure" {
  name           = "${var.project_name}-${var.environment}-inference-failure"
  pattern        = "\"ERROR\""
  log_group_name = "/aws/lambda/${var.project_name}-${var.environment}-inference"

  metric_transformation {
    name      = "InferenceFailure"
    namespace = "WasteClassifier/Metrics"
    value     = "1"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. CLOUDWATCH DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # Sintaxis correcta de SEARCH para métricas de SageMaker Training:
  #   '{namespace,dimension} "metricName" "filterTerm"'
  #
  # ❌ INCORRECTO (no funciona): MetricName="train:loss" TrainingJobName="project-*"
  #    → MetricName NO es una dimensión, es el nombre de la métrica.
  #    → El wildcard en dimensiones no se soporta en CloudWatch SEARCH.
  #
  # ✅ CORRECTO: poner la métrica como término libre y el proyecto como filtro
  #    → CloudWatch busca cualquier TrainingJob cuyo nombre contenga "waste-classifier"
  #      y que tenga la métrica "train:loss" publicada.
  #    → Automático: cada training job nuevo que matchee aparece solo.

  sm_ns = "{/aws/sagemaker/TrainingJobs,TrainingJobName}"
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.project_name}-${var.environment}-monitoring-dashboard"

  dashboard_body = jsonencode({
    widgets = [

      # ── WIDGET 1: Duración de los Glue Jobs ──────────────────────────────
      # Namespace correcto: "Glue" (no "AWS/Glue").
      # SEARCH dinámico: captura cualquier JobRunId nuevo automáticamente.
      # Type="driver" filtra solo el nodo principal del job Spark.
{
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [[{
            # Búsqueda abierta (Schema-less) para evitar conflictos de dimensiones
            expression = "SEARCH('Namespace=\"Glue\" MetricName=\"glue.driver.aggregate.elapsedTime\" JobName=\"${var.project_name}-${var.environment}-image-resize\"', 'Average', 300)"
            label      = "Elapsed Time (ms)"
            id         = "e1"
          }]]
          view    = "timeSeries"
          stacked = false
          region  = "us-east-1"
          title   = "Widget 1: Duración de Glue Job (Image Resize)"
          period  = 300
          stat    = "Average"
          yAxis   = { left = { label = "Milisegundos", min = 0 } }
        }
      },

      # ── WIDGET 2: Archivos en Curated Zone de S3 ─────────────────────────
      # Métrica diaria de AWS. Para verla cambia el rango del dashboard a 1d o 1sem.
      {
        type   = "metric"
        x = 12, y = 0, width = 12, height = 6
        properties = {
          metrics = [
            ["AWS/S3", "NumberOfObjects",
              "StorageType", "AllStorageTypes",
              "BucketName", "${var.project_name}-${var.environment}-processed-v3"]
          ]
          view   = "singleValue"
          region = "us-east-1"
          title  = "Widget 2: Archivos en Curated Zone (Processed v3) — rango mínimo: 1d"
          period = 86400
          stat   = "Maximum"
        }
      },

      # ── WIDGET 3: Tasa de error en el endpoint de SageMaker ──────────────
      # VariantName="AllTraffic" es obligatorio para que SageMaker emita la métrica.
      # Se añade Invocations para ver actividad incluso cuando no hay errores.
      {
        type   = "metric"
        x = 0, y = 6, width = 12, height = 6
        properties = {
          metrics = [
            ["AWS/SageMaker", "Invocations",
              "EndpointName", "${var.project_name}-${var.environment}-classifier-endpoint",
              "VariantName", "AllTraffic",
              { label = "Invocaciones totales", color = "#2ca02c" }],
            ["AWS/SageMaker", "Invocation4XXErrors",
              "EndpointName", "${var.project_name}-${var.environment}-classifier-endpoint",
              "VariantName", "AllTraffic",
              { label = "Errores 4XX (cliente)", color = "#ff7f0e" }],
            ["AWS/SageMaker", "Invocation5XXErrors",
              "EndpointName", "${var.project_name}-${var.environment}-classifier-endpoint",
              "VariantName", "AllTraffic",
              { label = "Errores 5XX (modelo)", color = "#d62728" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "us-east-1"
          title   = "Widget 3: Errores en SageMaker Endpoint (4XX y 5XX)"
          stat    = "Sum"
          period  = 60
        }
      },

      # ── WIDGET 4: Latencia real de inferencia ────────────────────────────
      # VariantName="AllTraffic" obligatorio (mismo motivo que Widget 3).
      # InvocationLatency no existe → nombre correcto: OverheadLatency.
      {
        type   = "metric"
        x = 12, y = 6, width = 12, height = 6
        properties = {
          metrics = [
            ["AWS/SageMaker", "ModelLatency",
              "EndpointName", "${var.project_name}-${var.environment}-classifier-endpoint",
              "VariantName", "AllTraffic",
              { label = "Latencia del Modelo (µs)", color = "#9467bd" }],
            ["AWS/SageMaker", "OverheadLatency",
              "EndpointName", "${var.project_name}-${var.environment}-classifier-endpoint",
              "VariantName", "AllTraffic",
              { label = "Overhead SageMaker (µs)", color = "#bcbd22" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "us-east-1"
          title   = "Tiempo Real de Inferencia (Model Latency)"
          period  = 60
          stat    = "Average"
          yAxis   = { left = { label = "Microsegundos (µs)", min = 0 } }
        }
      },

      # ── WIDGET 5: Imágenes con éxito vs fallidas ─────────────────────────
      # Alimentado por los Log Metric Filters de arriba.
      # Requiere que lambda_inference.py imprima "SUCCESS" o "ERROR".
      {
        type   = "metric"
        x = 0, y = 12, width = 12, height = 6
        properties = {
          metrics = [
            ["WasteClassifier/Metrics", "InferenceSuccess",
              { color = "#2ca02c", label = "Éxito" }],
            ["WasteClassifier/Metrics", "InferenceFailure",
              { color = "#d62728", label = "Fallo" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "us-east-1"
          title   = "Imágenes procesadas con éxito vs fallidas"
          stat    = "Sum"
          period  = 300
        }
      },

      # ── WIDGET 6: Tamaño promedio de archivos en Processed ───────────────
      # Math expression BucketSizeBytes / NumberOfObjects. Métrica diaria.
      {
        type   = "metric"
        x = 12, y = 12, width = 12, height = 6
        properties = {
          metrics = [
            ["AWS/S3", "BucketSizeBytes",
              "StorageType", "StandardStorage",
              "BucketName", "${var.project_name}-${var.environment}-processed-v3",
              { id = "m1", visible = false }],
            ["AWS/S3", "NumberOfObjects",
              "StorageType", "AllStorageTypes",
              "BucketName", "${var.project_name}-${var.environment}-processed-v3",
              { id = "m2", visible = false }],
            [{ expression = "m1/m2", label = "Promedio Bytes por Archivo", id = "e1", region = "us-east-1" }]
          ]
          view   = "singleValue"
          region = "us-east-1"
          title  = "Tamaño Promedio de Archivos en Processed (Bytes) — rango mínimo: 1d"
          period = 86400
          stat   = "Average"
        }
      },
# ═════════════════════════════════════════════════════════════════════
      # WIDGETS DE ENTRENAMIENTO — Usando SEARCH con Regex (=~)
      # ═════════════════════════════════════════════════════════════════════

      # ── WIDGET 7: Curvas de Pérdida (Loss) ───────────────────────────────
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          metrics = [
            [{ expression = "SEARCH('{/aws/sagemaker/TrainingJobs,TrainingJobName} MetricName=\"train:loss\" TrainingJobName=~\"${var.project_name}-*\"', 'Average', 60)", label = "train:loss", id = "e1" }],
            [{ expression = "SEARCH('{/aws/sagemaker/TrainingJobs,TrainingJobName} MetricName=\"val:loss\" TrainingJobName=~\"${var.project_name}-*\"', 'Average', 60)", label = "val:loss", id = "e2" }]
          ]
          view   = "timeSeries"
          region = "us-east-1"
          title  = "Pérdida / Loss (Train vs Validation)"
          period = 60
          stat   = "Average"
        }
      },

      # ── WIDGET 8: Exactitud (Accuracy) ───────────────────────────────────
      {
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          metrics = [
            [{ expression = "SEARCH('{/aws/sagemaker/TrainingJobs,TrainingJobName} MetricName=\"train:accuracy\" TrainingJobName=~\"${var.project_name}-*\"', 'Average', 60)", label = "train:accuracy", id = "e3" }],
            [{ expression = "SEARCH('{/aws/sagemaker/TrainingJobs,TrainingJobName} MetricName=\"val:accuracy\" TrainingJobName=~\"${var.project_name}-*\"', 'Average', 60)", label = "val:accuracy", id = "e4" }]
          ]
          view   = "timeSeries"
          region = "us-east-1"
          title  = "Exactitud / Accuracy (Train vs Validation)"
          period = 60
          stat   = "Average"
        }
      },

      # ── WIDGET 9: Macro F1 Score ─────────────────────────────────────────
      {
        type   = "metric"
        x      = 0
        y      = 24
        width  = 12
        height = 6
        properties = {
          metrics = [
            [{ expression = "SEARCH('{/aws/sagemaker/TrainingJobs,TrainingJobName} MetricName=\"train:macro_f1\" TrainingJobName=~\"${var.project_name}-*\"', 'Average', 60)", label = "train:macro_f1", id = "e5" }],
            [{ expression = "SEARCH('{/aws/sagemaker/TrainingJobs,TrainingJobName} MetricName=\"val:macro_f1\" TrainingJobName=~\"${var.project_name}-*\"', 'Average', 60)", label = "val:macro_f1", id = "e6" }]
          ]
          view   = "timeSeries"
          region = "us-east-1"
          title  = "Macro F1 Score (Train vs Validation)"
          period = 60
          stat   = "Average"
        }
      },

      # ── WIDGET 10: Sensibilidad / Recall ─────────────────────────────────
      {
        type   = "metric"
        x      = 12
        y      = 24
        width  = 12
        height = 6
        properties = {
          metrics = [
            [{ expression = "SEARCH('{/aws/sagemaker/TrainingJobs,TrainingJobName} MetricName=\"train:recall\" TrainingJobName=~\"${var.project_name}-*\"', 'Average', 60)", label = "train:recall", id = "e7" }],
            [{ expression = "SEARCH('{/aws/sagemaker/TrainingJobs,TrainingJobName} MetricName=\"val:recall\" TrainingJobName=~\"${var.project_name}-*\"', 'Average', 60)", label = "val:recall", id = "e8" }]
          ]
          view   = "timeSeries"
          region = "us-east-1"
          title  = "Sensibilidad / Recall (Train vs Validation)"
          period = 60
          stat   = "Average"
        }
      },
      # ── WIDGET 11: Tabla Comparativa Final (Train vs Val vs Test) ──
      {
        type   = "log"
        x      = 0
        y      = 30
        width  = 24
        height = 6
        properties = {
          # 1. Filtra las líneas que tengan la palabra "Conjunto"
          # 2. Muestra las columnas detectadas automáticamente
          # 3. Ordena del más reciente al más antiguo para atrapar solo el último Job
          # 4. Limita a 3 resultados (Test, Val, Train)
          query   = "SOURCE '/aws/sagemaker/TrainingJobs' | filter @message like /\"Conjunto\"/ | display Conjunto, Accuracy, MacroF1, Recall | sort @timestamp desc | limit 3"
          region  = "us-east-1"
          title   = "Resumen de Evaluación Final (Último Entrenamiento)"
          view    = "table"
        }
      }

    ]
  })
}