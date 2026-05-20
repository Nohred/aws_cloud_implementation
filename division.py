import os
import shutil

def sample_images(source_root, n):
    # Definir el nombre de la carpeta de destino
    dest_root = f"{source_root}_{n}"
    
    # Extensiones de imagen comunes
    valid_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')

    # Crear la carpeta raíz de destino si no existe
    if not os.path.exists(dest_root):
        os.makedirs(dest_root)
        print(f"Carpeta creada: {dest_root}")

    # Recorrer las subcarpetas de la carpeta raíz
    for subdir in os.listdir(source_root):
        subdir_path = os.path.join(source_root, subdir)

        # Verificar que sea una carpeta
        if os.path.isdir(subdir_path):
            # Crear la subcarpeta correspondiente en el destino
            dest_subdir_path = os.path.join(dest_root, subdir)
            os.makedirs(dest_subdir_path, exist_ok=True)

            # Listar y filtrar solo archivos de imagen, luego ordenar alfabéticamente
            images = [f for f in os.listdir(subdir_path) 
                      if f.lower().endswith(valid_extensions)]
            images.sort()

            # Seleccionar las primeras n imágenes
            selection = images[:n]

            # Copiar los archivos
            for img_name in selection:
                src_path = os.path.join(subdir_path, img_name)
                dst_path = os.path.join(dest_subdir_path, img_name)
                shutil.copy2(src_path, dst_path) # copy2 preserva metadatos

            print(f"Copiadas {len(selection)} imágenes de: {subdir}")

    print("\n¡Proceso completado con éxito!")

# --- CONFIGURACIÓN ---
NOMBRE_CARPETA_RAIZ = 'garbage_classification' 
CANTIDAD_N = 1200 # Cambia este número por el que necesites

sample_images(NOMBRE_CARPETA_RAIZ, CANTIDAD_N)