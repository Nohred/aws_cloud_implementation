import json
import os
from io import BytesIO

import torch
import torch.nn as nn
from PIL import Image
from torchvision import models, transforms
import numpy as np

IMAGE_SIZE = 300
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def _apply_clahe_numpy(img_pil: Image.Image) -> Image.Image:
    """Pure numpy CLAHE on the L channel of LAB colorspace."""
    rgb = np.array(img_pil, dtype=np.float32) / 255.0

    # RGB → LAB (approximate, no cv2 needed)
    def rgb_to_lab(rgb):
        # sRGB → linear
        mask = rgb > 0.04045
        rgb_lin = np.where(mask, ((rgb + 0.055) / 1.055) ** 2.4, rgb / 12.92)
        # linear RGB → XYZ (D65)
        M = np.array([[0.4124564, 0.3575761, 0.1804375],
                      [0.2126729, 0.7151522, 0.0721750],
                      [0.0193339, 0.1191920, 0.9503041]])
        xyz = rgb_lin @ M.T
        xyz /= np.array([0.95047, 1.00000, 1.08883])
        # XYZ → LAB
        eps = 0.008856
        xyz = np.where(xyz > eps, xyz ** (1/3), 7.787 * xyz + 16/116)
        L = (116 * xyz[..., 1] - 16)
        return L

    L = rgb_to_lab(rgb)  # 0–100 range

    # CLAHE via histogram equalization in tiles
    def clahe_channel(channel, clip_limit=2.0, tile_size=8):
        h, w = channel.shape
        out = np.zeros_like(channel)
        th = h // tile_size
        tw = w // tile_size
        for i in range(tile_size):
            for j in range(tile_size):
                y0, y1 = i * th, (i + 1) * th if i < tile_size - 1 else h
                x0, x1 = j * tw, (j + 1) * tw if j < tile_size - 1 else w
                tile = channel[y0:y1, x0:x1]
                hist, bins = np.histogram(tile.flatten(), bins=256, range=(0, 100))
                limit = clip_limit * tile.size / 256
                excess = np.maximum(hist - limit, 0).sum()
                hist = np.minimum(hist, limit)
                hist += excess / 256
                cdf = np.cumsum(hist)
                cdf = (cdf - cdf.min()) / (cdf.max() - cdf.min() + 1e-9) * 100
                out[y0:y1, x0:x1] = np.interp(tile.flatten(), bins[:-1], cdf).reshape(tile.shape)
        return out

    L_eq = clahe_channel(L)

    # Reconstruct: replace L, keep original colors via PIL enhance
    l_orig = np.array(img_pil.convert('L'), dtype=np.float32)
    l_new  = (L_eq / 100.0 * 255.0).clip(0, 255).astype(np.uint8)
    ratio  = l_new.astype(np.float32) / (l_orig + 1e-9)
    ratio  = np.clip(ratio, 0.5, 2.0)
    rgb_arr = np.array(img_pil, dtype=np.float32)
    rgb_eq  = np.clip(rgb_arr * ratio[..., np.newaxis], 0, 255).astype(np.uint8)
    return Image.fromarray(rgb_eq)

def _build_model(num_classes: int):
    weights = None
    try:
        weights = models.EfficientNet_B3_Weights.DEFAULT
    except Exception:
        weights = None
    model = models.efficientnet_b3(weights=weights)
    in_features = model.classifier[1].in_features
    model.classifier[1] = nn.Linear(in_features, num_classes)
    return model

def model_fn(model_dir):
    classes_path = os.path.join(model_dir, "classes.json")
    model_path = os.path.join(model_dir, "efficientnet_b3_waste.pt")

    try:
        with open(classes_path, "r", encoding="utf-8") as f:
            classes_data = json.load(f)
            
        # If it's a list: ["cardboard", "glass"]
        if isinstance(classes_data, list):
            idx_to_class = classes_data
        # If it's a dict mapping string to index: {"cardboard": 0, "glass": 1}
        elif isinstance(classes_data, dict) and all(isinstance(v, int) for v in classes_data.values()):
            # Sort the items by their integer values to ensure correct array order
            sorted_items = sorted(classes_data.items(), key=lambda x: x[1])
            idx_to_class = [item[0] for item in sorted_items]
        # If it's a dict mapping index to string: {"0": "cardboard", "1": "glass"}
        elif isinstance(classes_data, dict):
            sorted_keys = sorted(classes_data.keys(), key=int)
            idx_to_class = [classes_data[k] for k in sorted_keys]
        else:
            raise ValueError("Unsupported classes.json format")
            
    except Exception as e:
        print(f"Warning: Failed to load classes.json: {str(e)}. Falling back to numeric classes.")
        idx_to_class = [f"class_{i}" for i in range(10)]

    model = _build_model(len(idx_to_class))
    state = torch.load(model_path, map_location=DEVICE)
    if isinstance(state, dict) and "model_state_dict" in state:
        state = state["model_state_dict"]
    model.load_state_dict(state)
    model.to(DEVICE)
    model.eval()

    preprocess = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406],
                             std=[0.229, 0.224, 0.225]),
    ])

    return {"model": model, "classes": idx_to_class, "preprocess": preprocess}

def input_fn(request_body, content_type):
    if content_type not in ["application/x-image", "image/jpeg", "image/png"]:
        raise ValueError(f"Unsupported content type: {content_type}")

    img = Image.open(BytesIO(request_body)).convert("RGB")
    img = img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.LANCZOS)
    img = _apply_clahe_numpy(img)
    return img

def predict_fn(input_data, model_artifacts):
    model = model_artifacts["model"]
    classes = model_artifacts["classes"]
    preprocess = model_artifacts["preprocess"]

    tensor = preprocess(input_data).unsqueeze(0).to(DEVICE)
    with torch.no_grad():
        logits = model(tensor)
        probs = torch.softmax(logits, dim=1)[0].cpu()

    top_idx = int(torch.argmax(probs).item())
    top_prob = float(probs[top_idx].item())
    probabilities = {
        classes[i]: round(float(probs[i].item()), 6)
        for i in range(len(classes))
    }

    return {
        "predicted_class": classes[top_idx],
        "confidence": round(top_prob, 6),
        "probabilities": probabilities,
    }

def output_fn(prediction, accept):
    if accept not in ["application/json", "*/*"]:
        raise ValueError(f"Unsupported accept type: {accept}")
    return json.dumps(prediction), "application/json"