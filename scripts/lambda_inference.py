import json
import os
import boto3
import urllib.parse

s3        = boto3.client("s3")
sagemaker = boto3.client("sagemaker-runtime")
sns       = boto3.client("sns")

ENDPOINT_NAME = os.environ["SAGEMAKER_ENDPOINT_NAME"]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]


def handler(event, context):
    for record in event["Records"]:
        # SQS recibe el evento de S3 como JSON string en el body
        body = json.loads(record["body"])

        # S3 puede mandar un array de Records dentro del body
        s3_records = body.get("Records", [])
        if not s3_records:
            print(f"[SKIP] Mensaje sin Records de S3: {body}")
            continue

        for s3_record in s3_records:
            bucket = s3_record["s3"]["bucket"]["name"]
            key    = urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"])

            # Ignorar carpetas o archivos que no son imágenes
            if key.endswith("/") or not key.lower().endswith((".jpg", ".jpeg", ".png")):
                print(f"[SKIP] No es imagen: {key}")
                continue

            print(f"[INFO] Procesando s3://{bucket}/{key}")

            # 1. Descarga la imagen desde S3
            img_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

            # 2. Invoca el endpoint de SageMaker
            content_type = "image/jpeg" if key.lower().endswith((".jpg", ".jpeg")) else "image/png"
            response = sagemaker.invoke_endpoint(
                EndpointName=ENDPOINT_NAME,
                ContentType=content_type,
                Body=img_bytes,
            )
            result = json.loads(response["Body"].read())

            predicted_class = result["predicted_class"]
            confidence      = result["confidence"]

            print(f"[INFO] Predicción: {predicted_class} ({confidence:.1%})")

            # 3. Publica el resultado en SNS
            message = {
                "imagen":         f"s3://{bucket}/{key}",
                "clase":          predicted_class,
                "confianza":      f"{confidence:.1%}",
                "probabilidades": result.get("probabilities", {}),
            }
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=f"Clasificación: {predicted_class} ({confidence:.1%})",
                Message=json.dumps(message, indent=2, ensure_ascii=False),
            )
            print(f"[OK] Notificación enviada a SNS para {key}")
