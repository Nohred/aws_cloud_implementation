import os
import random
import shutil
from collections import defaultdict

def _allocate_stratified_counts(class_counts, target_total):
    """Asigna cupos por clase conservando proporciones y suma exacta target_total."""
    if target_total <= 0:
        raise ValueError("target_total debe ser mayor que 0")

    total_available = sum(class_counts.values())
    if total_available == 0:
        raise ValueError("No se encontraron imágenes en el dataset")
    if target_total > total_available:
        raise ValueError(
            f"target_total ({target_total}) es mayor que las imágenes disponibles ({total_available})"
        )

    expected = {}
    allocated = {}
    remainders = []

    for class_name, count in class_counts.items():
        exp = target_total * (count / total_available)
        base = int(exp)
        expected[class_name] = exp
        allocated[class_name] = base
        remainders.append((exp - base, class_name))

    missing = target_total - sum(allocated.values())
    remainders.sort(reverse=True)
    for i in range(missing):
        _, class_name = remainders[i]
        allocated[class_name] += 1

    return allocated, expected


def sample_images_stratified(source_root, target_total, seed=42):
    # Definir el nombre de la carpeta de destino
    dest_root = f"{source_root}_{target_total}_stratified"

    # Extensiones de imagen comunes
    valid_extensions = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

    # Construir índice por clase y contar imágenes
    images_by_class = defaultdict(list)
    for subdir in sorted(os.listdir(source_root)):
        subdir_path = os.path.join(source_root, subdir)
        if not os.path.isdir(subdir_path):
            continue

        class_images = [
            f for f in os.listdir(subdir_path) if f.lower().endswith(valid_extensions)
        ]
        class_images.sort()
        images_by_class[subdir].extend(class_images)

    class_counts = {class_name: len(images) for class_name, images in images_by_class.items()}
    total_images = sum(class_counts.values())

    print("\n=== CONTEO DE IMAGENES ===")
    for class_name in sorted(class_counts):
        print(f"{class_name}: {class_counts[class_name]}")
    print(f"TOTAL: {total_images}\n")

    allocated, expected = _allocate_stratified_counts(class_counts, target_total)

    if not os.path.exists(dest_root):
        os.makedirs(dest_root)
        print(f"Carpeta creada: {dest_root}")

    rng = random.Random(seed)

    print("\n=== MUESTREO ESTRATIFICADO ===")
    copied_total = 0

    for class_name in sorted(images_by_class):
        src_class_dir = os.path.join(source_root, class_name)
        dst_class_dir = os.path.join(dest_root, class_name)
        os.makedirs(dst_class_dir, exist_ok=True)

        class_images = images_by_class[class_name]
        k = allocated[class_name]
        selected = rng.sample(class_images, k) if k > 0 else []

        for img_name in selected:
            src_path = os.path.join(src_class_dir, img_name)
            dst_path = os.path.join(dst_class_dir, img_name)
            shutil.copy2(src_path, dst_path)

        copied_total += len(selected)
        proportion = (class_counts[class_name] / total_images) * 100 if total_images else 0
        print(
            f"{class_name}: copiadas {len(selected)} "
            f"(esperado={expected[class_name]:.2f}, proporcion={proportion:.2f}%)"
        )

    print(f"\nTOTAL COPIADAS: {copied_total}")
    print("¡Proceso completado con éxito!")

# --- CONFIGURACIÓN ---
NOMBRE_CARPETA_RAIZ = "garbage_classification"
TOTAL_ESTRATIFICADO = 15000
SEED = 42

sample_images_stratified(NOMBRE_CARPETA_RAIZ, TOTAL_ESTRATIFICADO, SEED)