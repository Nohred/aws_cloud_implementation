import os, json, logging, traceback
from datetime import datetime, timezone
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Env vars validated at cold start ──────────────────────────────────────────
STATE_MACHINE_ARN     = os.environ.get("STATE_MACHINE_ARN")
RAW_BUCKET            = os.environ.get("RAW_BUCKET")
SSM_PREFIX            = os.environ.get("SSM_PREFIX")          # e.g. /waste-classifier/dev
CW_NAMESPACE          = os.environ.get("CW_NAMESPACE", "WasteClassifier/Metrics")
MIN_DAYS_BETWEEN_RUNS = int(os.environ.get("MIN_DAYS_BETWEEN_RUNS", "7"))
MIN_NEW_IMAGES        = int(os.environ.get("MIN_NEW_IMAGES", "500"))
MIN_AVG_CONFIDENCE    = float(os.environ.get("MIN_AVG_CONFIDENCE", "0.10"))

if not STATE_MACHINE_ARN:
    raise RuntimeError("Missing required env var: STATE_MACHINE_ARN")
if not RAW_BUCKET:
    raise RuntimeError("Missing required env var: RAW_BUCKET")
if not SSM_PREFIX:
    raise RuntimeError("Missing required env var: SSM_PREFIX")

# ── AWS clients ───────────────────────────────────────────────────────────────
sf  = boto3.client("stepfunctions")
ssm = boto3.client("ssm")
s3c = boto3.client("s3")
cw  = boto3.client("cloudwatch")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_ssm(name: str, default=None):
    """Read an SSM parameter, return default if it doesn't exist yet."""
    try:
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return default
    except Exception as e:
        logger.warning(f"Could not read SSM {name}: {e}")
        return default


def _count_s3_objects(bucket: str) -> int:
    """Count total objects in a bucket using paginator."""
    paginator = s3c.get_paginator("list_objects_v2")
    total = 0
    for page in paginator.paginate(Bucket=bucket):
        total += page.get("KeyCount", 0)
    return total


def _get_avg_confidence_24h() -> float | None:
    """
    Query CloudWatch for the average AvgConfidence metric over the last 24h.
    Returns None if no data points exist yet (e.g. endpoint not yet deployed).
    """
    from datetime import timedelta
    now   = datetime.now(timezone.utc)
    start = now - timedelta(hours=24)

    try:
        response = cw.get_metric_statistics(
            Namespace=CW_NAMESPACE,
            MetricName="AvgConfidence",
            StartTime=start,
            EndTime=now,
            Period=86400,           # one 24h bucket
            Statistics=["Average"],
        )
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return None
        return datapoints[0]["Average"]
    except Exception as e:
        logger.warning(f"Could not query CloudWatch AvgConfidence: {e}")
        return None


# ── 3-Condition gate ──────────────────────────────────────────────────────────
def _should_retrain() -> tuple[bool, str]:
    """
    Returns (should_retrain: bool, reason: str).

    Condition 1 — Time gate:
      At least MIN_DAYS_BETWEEN_RUNS days must have passed since last training.
      → If not met: skip regardless of other conditions.

    Condition 2 — New images threshold:
      At least MIN_NEW_IMAGES new images must have arrived since last training.

    Condition 3 — Confidence gate:
      Average prediction confidence over the last 24h must be BELOW
      MIN_AVG_CONFIDENCE (default 0.10). If it is above the threshold,
      the model is performing well enough — no retrain needed.
      If no confidence data exists yet (endpoint not deployed), skip this check.

    Retrain triggers when:  Condition 1 AND (Condition 2 OR Condition 3)
    """

    # ── Condition 1: time gate ────────────────────────────────────────────────
    last_trained_at = _get_ssm(f"{SSM_PREFIX}/last-trained-at")

    if last_trained_at:
        last_dt    = datetime.fromisoformat(last_trained_at)
        days_since = (datetime.now(timezone.utc) - last_dt).days
        if days_since < MIN_DAYS_BETWEEN_RUNS:
            return False, (
                f"Time gate not met: only {days_since}d since last training "
                f"(need {MIN_DAYS_BETWEEN_RUNS}d)."
            )
        logger.info(f"Time gate PASSED: {days_since} days since last training.")
    else:
        # First run ever — no SSM record exists, always proceed
        logger.info("No last-trained-at found — first run, proceeding.")
        return True, "First training run ever."

    # ── Condition 2: new images threshold ────────────────────────────────────
    last_trained_count = int(_get_ssm(f"{SSM_PREFIX}/last-trained-count", "0"))
    current_count      = _count_s3_objects(RAW_BUCKET)
    new_images         = current_count - last_trained_count

    logger.info(f"Image count — current: {current_count}, last trained: {last_trained_count}, new: {new_images}")

    cond2_met = new_images >= MIN_NEW_IMAGES
    if cond2_met:
        logger.info(f"Image threshold PASSED: {new_images} new images (need {MIN_NEW_IMAGES}).")

    # ── Condition 3: confidence gate ─────────────────────────────────────────
    avg_confidence = _get_avg_confidence_24h()

    if avg_confidence is None:
        # No inference data yet — endpoint not deployed or no predictions made
        logger.info("No confidence data in CloudWatch — skipping confidence check.")
        cond3_met = False
    else:
        # Retrain if confidence is BELOW threshold (model is struggling)
        cond3_met = avg_confidence < MIN_AVG_CONFIDENCE
        status    = "TRIGGERED" if cond3_met else "OK"
        logger.info(
            f"Confidence gate [{status}]: avg={avg_confidence:.3f}, "
            f"threshold={MIN_AVG_CONFIDENCE} — "
            f"{'below threshold, retrain needed' if cond3_met else 'model performing well'}."
        )

    # ── Final decision: Condition 1 AND (Condition 2 OR Condition 3) ─────────
    if cond2_met and cond3_met:
        return True, f"Both conditions met: {new_images} new images AND confidence {avg_confidence:.3f} below threshold."
    if cond2_met:
        return True, f"Image threshold met: {new_images} new images since last training."
    if cond3_met:
        return True, f"Confidence below threshold: avg={avg_confidence:.3f} < {MIN_AVG_CONFIDENCE}."

    # Build skip reason
    reasons = []
    if not cond2_met:
        reasons.append(f"only {new_images}/{MIN_NEW_IMAGES} new images")
    if not cond3_met and avg_confidence is not None:
        reasons.append(f"confidence {avg_confidence:.3f} is acceptable (>= {MIN_AVG_CONFIDENCE})")
    return False, "Retrain conditions not met: " + "; ".join(reasons) + "."


# ── Handler ───────────────────────────────────────────────────────────────────
def handler(event, context):
    try:
        records = event.get("Records", [])
        if not records:
            logger.info("No records in event, skipping.")
            return {"statusCode": 200, "body": "No records."}

        # ── Deduplication check — skip if pipeline already running ────────────
        running = sf.list_executions(
            stateMachineArn=STATE_MACHINE_ARN,
            statusFilter="RUNNING",
            maxResults=1
        )["executions"]

        if running:
            logger.info(f"Pipeline already running: {running[0]['executionArn']}. Skipping {len(records)} record(s).")
            return {"statusCode": 200, "body": "Pipeline already running — skipped."}

        # ── 3-condition gate ──────────────────────────────────────────────────
        should_train, reason = _should_retrain()
        logger.info(f"Retrain decision: {should_train} — {reason}")

        if not should_train:
            return {"statusCode": 200, "body": reason}

        # ── Parse EventBridge event from SQS body (original working logic) ────
        try:
            sqs_body     = json.loads(records[0]["body"])
            s3_key       = sqs_body["detail"]["object"]["key"]

            # Preserve original tar.gz vs folder logic
            if s3_key.endswith(".tar.gz") or s3_key.endswith(".tgz"):
                target_batch = s3_key
            else:
                target_batch = s3_key.split("/")[0]

        except Exception as parse_err:
            logger.warning(f"Could not extract batch prefix: {parse_err}. Defaulting to root.")
            target_batch = ""

        # key name MUST match $.target_batch in glue_flow.json
        sfn_input = {"target_batch": target_batch}

        response = sf.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            input=json.dumps(sfn_input)
        )
        logger.info(f"Pipeline started for batch '{target_batch}': {response['executionArn']}")
        return {"statusCode": 200, "body": f"Pipeline started for batch: {target_batch}. Reason: {reason}"}

    except Exception as e:
        logger.error(f"Handler failed: {e}\n{traceback.format_exc()}")
        raise



# def handler(event, context):
#     """
#     Deduplicador de ejecuciones de Step Functions con extracción de lote incremental.
#     Recibe eventos de SQS (que vienen de EventBridge ← S3).
#     Solo dispara el pipeline si NO hay una ejecución ya en curso.
#     """
#     sf = boto3.client('stepfunctions')
#     state_machine_arn = os.environ['STATE_MACHINE_ARN']

#     # 1. Consultar si ya existe una ejecución corriendo
#     running = sf.list_executions(
#         stateMachineArn=state_machine_arn,
#         statusFilter='RUNNING',
#         maxResults=1
#     )['executions']

#     if running:
#         print(f"Pipeline ya en ejecución: {running[0]['executionArn']}. Descartando lote de {len(event['Records'])} evento(s).")
#         return {'statusCode': 200, 'body': 'Pipeline already running — skipped.'}

#     try:
#         # El mensaje de SQS viene serializado como string en 'body'
#         sqs_body = json.loads(event['Records'][0]['body'])
#         # Extraemos la S3 key completa (ejemplo: "comprimidos/prueba_final.tar.gz")
#         s3_key = sqs_body['detail']['object']['key']
        
#         # 🚀 CORRECCIÓN: Si es un archivo comprimido, pasamos el key completo para que Glue sepa qué descargar.
#         # Si no, dejamos la lógica de carpetas original.
#         if s3_key.endswith('.tar.gz') or s3_key.endswith('.tgz'):
#             target_folder = s3_key
#         else:
#             target_folder = s3_key.split('/')[0]
#     except Exception as e:
#         print(f"No se pudo extraer el prefijo del lote: {e}. Se procesará la raíz por defecto.")
#         target_folder = ""

#     # clean_folder = target_folder.replace('.tar.gz', '').replace('.tgz', '')

#     # 3. Empaquetar la carpeta de la prueba como el INPUT de la máquina de estados
#     sfn_input = {
#         "target_batch": target_folder
#     }
    
#     # Iniciar la ejecución inyectando el JSON dinámico
#     response = sf.start_execution(
#         stateMachineArn=state_machine_arn,
#         input=json.dumps(sfn_input) # Envía: {"target_batch": "prueba3"}
#     )
    
#     print(f"Pipeline iniciado para el lote '{target_folder}': {response['executionArn']}")
#     return {'statusCode': 200, 'body': f"Pipeline started for batch: {target_folder}"}