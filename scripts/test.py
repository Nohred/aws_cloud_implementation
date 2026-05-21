import boto3
import json

# 1. Conéctate a SageMaker
# Asegúrate de usar la misma región donde Terraform desplegó la infraestructura
client = boto3.client('sagemaker-runtime', region_name='us-east-1')

# El nombre exacto de tu endpoint en la consola de AWS
endpoint_name = "waste-classifier-dev-classifier-endpoint" 

# 2. Carga la imagen local de prueba
image_path = "brown-glass44.jpg"

print(f"Enviando {image_path} al endpoint...")
with open(image_path, "rb") as image_file:
    payload = image_file.read() # Leemos la imagen como bytes puros

# 3. Invoca el modelo
try:
    response = client.invoke_endpoint(
        EndpointName=endpoint_name,
        ContentType='application/x-image', # Le decimos a SageMaker que le enviamos una imagen
        Body=payload
    )

    # 4. Decodifica lo que responde tu modelo
    result = json.loads(response['Body'].read().decode())
    print("¡Éxito! El modelo dice que es:")
    print(result)

except Exception as e:
    print(f"Algo falló durante la inferencia: {e}")