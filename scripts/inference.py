import json
import os
from io import BytesIO

import torch
import torch.nn as nn
from PIL import Image
from torchvision import models, transforms

IMAGE_SIZE = 300
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


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

    with open(classes_path, "r", encoding="utf-8") as f:
        classes_data = json.load(f)

    if isinstance(classes_data, dict):
        if all(str(k).isdigit() for k in classes_data.keys()):
            idx_to_class = [classes_data[str(i)] for i in range(len(classes_data))]
        elif "classes" in classes_data and isinstance(classes_data["classes"], list):
            idx_to_class = classes_data["classes"]
        else:
            idx_to_class = list(classes_data.values())
    elif isinstance(classes_data, list):
        idx_to_class = classes_data
    else:
        raise ValueError("Unsupported classes.json format")

    model = _build_model(len(idx_to_class))
    state = torch.load(model_path, map_location=DEVICE)
    if isinstance(state, dict) and "model_state_dict" in state:
        state = state["model_state_dict"]
    model.load_state_dict(state)
    model.to(DEVICE)
    model.eval()

    preprocess = transforms.Compose([
        transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406],
                             std=[0.229, 0.224, 0.225]),
    ])

    return {"model": model, "classes": idx_to_class, "preprocess": preprocess}


def input_fn(request_body, content_type):
    if content_type not in ["application/x-image", "image/jpeg", "image/png"]:
        raise ValueError(f"Unsupported content type: {content_type}")
    image = Image.open(BytesIO(request_body)).convert("RGB")
    return image


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