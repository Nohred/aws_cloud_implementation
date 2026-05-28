# ─────────────────────────────────────────────────────────────────────────────
# train.py  —  Script de Entrenamiento para SageMaker (Script Mode)
# Framework : PyTorch 2.0  |  Modelo: EfficientNet-B3  |  Transfer Learning
#
# Monitoreo habilitado:
#   • TensorBoard   → curvas loss/accuracy/f1 en tiempo real durante el entrenamiento
#   • Confusion matrix → imagen PNG al final, subida por SageMaker a S3 automáticamente
#   • SageMaker Metrics → gráficas en la pestaña "Metrics" del training job en consola
# ─────────────────────────────────────────────────────────────────────────────

import os
import struct
import json
import boto3
import io
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from torchvision import models, transforms
from PIL import Image
from sklearn.metrics import f1_score, recall_score, accuracy_score, confusion_matrix
import numpy as np
import matplotlib
matplotlib.use('Agg')   # Sin display — obligatorio en contenedor SageMaker
import matplotlib.pyplot as plt
from torch.utils.tensorboard import SummaryWriter

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
_RECORDIO_MAGIC = 0xCED7230A

# ── Directorios de SageMaker ──────────────────────────────────────────────────
TENSORBOARD_DIR = os.environ.get('SM_TENSORBOARD_OUTPUT_DIR', '/opt/ml/output/tensorboard')
OUTPUT_DATA_DIR = os.environ.get('SM_OUTPUT_DATA_DIR',        '/opt/ml/output/data')
os.makedirs(TENSORBOARD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DATA_DIR, exist_ok=True)


# ── 1. Dataset RecordIO ───────────────────────────────────────────────────────
class SageMakerRecordIODataset(Dataset):
    def __init__(self, base_dir: str, filename: str, transform=None):
        self.transform = transform
        self.records: list[tuple[bytes, int]] = []

        rec_path = None
        # BLINDAJE: Solo intentar buscar si base_dir no es None
        if base_dir is not None:
            for root, _, files in os.walk(base_dir):
                if filename in files:
                    rec_path = os.path.join(root, filename)
                    print(f"[Dataset] Encontrado: {rec_path}")
                    break

        if rec_path is None:
            print(f"[Dataset] ALERTA: '{filename}' no encontrado en '{base_dir}'.")
            return

        with open(rec_path, 'rb') as f:
            while True:
                wrapper = f.read(8)
                if len(wrapper) < 8:
                    break
                magic, record_len = struct.unpack('<II', wrapper)
                if magic != _RECORDIO_MAGIC:
                    f.seek(-7, 1)
                    continue
                content = f.read(record_len)
                if len(content) < 24:
                    break
                label_float = struct.unpack('<f', content[4:8])[0]
                jpeg_bytes  = content[24:]
                self.records.append((jpeg_bytes, int(label_float)))

        print(f"[Dataset] {len(self.records):,} imágenes cargadas.")

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        jpeg_bytes, label = self.records[idx]
        img = Image.open(io.BytesIO(jpeg_bytes)).convert('RGB')
        if self.transform:
            img = self.transform(img)
        return img, label


# ── 2. Resolver num_classes desde classes.json ────────────────────────────────
def resolve_num_classes(train_dir: str, hint: int) -> tuple[int, dict]:
    if train_dir is not None:
        for root, _, files in os.walk(train_dir):
            if 'classes.json' in files:
                with open(os.path.join(root, 'classes.json')) as f:
                    class_mapping = json.load(f)
                actual = len(class_mapping)
                if actual != hint:
                    print(f"[WARNING] num_classes hint={hint} != reales={actual}. Usando {actual}.")
                print(f"[Classes] {class_mapping}")
                return actual, class_mapping
    print(f"[WARNING] classes.json no encontrado. Usando hint={hint}.")
    return hint, {}


# ── 3. Confusion matrix como figura matplotlib ────────────────────────────────
def plot_confusion_matrix(cm: np.ndarray, class_names: list, title: str) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(max(6, len(class_names)), max(5, len(class_names) - 1)))
    cm_norm = cm.astype(float) / (cm.sum(axis=1, keepdims=True) + 1e-9)

    im = ax.imshow(cm_norm, interpolation='nearest', cmap='Blues')
    plt.colorbar(im, ax=ax)

    ax.set(
        xticks=np.arange(len(class_names)),
        yticks=np.arange(len(class_names)),
        xticklabels=class_names,
        yticklabels=class_names,
        title=title,
        ylabel='Etiqueta real',
        xlabel='Etiqueta predicha',
    )
    plt.setp(ax.get_xticklabels(), rotation=45, ha='right')

    thresh = cm_norm.max() / 2.0
    for i in range(cm_norm.shape[0]):
        for j in range(cm_norm.shape[1]):
            ax.text(j, i, f'{cm[i,j]}\n({cm_norm[i,j]:.0%})',
                    ha='center', va='center',
                    color='white' if cm_norm[i, j] > thresh else 'black',
                    fontsize=8)
    fig.tight_layout()
    return fig


# ── 4. Entrenamiento ──────────────────────────────────────────────────────────
def train(args: dict):
    print(f"\n{'='*60}")
    print(f"Dispositivo : {device}")
    print(f"Épocas      : {args['epochs']}")
    print(f"Batch size  : {args['batch_size']}")
    print(f"LR          : {args['lr']}")
    print(f"TensorBoard : {TENSORBOARD_DIR}")
    print(f"Output data : {OUTPUT_DATA_DIR}")
    print(f"{'='*60}\n")

    num_classes, class_mapping = resolve_num_classes(args['train_dir'], args['num_classes_hint'])
    idx_to_class = {v: k for k, v in class_mapping.items()} if class_mapping else {i: str(i) for i in range(num_classes)}
    class_names  = [idx_to_class[i] for i in range(num_classes)]
    print(f"Clases: {class_names}\n")

    data_transforms = transforms.Compose([
        # transforms.Resize((300, 300)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])

    # INYECCIÓN: Agregar el dataset de Test
    train_dataset = SageMakerRecordIODataset(args['train_dir'], 'train.rec',      transform=data_transforms)
    val_dataset   = SageMakerRecordIODataset(args['val_dir'],   'validation.rec', transform=data_transforms)
    test_dataset  = SageMakerRecordIODataset(args['test_dir'],  'test.rec',       transform=data_transforms)

    if len(train_dataset) == 0:
        raise RuntimeError("Dataset de entrenamiento vacío.")
    if len(val_dataset) == 0:
        raise RuntimeError("Dataset de validación vacío.")

    max_label = max(label for _, label in train_dataset.records)
    if max_label >= num_classes:
        raise RuntimeError(f"Etiqueta {max_label} >= num_classes {num_classes}.")

    print(f"Train: {len(train_dataset):,}  |  Val: {len(val_dataset):,}  |  Test: {len(test_dataset):,}\n")

    train_loader = DataLoader(train_dataset, batch_size=args['batch_size'], shuffle=True,  num_workers=2)
    val_loader   = DataLoader(val_dataset,   batch_size=args['batch_size'], shuffle=False, num_workers=2)
    test_loader  = DataLoader(test_dataset,  batch_size=args['batch_size'], shuffle=False, num_workers=2)

    # ── Modelo ────────────────────────────────────────────────────────────────
    print("Cargando EfficientNet-B3 preentrenado...")
    model = models.efficientnet_b3(weights=models.EfficientNet_B3_Weights.DEFAULT)
    for param in model.parameters():
        param.requires_grad = False
    model.classifier[1] = nn.Linear(model.classifier[1].in_features, num_classes)
    model = model.to(device)

    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.classifier[1].parameters(), lr=args['lr'])

    writer = SummaryWriter(log_dir=TENSORBOARD_DIR)

    all_val_preds  = []
    all_val_labels = []

    # ── Bucle de épocas ───────────────────────────────────────────────────────
    for epoch in range(args['epochs']):
        print(f"\n--- Epoch {epoch+1}/{args['epochs']} ---")

        # TRAIN
        model.train()
        train_loss, train_preds, train_labels = 0.0, [], []
        for inputs, labels in train_loader:
            inputs, labels = inputs.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * inputs.size(0)
            _, preds = torch.max(outputs, 1)
            train_preds.extend(preds.cpu().numpy())
            train_labels.extend(labels.cpu().numpy())

        t_loss = train_loss / len(train_dataset)
        t_acc  = accuracy_score(train_labels, train_preds)
        t_f1   = f1_score(train_labels, train_preds, average='macro', zero_division=0)
        t_rec  = recall_score(train_labels, train_preds, average='macro', zero_division=0)

        print(f"train:loss={t_loss:.4f};", flush=True)
        print(f"train:accuracy={t_acc:.4f};", flush=True)
        print(f"train:macro_f1={t_f1:.4f};", flush=True)
        print(f"train:recall={t_rec:.4f};", flush=True)

        writer.add_scalar('Loss/train',     t_loss, epoch)
        writer.add_scalar('Accuracy/train', t_acc,  epoch)
        writer.add_scalar('F1/train',       t_f1,   epoch)
        writer.add_scalar('Recall/train',   t_rec,  epoch)

        # VAL
        model.eval()
        val_loss, val_preds, val_labels = 0.0, [], []
        with torch.no_grad():
            for inputs, labels in val_loader:
                inputs, labels = inputs.to(device), labels.to(device)
                outputs = model(inputs)
                loss = criterion(outputs, labels)
                val_loss += loss.item() * inputs.size(0)
                _, preds = torch.max(outputs, 1)
                val_preds.extend(preds.cpu().numpy())
                val_labels.extend(labels.cpu().numpy())

        v_loss = val_loss / len(val_dataset)
        v_acc  = accuracy_score(val_labels, val_preds)
        v_f1   = f1_score(val_labels, val_preds, average='macro', zero_division=0)
        v_rec  = recall_score(val_labels, val_preds, average='macro', zero_division=0)

        print(f"val:loss={v_loss:.4f};", flush=True)
        print(f"val:accuracy={v_acc:.4f};", flush=True)
        print(f"val:macro_f1={v_f1:.4f};", flush=True)
        print(f"val:recall={v_rec:.4f};", flush=True)

        writer.add_scalar('Loss/val',     v_loss, epoch)
        writer.add_scalar('Accuracy/val', v_acc,  epoch)
        writer.add_scalar('F1/val',       v_f1,   epoch)
        writer.add_scalar('Recall/val',   v_rec,  epoch)

        all_val_preds  = val_preds
        all_val_labels = val_labels

    # === EVALUACIÓN FINAL EN TEST ===
    print("\n--- Evaluación Final en Test ---")
    test_preds, test_labels = [], []
    test_acc, test_f1, test_rec = 0, 0, 0
    if len(test_dataset) > 0:
        model.eval()
        with torch.no_grad():
            for inputs, labels in test_loader:
                inputs, labels = inputs.to(device), labels.to(device)
                outputs = model(inputs)
                _, preds = torch.max(outputs, 1)
                test_preds.extend(preds.cpu().numpy())
                test_labels.extend(labels.cpu().numpy())

        test_acc  = accuracy_score(test_labels, test_preds)
        test_f1   = f1_score(test_labels, test_preds, average='macro', zero_division=0)
        test_rec  = recall_score(test_labels, test_preds, average='macro', zero_division=0)
        print(f"test:accuracy={test_acc:.4f}; test:macro_f1={test_f1:.4f}; test:recall={test_rec:.4f};", flush=True)


    # === REPORTE JSON PARA EL WIDGET DE CLOUDWATCH ===
    print("\n=== REPORTE_FINAL_METRICAS ===")
    print(json.dumps({"Conjunto": "1_Train", "Accuracy": round(t_acc, 4), "MacroF1": round(t_f1, 4), "Recall": round(t_rec, 4)}))
    print(json.dumps({"Conjunto": "2_Val",   "Accuracy": round(v_acc, 4), "MacroF1": round(v_f1, 4), "Recall": round(v_rec, 4)}))
    print(json.dumps({"Conjunto": "3_Test",  "Accuracy": round(test_acc, 4), "MacroF1": round(test_f1, 4), "Recall": round(test_rec, 4)}))
    print("==============================")

    # ── Confusion matrix (al terminar el entrenamiento) ───────────────────────
    cm  = confusion_matrix(all_val_labels, all_val_preds, labels=list(range(num_classes)))
    fig = plot_confusion_matrix(cm, class_names, title='Confusion Matrix — Validación (última época)')

    cm_path = os.path.join(OUTPUT_DATA_DIR, 'confusion_matrix.png')
    fig.savefig(cm_path, dpi=150, bbox_inches='tight')
    print(f"\nConfusion matrix guardada en: {cm_path}")

    writer.add_figure('ConfusionMatrix/val', fig, global_step=args['epochs'])
    writer.close()

    # ── Guardar modelo ────────────────────────────────────────────────────────
    torch.save(model.state_dict(), os.path.join(args['model_dir'], 'efficientnet_b3_waste.pt'))
    with open(os.path.join(args['model_dir'], 'classes.json'), 'w') as f:
        json.dump(class_mapping, f, indent=2)

    inference_bucket = os.environ.get('INFERENCE_SCRIPT_BUCKET')
    inference_key    = os.environ.get('INFERENCE_SCRIPT_KEY', 'inference/inference.py')

    if inference_bucket:
        code_dir = os.path.join(args['model_dir'], 'code')
        os.makedirs(code_dir, exist_ok=True)
        dest = os.path.join(code_dir, 'inference.py')

        print(f"\n📥 Descargando inference.py desde s3://{inference_bucket}/{inference_key}")
        boto3.client('s3').download_file(inference_bucket, inference_key, dest)
        print(f"✅ inference.py inyectado en: {dest}")
    else:
        print("[WARNING] INFERENCE_SCRIPT_BUCKET no definido. inference.py NO incluido en el modelo.")

    print(f"Modelo guardado en: {args['model_dir']}")
    print("\nENTRENAMIENTO COMPLETADO")

    # # ── Registrar URL del modelo en SSM Parameter Store ──────────────────────────
    # ssm_parameter = os.environ.get('MODEL_SSM_PARAMETER')
    # if ssm_parameter:
    #     output_path = os.environ.get('SM_OUTPUT_DIR', '/opt/ml/output')
    #     training_job_name = os.environ.get('TRAINING_JOB_NAME', '')
    #     s3_output_path    = os.environ.get('SM_HP_S3_OUTPUT_PATH', '')
    #     if training_job_name and s3_output_path:
    #         model_url = f"{s3_output_path.rstrip('/')}/{training_job_name}/output/model.tar.gz"
    #         print(f"\n📌 Registrando modelo en SSM: {ssm_parameter}")
    #         print(f"   URL: {model_url}")
    #         boto3.client('ssm', region_name='us-east-1').put_parameter(
    #             Name=ssm_parameter,
    #             Value=model_url,
    #             Type='String',
    #             Overwrite=True
    #         )
    #         print("✅ SSM actualizado")
    #     else:
    #         print("[WARNING] No se pudo construir model_url: faltan variables de entorno")
    # else:
    #     print("[WARNING] MODEL_SSM_PARAMETER no definido. SSM no actualizado.")
    # ── Registrar URL del modelo y métricas de retrain en SSM ────────────────
    ssm_parameter = os.environ.get('MODEL_SSM_PARAMETER')
    ssm_prefix    = os.environ.get('SSM_PREFIX')  # e.g. /waste-classifier/dev

    # Use region from env instead of hardcoded us-east-1
    aws_region        = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
    ssm_client        = boto3.client('ssm', region_name=aws_region)
    training_job_name = os.environ.get('TRAINING_JOB_NAME', '')
    s3_output_path    = os.environ.get('SM_HP_S3_OUTPUT_PATH', '')

    # ── 1. Model URL → used by endpoint update logic ──────────────────────────
    if ssm_parameter and training_job_name and s3_output_path:
        model_url = f"{s3_output_path.rstrip('/')}/{training_job_name}/output/model.tar.gz"
        print(f"\n📌 Registrando modelo en SSM: {ssm_parameter}")
        print(f"   URL: {model_url}")
        ssm_client.put_parameter(
            Name=ssm_parameter,
            Value=model_url,
            Type='String',
            Overwrite=True
        )
        print("✅ SSM modelo actualizado")
    else:
        print("[WARNING] SSM modelo no actualizado — faltan MODEL_SSM_PARAMETER, TRAINING_JOB_NAME o SM_HP_S3_OUTPUT_PATH")

    # ── 2. Retrain tracking → read by lambda_deduplicator gate ───────────────
    if ssm_prefix:
        from datetime import datetime, timezone

        # Total images seen across all splits
        total_images = len(train_dataset) + len(val_dataset) + len(test_dataset)

        try:
            ssm_client.put_parameter(
                Name=f"{ssm_prefix}/last-trained-at",
                Value=datetime.now(timezone.utc).isoformat(),
                Type='String',
                Overwrite=True
            )
            ssm_client.put_parameter(
                Name=f"{ssm_prefix}/last-trained-count",
                Value=str(total_images),
                Type='String',
                Overwrite=True
            )
            print(f"✅ SSM retrain tracking actualizado — {total_images} imágenes, timestamp registrado")
        except Exception as e:
            # Non-fatal — model is saved, don't fail the training job over this
            print(f"[WARNING] No se pudo actualizar SSM retrain tracking: {e}")
    else:
        print("[WARNING] SSM_PREFIX no definido. Retrain tracking no actualizado.")


# ── 5. Punto de entrada ───────────────────────────────────────────────────────
if __name__ == '__main__':
    args_dict = {
        'epochs':           int(os.environ.get('SM_HP_EPOCHS', 25)),
        'batch_size':       int(os.environ.get('SM_HP_BATCH_SIZE', 32)),
        'lr':               float(os.environ.get('SM_HP_LR', 0.0005)),
        'num_classes_hint': int(os.environ.get('SM_HP_NUM_CLASSES', 10)),
        'train_dir':        os.environ.get('SM_CHANNEL_TRAIN'),
        'val_dir':          os.environ.get('SM_CHANNEL_VALIDATION'),
        'test_dir':         os.environ.get('SM_CHANNEL_TEST'),    # INYECCIÓN: Agregado el entorno TEST
        'model_dir':        os.environ.get('SM_MODEL_DIR'),
    }
    train(args_dict)