# ─────────────────────────────────────────────────────────────────────────────
# glue_etl.py  —  Glue 5.1 / PySpark
# ─────────────────────────────────────────────────────────────────────────────

from awsglue.context import GlueContext
from pyspark.context import SparkContext
from awsglue.utils import getResolvedOptions
import sys
import struct
import json
import boto3
from PIL import Image
import io
from datetime import datetime
import numpy as np

args = getResolvedOptions(sys.argv, ['JOB_NAME', 'input_bucket', 'output_bucket', 'size'])

sc          = SparkContext()
glueContext = GlueContext(sc)

INPUT_BUCKET  = args['input_bucket']
OUTPUT_BUCKET = args['output_bucket']
SIZE          = int(args.get('size', '256'))

def _get_optional(name, default=''):
    flag = f'--{name}'
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return default

TARGET_BATCH = _get_optional('target_batch', '')
#OUTPUT_PREFIX = f"recordio/{TARGET_BATCH.strip('/')}" if TARGET_BATCH else f"recordio/{RUN_TIMESTAMP}"

RUN_TIMESTAMP = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

OUTPUT_PREFIX = f"{TARGET_BATCH.strip('/')}" if TARGET_BATCH else f"{RUN_TIMESTAMP}"
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}

# Carpetas del sistema que NUNCA son clases válidas.
# La categoría es la carpeta inmediata que contiene el archivo (parts[-2]),
# así que solo necesitamos verificar que ESA carpeta no sea del sistema.
SYSTEM_FOLDERS = {
    '_MACOSX', '__MACOSX', '__pycache__',
    '.DS_Store', 'recordio', 'code', 'models',
}

_RECORDIO_MAGIC = 0xCED7230A

def _pack_record(label: float, jpeg_bytes: bytes) -> bytes:
    header  = struct.pack('<IfQQ', 0, label, 0, 0)
    content = header + jpeg_bytes
    return struct.pack('<II', _RECORDIO_MAGIC, len(content)) + content

def apply_clahe_numpy(pil_img: Image.Image) -> Image.Image:
    """CLAHE on the L channel using pure numpy — no OpenCV needed."""
    img_np = np.array(pil_img, dtype=np.float32)

    # Convert RGB → YUV-style luminance (ITU-R BT.601)
    r, g, b = img_np[:,:,0], img_np[:,:,1], img_np[:,:,2]
    L = (0.299 * r + 0.587 * g + 0.114 * b).astype(np.uint8)

    # Tile-based histogram equalization (CLAHE approximation)
    tile_h, tile_w = max(1, L.shape[0] // 8), max(1, L.shape[1] // 8)
    L_eq = np.zeros_like(L)

    for i in range(8):
        for j in range(8):
            y0, y1 = i * tile_h, (i+1) * tile_h if i < 7 else L.shape[0]
            x0, x1 = j * tile_w, (j+1) * tile_w if j < 7 else L.shape[1]
            tile = L[y0:y1, x0:x1]
            hist, _ = np.histogram(tile.flatten(), bins=256, range=(0, 256))
            # Clip limit (equivalent to clipLimit=2.0 in OpenCV)
            clip_limit = int(2.0 * tile.size / 256)
            excess = np.maximum(hist - clip_limit, 0).sum()
            hist = np.minimum(hist, clip_limit) + excess // 256
            cdf = hist.cumsum()
            cdf_min = cdf[cdf > 0].min()
            lut = np.round(
                (cdf - cdf_min) / (tile.size - cdf_min) * 255
            ).astype(np.uint8)
            L_eq[y0:y1, x0:x1] = lut[tile]

    # Blend equalized L back: scale RGB channels proportionally
    L_orig = L.astype(np.float32) + 1e-6
    scale = L_eq.astype(np.float32) / L_orig
    scale = np.clip(scale, 0, 2)[:, :, np.newaxis]
    result = np.clip(img_np * scale, 0, 255).astype(np.uint8)
    return Image.fromarray(result, 'RGB')

def get_category(path: str) -> str:
    """
    Extrae la categoría de cualquier ruta S3, sin importar la profundidad.

    La categoría es SIEMPRE la carpeta inmediatamente anterior al archivo:
        s3://bucket/cardboard/img.jpg          → "cardboard"
        s3://bucket/prueba11/cardboard/img.jpg → "cardboard"
        s3a://bucket/prueba11/cardboard/img.jpg → "cardboard"

    Esto es equivalente a lo que hacía el código original con path.split('/')[-2],
    y funciona independientemente del protocolo (s3:// o s3a://) o profundidad.
    """
    return path.rstrip('/').split('/')[-2]


def is_valid(path: str) -> bool:
    """
    Acepta un archivo si:
      1. Tiene extensión de imagen válida.
      2. La carpeta que lo contiene (categoría) no es una carpeta del sistema.

    No impone ninguna restricción de profundidad — funciona igual con o sin
    subcarpeta de batch y sin importar el protocolo que use Glue internamente.
    """
    parts = path.rstrip('/').split('/')

    # Necesitamos al menos: [bucket, categoria, archivo]
    if len(parts) < 3:
        return False

    filename = parts[-1]
    category = parts[-2]

    # Descartar si no tiene extensión
    if '.' not in filename:
        return False

    ext = '.' + filename.rsplit('.', 1)[-1].lower()
    if ext not in IMAGE_EXTENSIONS:
        return False

    # Descartar carpetas del sistema
    if category.startswith('_') or category.startswith('.'):
        return False
    if category in SYSTEM_FOLDERS:
        return False

    return True


def process_image(pair, class_mapping, size):
    path, portable = pair
    try:
        category = get_category(path)
        label_id = class_mapping[category]

        if hasattr(portable, 'read'):
            data = portable.read()
        elif isinstance(portable, (bytes, bytearray, memoryview)):
            data = bytes(portable)
        else:
            raise TypeError(f'Payload no soportado: {type(portable)!r}')

        # 1. Open + force RGB
        img = Image.open(io.BytesIO(data)).convert('RGB')
        # 2. LANCZOS resize
        img = img.resize((size, size), Image.LANCZOS)
        # 3. CLAHE (pure numpy — no OpenCV)
        img = apply_clahe_numpy(img)
        # 4. Save as JPEG q=95
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=95)

        return (path, ('SUCCESS', _pack_record(float(label_id), buf.getvalue())))
    except Exception as exc:
        return (path, ('FAILED', str(exc)))
    
def main():
    import tarfile
    import os
    
    # 🚀 CORRECCIÓN: Detectar si el lote es un archivo comprimido
    is_compressed = TARGET_BATCH.endswith('.tar.gz') or TARGET_BATCH.endswith('.tgz')
    
    if is_compressed:
        # Extrae el nombre limpio del archivo (ej. "comprimidos/prueba_final.tar.gz" -> "prueba_final")
        batch_name = TARGET_BATCH.split('/')[-1].replace('.tar.gz', '').replace('.tgz', '')
        
        # Ruta temporal en S3 para que Spark lea de forma nativa con sus asteriscos
        s3_working_dir = f"prueba_unzipped_{batch_name}"
        s3_read_path = f's3://{INPUT_BUCKET}/{s3_working_dir}/*/*'
        
        # Descarga y descompresión en el nodo local de Glue
        s3_client = boto3.client('s3')
        local_tar = f"/tmp/{batch_name}.tar.gz"
        local_extract = f"/tmp/{batch_name}/"
        
        print(f"[Glue Mode] Descargando archivo comprimido: s3://{INPUT_BUCKET}/{TARGET_BATCH}...")
        s3_client.download_file(INPUT_BUCKET, TARGET_BATCH, local_tar)
        
        print(f"[Glue Mode] Extrayendo localmente en {local_extract}...")
        with tarfile.open(local_tar, "r:gz") as tar:
            tar.extractall(path=local_extract)
            
        print(f"[Glue Mode] Subiendo imágenes organizadas a S3 temporal: s3://{INPUT_BUCKET}/{s3_working_dir}/")
        # Subimos la estructura de regreso a S3 para que sc.binaryFiles trabaje distribuido
        for root, _, files in os.walk(local_extract):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    category = os.path.basename(root)
                    if category not in SYSTEM_FOLDERS and not category.startswith('.'):
                        local_file_path = os.path.join(root, file)
                        s3_key = f"{s3_working_dir}/{category}/{file}"
                        with open(local_file_path, 'rb') as f:
                            s3_client.put_object(Bucket=INPUT_BUCKET, Key=s3_key, Body=f.read())
        print(f"[Glue Mode] Sincronización a S3 completada con éxito.")
    else:
        # Mantiene tu flujo original intacto por si subes carpetas abiertas
        if TARGET_BATCH:
            batch_prefix = TARGET_BATCH.strip('/')
            s3_read_path = f's3://{INPUT_BUCKET}/{batch_prefix}/*/*'
        else:
            s3_read_path = f's3://{INPUT_BUCKET}/*/*'

    print("=" * 60)
    print(f"JOB INICIADO  | {RUN_TIMESTAMP}")
    print(f"  s3_read_path  = {s3_read_path}")
    print(f"  target_batch  = '{TARGET_BATCH}'")
    print(f"  output_prefix = {OUTPUT_PREFIX}")
    print(f"  image_size    = {SIZE}x{SIZE} px")
    print("=" * 60)

    # El resto de tu lógica RDD original continúa intacta y feliz de recibir los paths correctos
    rdd_all = sc.binaryFiles(s3_read_path)

    # ── DIAGNÓSTICO (visible en CloudWatch) ───────────────────────────────────
    sample_raw = rdd_all.map(lambda p: p[0]).take(5)
    print("\n[DIAGNÓSTICO] Primeras 5 rutas crudas de sc.binaryFiles:")
    for p in sample_raw:
        print(f"  path     : {p} | valida: {is_valid(p)}")
    print()
    # ─────────────────────────────────────────────────────────────────────────

    rdd = rdd_all.filter(lambda pair: is_valid(pair[0]))
    sample_count = rdd.count()
    print(f"Archivos válidos tras filtrado: {sample_count:,}")

    if sample_count == 0:
        raise RuntimeError(f"0 imágenes válidas en '{s3_read_path}'. Revisa CloudWatch Logs.")

    # ── Detección dinámica de clases ──────────────────────────────────────────
    unique_categories = rdd.map(lambda pair: get_category(pair[0])).distinct().collect()
    unique_categories.sort()
    CLASS_MAPPING = {cat: idx for idx, cat in enumerate(unique_categories)}
    print(f"\nCLASES ({len(CLASS_MAPPING)}): {CLASS_MAPPING}")

    # ── Guardar classes.json ──────────────────────────────────────────────────
    s3 = boto3.client('s3')
    s3.put_object(
        Bucket=OUTPUT_BUCKET,
        Key=f"{OUTPUT_PREFIX}/classes.json",
        Body=json.dumps(CLASS_MAPPING, indent=2).encode(),
        ContentType='application/json'
    )
    print(f"  [classes.json] → s3://{OUTPUT_BUCKET}/{OUTPUT_PREFIX}/classes.json")

    # ── Procesamiento distribuido ─────────────────────────────────────────────
    processed_rdd = rdd.map(lambda pair: process_image(pair, CLASS_MAPPING, SIZE)).cache()
    successful = processed_rdd.filter(lambda x: x[1][0] == 'SUCCESS').map(lambda x: x[1][1])

    train_rdd, val_rdd, test_rdd = successful.randomSplit([0.70, 0.15, 0.15], seed=42)
    train_records = train_rdd.collect()
    val_records   = val_rdd.collect()
    test_records  = test_rdd.collect()

    def write_channel(records, channel_name):
        if not records:
            print(f"  [{channel_name}] vacío — no creado.")
            return
        key  = f"{OUTPUT_PREFIX}/{channel_name}.rec"
        body = b"".join(records)
        s3.put_object(Bucket=OUTPUT_BUCKET, Key=key, Body=body, ContentType='application/x-recordio')
        print(f"  [{channel_name:12}] {len(records):,} imgs → s3://{OUTPUT_BUCKET}/{key}")

    print("\nEscribiendo canales RecordIO:")
    write_channel(train_records, 'train')
    write_channel(val_records,   'validation')
    write_channel(test_records,  'test')

    # Limpieza automática para no dejar basura desempaquetada en el bucket raw
    if is_compressed:
        print(f"\n[Glue] Limpiando carpeta temporal desempacada en S3...")
        objects_to_delete = s3.list_objects_v2(Bucket=INPUT_BUCKET, Prefix=s3_working_dir)
        if 'Contents' in objects_to_delete:
            delete_keys = [{'Key': obj['Key']} for obj in objects_to_delete['Contents']]
            s3.delete_objects(Bucket=INPUT_BUCKET, Delete={'Objects': delete_keys})

    failures = processed_rdd.filter(lambda x: x[1][0] == 'FAILED').collect()
    total    = len(train_records) + len(val_records) + len(test_records) + len(failures)
    print(f"\nRESUMEN — total:{total:,}  ok:{total-len(failures):,}  fail:{len(failures):,}")
    print("\nJOB COMPLETADO")

if __name__ == '__main__':
    main()