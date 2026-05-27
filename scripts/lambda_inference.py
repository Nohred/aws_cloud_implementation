import os, json, logging, traceback
import urllib.parse
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")

if not ENDPOINT_NAME:
    raise RuntimeError("Missing required env var: SAGEMAKER_ENDPOINT_NAME")
if not SNS_TOPIC_ARN:
    raise RuntimeError("Missing required env var: SNS_TOPIC_ARN")

s3        = boto3.client("s3")
sagemaker = boto3.client("sagemaker-runtime")
sns       = boto3.client("sns")


def handler(event, context):
    for record in event.get("Records", []):
        try:
            # Direct S3 → SQS notification format (NOT EventBridge)
            body       = json.loads(record["body"])
            s3_records = body.get("Records", [])

            if not s3_records:
                logger.info(f"No S3 records in message body, skipping: {body}")
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

                # Explicit content-type map
                ext = key.rsplit(".", 1)[-1].lower()
                content_type_map = {
                    "jpg":  "image/jpeg",
                    "jpeg": "image/jpeg",
                    "png":  "image/png",
                }
                content_type = content_type_map.get(ext, "image/jpeg")

                response     = sagemaker.invoke_endpoint(
                    EndpointName=ENDPOINT_NAME,
                    ContentType=content_type,
                    Body=img_bytes
                )
                result       = json.loads(response["Body"].read())
                pred_class   = result.get("predicted_class", "unknown")
                confidence   = result.get("confidence", 0.0)

                message = {
                    "imagen":         f"s3://{bucket}/{key}",
                    "clase":          pred_class,
                    "confianza":      f"{confidence:.1%}",
                    "probabilidades": result.get("probabilities", {}),
                }

                sns.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Subject=f"Clasificación: {pred_class} ({confidence:.1%})",
                    Message=json.dumps(message, indent=2, ensure_ascii=False)
                )
                # ← Exact strings for CloudWatch metric filters
                print("SUCCESS")
                logger.info(f"SUCCESS — {pred_class} ({confidence:.1%}) for {key}")

        except Exception as e:
            print("ERROR")
            logger.error(f"ERROR processing record: {e}\n{traceback.format_exc()}")
            # Don't re-raise here — process remaining records, let SQS
            # handle the failed message via visibility timeout retry → DLQ

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
