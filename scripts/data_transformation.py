#!/usr/bin/env python3
import argparse
from pathlib import Path
import cv2


def resize_image(input_path: Path, output_path: Path, size: int) -> None:
    image = cv2.imread(str(input_path))
    if image is None:
        raise ValueError(f"No se pudo leer la imagen: {input_path}")

    resized = cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not cv2.imwrite(str(output_path), resized):
        raise ValueError(f"No se pudo guardar la imagen: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Resize simple de imágenes con OpenCV")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--size", type=int, default=256)
    args = parser.parse_args()

    image_paths = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp"):
        image_paths.extend(args.input_dir.glob(ext))

    if not image_paths:
        print(f"No se encontraron imágenes en {args.input_dir}")
        return

    for input_path in image_paths:
        output_path = args.output_dir / input_path.name
        resize_image(input_path, output_path, args.size)
        print(f"OK: {input_path.name} -> {output_path}")

if __name__ == "__main__":
    main()