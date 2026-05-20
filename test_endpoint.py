import boto3
import json

endpoint = "waste-classifier-dev-classifier-endpoint"
image_path = "garbage_classification/metal/metal_453.jpg"

runtime = boto3.client("sagemaker-runtime", region_name="us-east-1")

with open(image_path, "rb") as f:
    payload = f.read()

response = runtime.invoke_endpoint(
    EndpointName=endpoint,
    ContentType="application/x-image",
    Accept="application/json",
    Body=payload
)

result = json.loads(response["Body"].read().decode("utf-8"))
print(json.dumps(result, indent=2, ensure_ascii=False))