import sys
import boto3
import io
from datetime import datetime
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from PIL import Image

args = getResolvedOptions(sys.argv, ['JOB_NAME'])
sc = SparkContext()
glueContext = GlueContext(sc)
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

SOURCE_BUCKET = "bucket-source-garbage-classification"
TARGET_BUCKET = "bucket-target-garbage-classification"
TODAY = datetime.utcnow().strftime("%Y-%m-%d")

s3 = boto3.client('s3')
paginator = s3.get_paginator('list_objects_v2')

for page in paginator.paginate(Bucket=SOURCE_BUCKET, Prefix=f"{TODAY}/"):
    for obj in page.get('Contents', []):
        key = obj['Key']
        if not key.lower().endswith(('.jpg', '.jpeg', '.png')):
            continue

        # Descargar
        img_data = s3.get_object(Bucket=SOURCE_BUCKET, Key=key)['Body'].read()

        # Procesar: convertir a RGB y resize 224x224
        img = Image.open(io.BytesIO(img_data)).convert("RGB").resize((224, 224))

        # Guardar en target manteniendo estructura de carpetas
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        buffer.seek(0)

        s3.put_object(
            Bucket=TARGET_BUCKET,
            Key=key,
            Body=buffer.getvalue(),
            ContentType='image/jpeg'
        )
        print(f"Procesada: {key}")

job.commit()