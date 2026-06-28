"""Utilidades de sincronización: conversión de filas locales (con IDs numéricos)
a payloads cloud (con UUIDs), generación de timestamps y helpers de encolado.

Los repos locales usan IDs autoincrement para FKs, pero el cloud usa UUIDs.
Al encolar una escritura, reemplazamos los *_id por los *_uuid correspondientes.
"""
import logging
import uuid as _uuid
from datetime import datetime, timezone

from database import get_db

log = logging.getLogger(__name__)


def nuevo_uuid() -> str:
    return str(_uuid.uuid4())


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _uuid_from_id(conn, tabla: str, id_valor) -> str | None:
    if id_valor is None:
        return None
    row = conn.execute(
        f"SELECT uuid FROM {tabla} WHERE id=?", (id_valor,)
    ).fetchone()
    return row["uuid"] if row and row["uuid"] else None


# Mapeos: por cada tabla local, qué columnas tienen _id a traducir a _uuid
# y con qué tabla se resuelven.
_MAP_IDS = {
    "tareas": [
        ("creador_id", "usuarios", "creador_uuid"),
        ("validado_por", "usuarios", "validado_por_uuid"),
        ("objetivo_id", "objetivos", "objetivo_uuid"),
    ],
    "objetivos": [
        ("responsable_id", "usuarios", "responsable_uuid"),
    ],
    "tarea_sesiones": [
        ("tarea_id", "tareas", "tarea_uuid"),
        ("usuario_id", "usuarios", "usuario_uuid"),
    ],
    "tarea_rechazos": [
        ("tarea_id", "tareas", "tarea_uuid"),
        ("admin_id", "usuarios", "admin_uuid"),
    ],
    "tarea_observaciones": [
        ("tarea_id", "tareas", "tarea_uuid"),
        ("usuario_id", "usuarios", "usuario_uuid"),
    ],
    "tarea_adjuntos": [
        ("tarea_id", "tareas", "tarea_uuid"),
        ("usuario_id", "usuarios", "usuario_uuid"),
    ],
    "tarea_participantes": [
        ("tarea_id", "tareas", "tarea_uuid"),
        ("usuario_id", "usuarios", "usuario_uuid"),
    ],
    "tarea_asignaciones": [
        ("tarea_id", "tareas", "tarea_uuid"),
        ("usuario_id", "usuarios", "usuario_uuid"),
    ],
    "usuarios": [],
}

# Columnas locales que no se envían al cloud (IDs autoincrement internos)
_OMIT_EN_CLOUD = {"id", "fecha_registro", "fecha_creacion", "fecha_validacion"}

# Omits adicionales por tabla. Se usa cuando una columna local existe pero el
# worker Cloudflare aún no la tiene en su schema (la upsert reflectiva del
# worker falla con "no such column" si la enviamos). Cuando se aplique la
# migración cloud correspondiente, se puede vaciar este dict para que la
# papelera de notas se sincronice entre dispositivos.
_OMIT_EN_CLOUD_POR_TABLA: dict[str, set[str]] = {
    "tarea_observaciones": {"deleted_at"},
}


def _construir_payload(conn, tabla: str, row) -> dict | None:
    """Convierte una fila local a payload cloud. Devuelve None si no hay uuid."""
    if not row:
        return None
    data = dict(row)
    if not data.get("uuid"):
        return None
    # Resolver IDs → UUIDs
    for col_id, tabla_rel, col_uuid in _MAP_IDS.get(tabla, []):
        valor_id = data.pop(col_id, None)
        data[col_uuid] = _uuid_from_id(conn, tabla_rel, valor_id)
    # Filtrar columnas internas (globales + por-tabla)
    omit = _OMIT_EN_CLOUD | _OMIT_EN_CLOUD_POR_TABLA.get(tabla, set())
    return {k: v for k, v in data.items() if k not in omit}


def asegurar_uuid(conn, tabla: str, row_id: int) -> str:
    """Garantiza que la fila tiene uuid; si no, lo genera y lo graba."""
    row = conn.execute(
        f"SELECT uuid FROM {tabla} WHERE id=?", (row_id,)
    ).fetchone()
    if row and row["uuid"]:
        return row["uuid"]
    nuevo = nuevo_uuid()
    conn.execute(f"UPDATE {tabla} SET uuid=? WHERE id=?", (nuevo, row_id))
    return nuevo


def marcar_actualizada(conn, tabla: str, row_id: int, timestamp: str | None = None) -> str:
    """Actualiza updated_at y devuelve el valor usado."""
    ts = timestamp or ahora_iso()
    conn.execute(f"UPDATE {tabla} SET updated_at=? WHERE id=?", (ts, row_id))
    return ts


def encolar_fila(tabla: str, row_id: int, endpoint: str | None = None) -> None:
    """Llama al cloud_sync singleton para encolar esta fila. Silencioso si no hay sync."""
    try:
        from cloud_sync import get_sync
    except Exception:
        return
    sync = get_sync()
    if not sync or not sync.habilitado:
        return
    with get_db() as conn:
        row = conn.execute(f"SELECT * FROM {tabla} WHERE id=?", (row_id,)).fetchone()
        if not row:
            return
        payload = _construir_payload(conn, tabla, row)
    if not payload:
        return
    ep = endpoint or f"/{tabla}"
    sync.queue(tabla, ep, "POST", payload)


def encolar_fila_por_uuid(tabla: str, uuid_valor: str, endpoint: str | None = None) -> None:
    try:
        from cloud_sync import get_sync
    except Exception:
        return
    sync = get_sync()
    if not sync or not sync.habilitado:
        return
    with get_db() as conn:
        row = conn.execute(f"SELECT * FROM {tabla} WHERE uuid=?", (uuid_valor,)).fetchone()
        if not row:
            return
        payload = _construir_payload(conn, tabla, row)
    if not payload:
        return
    ep = endpoint or f"/{tabla}"
    sync.queue(tabla, ep, "POST", payload)


def encolar_relacion(tabla: str, tarea_id: int, usuario_id: int,
                     estado: str | None = None,
                     fecha_completado: str | None = None) -> None:
    """Para relaciones compuestas (participantes/asignaciones) sin UUID propio."""
    try:
        from cloud_sync import get_sync
    except Exception:
        return
    sync = get_sync()
    if not sync or not sync.habilitado:
        return
    with get_db() as conn:
        tarea_uuid = _uuid_from_id(conn, "tareas", tarea_id)
        usuario_uuid = _uuid_from_id(conn, "usuarios", usuario_id)
    if not tarea_uuid or not usuario_uuid:
        return
    payload = {
        "tarea_uuid": tarea_uuid,
        "usuario_uuid": usuario_uuid,
        "updated_at": ahora_iso(),
    }
    if estado is not None:
        payload["estado"] = estado
    if fecha_completado is not None:
        payload["fecha_completado"] = fecha_completado
    sync.queue(tabla, f"/{tabla}", "POST", payload)


def encolar_borrado_relacion(tabla: str, tarea_id: int, usuario_id: int) -> None:
    try:
        from cloud_sync import get_sync
    except Exception:
        return
    sync = get_sync()
    if not sync or not sync.habilitado:
        return
    with get_db() as conn:
        tarea_uuid = _uuid_from_id(conn, "tareas", tarea_id)
        usuario_uuid = _uuid_from_id(conn, "usuarios", usuario_id)
    if not tarea_uuid or not usuario_uuid:
        return
    sync.queue(tabla, f"/{tabla}/delete", "POST",
               {"tarea_uuid": tarea_uuid, "usuario_uuid": usuario_uuid})


def encolar_borrado(tabla: str, uuid_valor: str) -> None:
    try:
        from cloud_sync import get_sync
    except Exception:
        return
    sync = get_sync()
    if not sync or not sync.habilitado:
        return
    sync.queue(tabla, f"/{tabla}/{uuid_valor}", "DELETE", {})
