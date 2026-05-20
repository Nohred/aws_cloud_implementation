import boto3
import os
import json

def handler(event, context):
    """
    Deduplicador de ejecuciones de Step Functions con extracción de lote incremental.
    Recibe eventos de SQS (que vienen de EventBridge ← S3).
    Solo dispara el pipeline si NO hay una ejecución ya en curso.
    """
    sf = boto3.client('stepfunctions')
    state_machine_arn = os.environ['STATE_MACHINE_ARN']

    # 1. Consultar si ya existe una ejecución corriendo
    running = sf.list_executions(
        stateMachineArn=state_machine_arn,
        statusFilter='RUNNING',
        maxResults=1
    )['executions']

    if running:
        print(f"Pipeline ya en ejecución: {running[0]['executionArn']}. Descartando lote de {len(event['Records'])} evento(s).")
        return {'statusCode': 200, 'body': 'Pipeline already running — skipped.'}

    try:
        # El mensaje de SQS viene serializado como string en 'body'
        sqs_body = json.loads(event['Records'][0]['body'])
        # Extraemos la S3 key completa (ejemplo: "comprimidos/prueba_final.tar.gz")
        s3_key = sqs_body['detail']['object']['key']
        
        # 🚀 CORRECCIÓN: Si es un archivo comprimido, pasamos el key completo para que Glue sepa qué descargar.
        # Si no, dejamos la lógica de carpetas original.
        if s3_key.endswith('.tar.gz') or s3_key.endswith('.tgz'):
            target_folder = s3_key
        else:
            target_folder = s3_key.split('/')[0]
    except Exception as e:
        print(f"No se pudo extraer el prefijo del lote: {e}. Se procesará la raíz por defecto.")
        target_folder = ""

    # clean_folder = target_folder.replace('.tar.gz', '').replace('.tgz', '')

    # 3. Empaquetar la carpeta de la prueba como el INPUT de la máquina de estados
    sfn_input = {
        "target_batch": target_folder
    }
    
    # Iniciar la ejecución inyectando el JSON dinámico
    response = sf.start_execution(
        stateMachineArn=state_machine_arn,
        input=json.dumps(sfn_input) # Envía: {"target_batch": "prueba3"}
    )
    
    print(f"Pipeline iniciado para el lote '{target_folder}': {response['executionArn']}")
    return {'statusCode': 200, 'body': f"Pipeline started for batch: {target_folder}"}