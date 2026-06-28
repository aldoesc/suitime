import os
import re
from datetime import datetime
from pathlib import Path

import bcrypt

from constants import EXTENSIONES_PERMITIDAS, TAMANO_MAX_ARCHIVO


def hash_password(plain: str) -> str:
    if not plain:
        raise ValueError("La contraseña no puede estar vacía.")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


_NOMBRE_SEGURO_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitizar_nombre_archivo(nombre: str) -> str:
    """Devuelve un nombre de archivo seguro (sin separadores, sin '..')."""
    nombre = os.path.basename(nombre or "")
    nombre = nombre.replace("\\", "").replace("/", "")
    nombre = _NOMBRE_SEGURO_RE.sub("_", nombre).strip("._")
    if not nombre:
        nombre = "archivo"
    return nombre[:120]


class ArchivoInvalido(Exception):
    pass


def validar_archivo(ruta_origen: str, nombre_original: str) -> None:
    """Valida extensión y tamaño. Lanza ArchivoInvalido si falla."""
    if not ruta_origen or not os.path.isfile(ruta_origen):
        raise ArchivoInvalido("El archivo no existe.")
    ext = Path(nombre_original).suffix.lower()
    if ext not in EXTENSIONES_PERMITIDAS:
        raise ArchivoInvalido(f"Extensión no permitida: {ext or '(sin extensión)'}")
    tamano = os.path.getsize(ruta_origen)
    if tamano > TAMANO_MAX_ARCHIVO:
        mb = TAMANO_MAX_ARCHIVO / (1024 * 1024)
        raise ArchivoInvalido(f"El archivo supera el tamaño máximo de {mb:.0f} MB.")
    if tamano == 0:
        raise ArchivoInvalido("El archivo está vacío.")


def generar_ruta_destino(upload_dir: str, nombre_original: str) -> tuple[str, str]:
    """Devuelve (ruta_absoluta_destino, nombre_seguro_almacenado)."""
    nombre_seguro = sanitizar_nombre_archivo(nombre_original)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    nombre_final = f"{stamp}_{nombre_seguro}"
    ruta = os.path.join(upload_dir, nombre_final)
    upload_dir_abs = os.path.abspath(upload_dir)
    ruta_abs = os.path.abspath(ruta)
    if not ruta_abs.startswith(upload_dir_abs + os.sep):
        raise ArchivoInvalido("Ruta de destino inválida.")
    return ruta_abs, nombre_final
