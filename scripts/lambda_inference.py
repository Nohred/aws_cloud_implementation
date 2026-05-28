import os, json, logging, traceback
import urllib.parse
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Env vars validated at cold start ──────────────────────────────────────────
ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
CODE_BUCKET   = os.environ.get("CODE_BUCKET")
CLASSES_KEY   = os.environ.get("CLASSES_KEY", "models/classes.json")
CW_NAMESPACE = os.environ.get("CW_NAMESPACE", "WasteClassifier/Metrics")

if not ENDPOINT_NAME:
    raise RuntimeError("Missing required env var: SAGEMAKER_ENDPOINT_NAME")
if not SNS_TOPIC_ARN:
    raise RuntimeError("Missing required env var: SNS_TOPIC_ARN")
if not CODE_BUCKET:
    raise RuntimeError("Missing required env var: CODE_BUCKET")

# ── AWS clients ───────────────────────────────────────────────────────────────
s3        = boto3.client("s3")
sagemaker = boto3.client("sagemaker-runtime")
sns       = boto3.client("sns")
cw        = boto3.client("cloudwatch")

# ── Load class index → name mapping once at cold start ───────────────────────
# classes.json is written by train.py as {"cardboard": 0, "glass": 1, ...}
# We invert it to {0: "cardboard", 1: "glass", ...}
CLASS_NAMES = {}
try:
    obj = s3.get_object(Bucket=CODE_BUCKET, Key=CLASSES_KEY)
    raw = json.loads(obj["Body"].read())
    CLASS_NAMES = {v: k for k, v in raw.items()}
    logger.info(f"Loaded {len(CLASS_NAMES)} classes: {list(CLASS_NAMES.values())}")
except Exception as e:
    logger.warning(f"Could not load classes.json from s3://{CODE_BUCKET}/{CLASSES_KEY}: {e}. Will use numeric indices.")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _confidence_label(confidence: float) -> str:
    if confidence >= 0.80:
        return "✅ HIGH"
    elif confidence >= 0.50:
        return "⚠️  MEDIUM"
    else:
        return "🔴 LOW — model may be uncertain"


def _build_message(bucket: str, key: str, pred_index, confidence: float, probs_raw: dict) -> tuple[str, str]:
    """Returns (subject, plain_text_body)."""
    pred_name = CLASS_NAMES.get(int(pred_index) if str(pred_index).isdigit() else pred_index,
                                 f"class_{pred_index}")

    # Map numeric probability keys to class names, sort descending
    probs_named = {}
    for k, v in probs_raw.items():
        name = CLASS_NAMES.get(int(k) if str(k).isdigit() else k, f"class_{k}")
        probs_named[name] = round(v * 100, 1)
    probs_sorted = dict(sorted(probs_named.items(), key=lambda x: x[1], reverse=True))

    conf_label = _confidence_label(confidence)
    conf_emoji = conf_label[0]  # ✅ ⚠️ or 🔴

    # Top 3 candidates
    top3 = list(probs_sorted.items())[:3]
    top3_lines = "\n".join(
        f"   {i+1}. {name:<16} {pct:.1f}%"
        for i, (name, pct) in enumerate(top3)
    )

    # ASCII bar chart for full breakdown
    bar_lines = "\n".join(
        f"   {name:<16} {'█' * int(pct / 5):<20} {pct:.1f}%"
        for name, pct in probs_sorted.items()
    )

    filename = key.split("/")[-1]

    body = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WASTE CLASSIFIER — INFERENCE RESULT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  File      : {filename}
  Location  : s3://{bucket}/{key}

  Prediction  : {pred_name.upper()}
  Confidence  : {confidence:.1%}  {conf_label}

  Top 3 candidates:
{top3_lines}

  Full breakdown:
{bar_lines}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".strip()

    subject = f"{conf_emoji} Waste: {pred_name.upper()} ({confidence:.1%})"
    return subject, body


# ── Handler ───────────────────────────────────────────────────────────────────
def handler(event, context):
    for record in event.get("Records", []):
        try:
            # Direct S3 → SQS notification format (NOT EventBridge)
            body       = json.loads(record["body"])
            s3_records = body.get("Records", [])

            if not s3_records:
                logger.info(f"No S3 records in message body, skipping.")
                continue

            for s3_record in s3_records:
                bucket = s3_record["s3"]["bucket"]["name"]
                # unquote_plus handles spaces and special chars in S3 keys
                key    = urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"])

                # Skip folders or non-image files
                if key.endswith("/") or not key.lower().endswith((".jpg", ".jpeg", ".png")):
                    logger.info(f"Skipping non-image key: {key}")
                    continue

                logger.info(f"Processing s3://{bucket}/{key}")

                img_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

                ext = key.rsplit(".", 1)[-1].lower()
                content_type = {
                    "jpg":  "image/jpeg",
                    "jpeg": "image/jpeg",
                    "png":  "image/png",
                }.get(ext, "image/jpeg")

                response   = sagemaker.invoke_endpoint(
                    EndpointName=ENDPOINT_NAME,
                    ContentType=content_type,
                    Body=img_bytes
                )
                result     = json.loads(response["Body"].read())

                pred_class = result.get("predicted_class", -1)
                confidence = result.get("confidence", 0.0)
                probs_raw  = result.get("probabilities", {})

                subject, message_body = _build_message(
                    bucket, key, pred_class, confidence, probs_raw
                )

                sns.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Subject=subject,
                    Message=message_body
                )

                # ── Emit confidence metric for retraining condition 3 ─────────
                # Each prediction pushes one data point to CloudWatch.
                # The deduplicator queries the 24h average of this metric
                # and triggers retraining only if it drops below MIN_AVG_CONFIDENCE.
                try:
                    cw.put_metric_data(
                        Namespace=CW_NAMESPACE,
                        MetricData=[{
                            "MetricName": "AvgConfidence",
                            "Value":      confidence,          # float 0.0–1.0
                            "Unit":       "None",
                            "Dimensions": [{
                                "Name":  "EndpointName",
                                "Value": ENDPOINT_NAME
                            }]
                        }]
                    )
                    logger.info(f"Emitted AvgConfidence={confidence:.4f} to {CW_NAMESPACE}")
                except Exception as cw_err:
                    # Non-fatal — a CloudWatch failure must never block inference
                    logger.warning(f"Could not emit confidence metric: {cw_err}")

                # ← Exact strings for CloudWatch metric filters (do not change)
                print("SUCCESS")
                logger.info(f"SUCCESS — {subject} for {key}")

        except Exception as e:
            # ← Feeds the InferenceFailure CloudWatch metric filter
            print("ERROR")
            logger.error(f"ERROR processing record: {e}\n{traceback.format_exc()}")
            # Don't re-raise — process remaining records
            # SQS visibility timeout handles retry → DLQ after 3 failures

    return {"statusCode": 200, "body": "Inferencia completada"}

# def handler(event, context):
#     for record in event["Records"]:
#         try:
#             # SQS recibe el evento de S3 como JSON string en el body
#             body = json.loads(record["body"])

#             # S3 puede mandar un array de Records dentro del body
#             s3_records = body.get("Records", [])
#             if not s3_records:
#                 print(f"[SKIP] Mensaje sin Records de S3: {body}")
#                 continue

#             for s3_record in s3_records:
#                 bucket = s3_record["s3"]["bucket"]["name"]
#                 key    = urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"])

#                 # Ignorar carpetas o archivos que no son imágenes
#                 if key.endswith("/") or not key.lower().endswith((".jpg", ".jpeg", ".png")):
#                     print(f"[SKIP] No es imagen: {key}")
#                     continue

#                 print(f"[INFO] Procesando s3://{bucket}/{key}")

#                 # 1. Descarga la imagen desde S3
#                 img_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

#                 # 2. Invoca el endpoint de SageMaker
#                 content_type = "image/jpeg" if key.lower().endswith((".jpg", ".jpeg")) else "image/png"
#                 response = sagemaker.invoke_endpoint(
#                     EndpointName=ENDPOINT_NAME,
#                     ContentType=content_type,
#                     Body=img_bytes,
#                 )
#                 result = json.loads(response["Body"].read())

#                 predicted_class = result["predicted_class"]
#                 confidence      = result["confidence"]

#                 print(f"[INFO] Predicción: {predicted_class} ({confidence:.1%})")

#                 # 3. Publica el resultado en SNS
#                 message = {
#                     "imagen":         f"s3://{bucket}/{key}",
#                     "clase":          predicted_class,
#                     "confianza":      f"{confidence:.1%}",
#                     "probabilidades": result.get("probabilities", {}),
#                 }
#                 sns.publish(
#                     TopicArn=SNS_TOPIC_ARN,
#                     Subject=f"Clasificación: {predicted_class} ({confidence:.1%})",
#                     Message=json.dumps(message, indent=2, ensure_ascii=False),
#                 )
#                 print(f"[OK] Notificación enviada a SNS para {key}")
                
#                 # ¡Esta métrica alimenta la línea verde de tu Dashboard!
#                 print("SUCCESS") 

#         except Exception as e:
#             print(f"[EXCEPTION] Ocurrió un error procesando el mensaje: {str(e)}")
#             # ¡Esta métrica alimenta la línea roja de tu Dashboard!
#             print("ERROR")

#     # El return debe ir hasta el final, totalmente alineado a la izquierda (fuera de los 'for')
#     return {
#         'statusCode': 200,
#         'body': 'Inferencia completada'
#     }
