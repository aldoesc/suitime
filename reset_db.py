"""Script para borrar la base de datos local y empezar de cero.

Uso:
    python reset_db.py            # pregunta antes de borrar
    python reset_db.py --force    # borra sin preguntar

Qué hace:
  - Elimina el archivo `gestor_operacional.db` (dejando BD vacía para el próximo arranque).
  - Opcionalmente vacía la carpeta `uploads/` (pregunta también).
  - No toca `cloud_config.json` ni `.env_local`.

Al volver a ejecutar `main.py`, la BD se creará de cero y el seed dejará
únicamente al admin Christian Balbin (cmbalbin@hotmail.com).
"""
from __future__ import annotations

import os
import shutil
import sys

from database import DB_NAME, UPLOAD_DIR


def main() -> int:
    force = "--force" in sys.argv
    keep_uploads = "--keep-uploads" in sys.argv

    print(f"BD: {DB_NAME}")
    print(f"Uploads: {UPLOAD_DIR}")
    print()

    if not force:
        resp = input("¿Eliminar la base de datos? [s/N]: ").strip().lower()
        if resp not in ("s", "si", "y", "yes"):
            print("Cancelado.")
            return 1

    if os.path.exists(DB_NAME):
        os.remove(DB_NAME)
        print(f"✓ BD eliminada: {DB_NAME}")
    else:
        print(f"· BD no existía (ya estaba limpia): {DB_NAME}")

    if not keep_uploads and os.path.isdir(UPLOAD_DIR):
        if force:
            resp = "s"
        else:
            resp = input(
                f"¿Eliminar también el contenido de '{UPLOAD_DIR}'? [s/N]: "
            ).strip().lower()
        if resp in ("s", "si", "y", "yes"):
            for entry in os.listdir(UPLOAD_DIR):
                ruta = os.path.join(UPLOAD_DIR, entry)
                try:
                    if os.path.isfile(ruta):
                        os.remove(ruta)
                    elif os.path.isdir(ruta):
                        shutil.rmtree(ruta)
                except OSError as e:
                    print(f"  ! No se pudo borrar {ruta}: {e}")
            print(f"✓ Carpeta uploads vaciada.")

    print()
    print("Listo. Al iniciar `python main.py` se creará la BD de cero.")
    print("  Usuario admin: admin")
    print("  Contraseña:    admin123  (cámbiala tras el primer login)")
    print("  Nombre:        Christian Balbin")
    print("  Email:         cmbalbin@hotmail.com")
    print("  Teléfono:      +51 993 457 560")
    return 0


if __name__ == "__main__":
    sys.exit(main())
