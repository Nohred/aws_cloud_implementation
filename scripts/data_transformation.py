#!/usr/bin/env python3
import sys
from awsglue.utils import getResolvedOptions
import boto3
import cv2
import os


def resize_and_upload(input_bucket: str, input_key: str, output_bucket: str, output_key: str, size: int = 256):
    # Descargar imagen
    s3 = boto3.client('s3')
    s3.download_file(input_bucket, input_key, '/tmp/input.jpg')
    
    # Procesar
    image = cv2.imread('/tmp/input.jpg')
    if image is None:
        raise ValueError("No se pudo leer la imagen")

    resized = cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)
    
    # Subir resultado
    cv2.imwrite('/tmp/output.jpg', resized)
    s3.upload_file('/tmp/output.jpg', output_bucket, output_key)
    
    print(f"Procesada: s3://{input_bucket}/{input_key} -> s3://{output_bucket}/{output_key}")


def main():
    args = getResolvedOptions(sys.argv, [
        'JOB_NAME',
        'input_bucket',
        'input_key',
        'output_bucket',
        'output_key',
        'size'
    ])

    resize_and_upload(
        input_bucket=args['input_bucket'],
        input_key=args['input_key'],
        output_bucket=args['output_bucket'],
        output_key=args['output_key'],
        size=int(args['size'])
    )


if __name__ == "__main__":
    main()