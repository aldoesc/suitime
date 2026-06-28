"""
MCP SUItime — servidor de consulta operacional (solo lectura, vía API cloud).

Lee la base de datos compartida del gestor SUItime a través de su API cloud
(Cloudflare Worker + D1), de modo que ve los datos de TODOS los usuarios y
dispositivos, no sólo los de este equipo. Expone herramientas MCP para
responder sobre estado de equipos, fallas, mantenimientos, operatividad,
notas activas y tareas (terminadas / en curso).

Es de SOLO LECTURA: únicamente hace GET a los endpoints `/{tabla}?since=`.

Configuración (variables de entorno, opcionales):
    SUITIME_API_URL   URL base de la API (default: la de cloud_config.json).
    SUITIME_API_KEY   API key (default: la de cloud_config.json).
    SUITIME_TTL       Segundos de caché de los datos en memoria (default 30).

Ejecutar:
    python -m mcp_suitime.server          # stdio (para clientes MCP)
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #

_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cargar_config_local() -> dict:
    """Lee cloud_config.json del proyecto, si existe (para api_url/api_key)."""
    ruta = os.path.join(_PROYECTO, "cloud_config.json")
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


_CFG = _cargar_config_local()
API_URL = (os.environ.get("SUITIME_API_URL")
           or _CFG.get("api_url")
           or "https://api-suitime.hexa38.com").rstrip("/")
API_KEY = (os.environ.get("SUITIME_API_KEY")
           or _CFG.get("api_key")
           or "")
TTL = int(os.environ.get("SUITIME_TTL", "30"))

_TIPOS_OBS = {
    "nota": "Nota",
    "falla": "Falla",
    "reparacion": "Reparación",
    "cierre": "Cierre",
}
_ESTADOS_ACTIVOS = ("pendiente", "en progreso")
_ESTADOS_CERRADOS = ("completada", "validada")

_TABLAS = (
    "usuarios", "objetivos", "tareas",
    "tarea_observaciones", "tarea_sesiones", "tarea_rechazos",
    "tarea_adjuntos", "tarea_asignaciones",
)


# --------------------------------------------------------------------------- #
# Acceso a la API con caché en memoria
# --------------------------------------------------------------------------- #

_cache: dict[str, tuple[float, list[dict]]] = {}

# Campos sensibles que NUNCA deben salir del fetch (ni a memoria ni a caché).
# La API expone el hash bcrypt en /usuarios; lo eliminamos en el ingreso.
_CAMPOS_SENSIBLES = {"password_hash", "password"}


def _sanear(fila: dict) -> dict:
    """Quita campos sensibles (hashes de contraseña) de una fila."""
    return {k: v for k, v in fila.items() if k not in _CAMPOS_SENSIBLES}


def _api_get(tabla: str) -> list[dict]:
    """GET /{tabla}?since= → lista de filas (saneadas). Cacheado TTL segundos."""
    ahora = time.time()
    hit = _cache.get(tabla)
    if hit and (ahora - hit[0]) < TTL:
        return hit[1]
    url = f"{API_URL}/{tabla}?since="
    req = urllib.request.Request(url, headers={
        "X-API-Key": API_KEY,
        "X-Dispositivo-ID": "mcp-suitime-reader",
        "User-Agent": "GestorSUI-MCP/1.0",
    }, method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    data = [_sanear(f) for f in ((payload or {}).get("data") or [])]
    _cache[tabla] = (ahora, data)
    return data


def _vivo(fila: dict) -> bool:
    """True si la fila no está soft-deleted."""
    return not (fila.get("deleted_at") or fila.get("eliminada"))


def _idx_usuarios() -> dict[str, dict]:
    return {u["uuid"]: u for u in _api_get("usuarios") if u.get("uuid")}


def _nombre(idx: dict[str, dict], uuid: str | None) -> str | None:
    u = idx.get(uuid or "")
    return u.get("nombre") if u else None


def _segundos(inicio: str | None, fin: str | None) -> int:
    """Duración en segundos entre dos timestamps ISO/SQL. 0 si falta alguno."""
    if not inicio or not fin:
        return 0
    try:
        fmt = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
        return max(0, int((fmt(fin) - fmt(inicio)).total_seconds()))
    except Exception:
        return 0


def _hms(segs: int) -> str:
    return f"{segs // 3600}h {(segs % 3600) // 60}m"


def _ts(s: str | None) -> str:
    """Normaliza un timestamp para comparar/ordenar. '' si None."""
    return (s or "").replace(" ", "T")


# --------------------------------------------------------------------------- #
# Servidor
# --------------------------------------------------------------------------- #

mcp = FastMCP("suitime")


@mcp.tool()
def resumen_operacional() -> dict:
    """Panorama general del estado operativo de TODOS los equipos/usuarios:
    conteo de tareas por estado, fallas abiertas, observaciones por tipo,
    operadores y sesiones de trabajo abiertas ahora mismo. Primera herramienta
    a usar para un reporte global de operatividad.
    """
    tareas_v = [t for t in _api_get("tareas") if _vivo(t)]
    obs_v = [o for o in _api_get("tarea_observaciones") if _vivo(o)]
    usuarios = [u for u in _api_get("usuarios") if _vivo(u)]
    sesiones = _api_get("tarea_sesiones")

    por_estado: dict[str, int] = {}
    for t in tareas_v:
        e = t.get("estado") or "?"
        por_estado[e] = por_estado.get(e, 0) + 1
    por_tipo: dict[str, int] = {}
    for o in obs_v:
        et = _TIPOS_OBS.get(o.get("tipo"), o.get("tipo") or "?")
        por_tipo[et] = por_tipo.get(et, 0) + 1

    return {
        "fuente": API_URL,
        "total_tareas": len(tareas_v),
        "tareas_activas": sum(v for k, v in por_estado.items()
                              if k in _ESTADOS_ACTIVOS),
        "tareas_por_estado": por_estado,
        "observaciones_por_tipo": por_tipo,
        "fallas_abiertas": len(_fallas_abiertas()),
        "operadores": sum(1 for u in usuarios if u.get("rol") == "operador"),
        "total_usuarios": len(usuarios),
        "sesiones_de_trabajo_abiertas_ahora": sum(
            1 for s in sesiones if not s.get("fin_utc")),
    }


@mcp.tool()
def tareas(estado: str = "activas", limite: int = 50) -> list[dict]:
    """Lista tareas con responsable, fechas y prioridad.

    estado: 'activas' (pendiente + en progreso), 'pendiente', 'en progreso',
        'completada', 'validada', 'pendiente_validacion', 'cerradas'
        (completada + validada) o 'todas'.
    limite: máximo de filas (default 50).
    """
    estado = (estado or "activas").strip().lower()
    if estado == "activas":
        permitidos = set(_ESTADOS_ACTIVOS)
    elif estado == "cerradas":
        permitidos = set(_ESTADOS_CERRADOS)
    elif estado == "todas":
        permitidos = None
    else:
        permitidos = {estado}

    idx = _idx_usuarios()
    filas = [t for t in _api_get("tareas")
             if _vivo(t) and (permitidos is None or t.get("estado") in permitidos)]
    # Orden: por vencimiento (las sin fecha al final), luego por creación desc.
    filas.sort(key=lambda t: (t.get("fecha_vencimiento") is None,
                              t.get("fecha_vencimiento") or "",
                              ), reverse=False)
    out = []
    for t in filas[:limite]:
        out.append({
            "uuid": t.get("uuid"),
            "titulo": t.get("titulo"),
            "descripcion": t.get("descripcion"),
            "estado": t.get("estado"),
            "prioridad": t.get("prioridad"),
            "fecha_inicio": t.get("fecha_inicio"),
            "fecha_vencimiento": t.get("fecha_vencimiento"),
            "recurrencia": t.get("recurrencia"),
            "creador": _nombre(idx, t.get("creador_uuid")),
        })
    return out


def _fallas_abiertas() -> list[dict]:
    """Observaciones tipo 'falla' sin una reparación/cierre posterior en la
    misma tarea."""
    obs = [o for o in _api_get("tarea_observaciones") if _vivo(o)]
    # Por tarea, fecha de la última reparación/cierre.
    cierre_por_tarea: dict[str, str] = {}
    for o in obs:
        if o.get("tipo") in ("reparacion", "cierre"):
            tu = o.get("tarea_uuid")
            f = _ts(o.get("fecha_utc"))
            if f > cierre_por_tarea.get(tu, ""):
                cierre_por_tarea[tu] = f
    abiertas = []
    for o in obs:
        if o.get("tipo") != "falla":
            continue
        tu = o.get("tarea_uuid")
        if _ts(o.get("fecha_utc")) <= cierre_por_tarea.get(tu, ""):
            continue
        abiertas.append(o)
    abiertas.sort(key=lambda o: _ts(o.get("fecha_utc")), reverse=True)
    return abiertas


@mcp.tool()
def fallas_activas() -> list[dict]:
    """Fallas reportadas que aún NO tienen una reparación o cierre registrado.
    Indica qué equipos siguen con problemas pendientes. Cada falla trae la
    tarea asociada y quién la reportó.
    """
    idx_u = _idx_usuarios()
    idx_t = {t["uuid"]: t for t in _api_get("tareas") if t.get("uuid")}
    out = []
    for o in _fallas_abiertas():
        t = idx_t.get(o.get("tarea_uuid"), {})
        out.append({
            "tarea": t.get("titulo"),
            "tarea_uuid": o.get("tarea_uuid"),
            "estado_tarea": t.get("estado"),
            "texto": o.get("texto"),
            "fecha": o.get("fecha_utc"),
            "reportado_por": _nombre(idx_u, o.get("usuario_uuid")),
        })
    return out


@mcp.tool()
def notas(tipo: str = "todas", dias: int = 30, limite: int = 100) -> list[dict]:
    """Notas y observaciones registradas en las tareas — el registro vivo de lo
    que pasa con cada equipo: fallas detectadas, reparaciones, cierres y notas.

    tipo: 'todas', 'nota', 'falla', 'reparacion' o 'cierre'.
    dias: ventana hacia atrás (default 30; usa 0 para sin límite).
    limite: máximo de filas.
    """
    tipo = (tipo or "todas").strip().lower()
    idx_u = _idx_usuarios()
    idx_t = {t["uuid"]: t for t in _api_get("tareas") if t.get("uuid")}
    desde = ""
    if dias and dias > 0:
        desde = _ts((datetime.now(timezone.utc) - timedelta(days=dias))
                    .isoformat())
    out = []
    for o in _api_get("tarea_observaciones"):
        if not _vivo(o):
            continue
        if tipo != "todas" and o.get("tipo") != tipo:
            continue
        if desde and _ts(o.get("fecha_utc")) < desde:
            continue
        t = idx_t.get(o.get("tarea_uuid"), {})
        out.append({
            "tipo": o.get("tipo"),
            "tarea": t.get("titulo"),
            "tarea_uuid": o.get("tarea_uuid"),
            "texto": o.get("texto"),
            "fecha": o.get("fecha_utc"),
            "autor": _nombre(idx_u, o.get("usuario_uuid")),
        })
    out.sort(key=lambda x: _ts(x["fecha"]), reverse=True)
    return out[:limite]


@mcp.tool()
def historial_tarea(tarea_uuid: str) -> dict:
    """Línea de tiempo completa de una tarea: datos, observaciones (notas,
    fallas, reparaciones, cierres), rechazos de validación y tiempo total
    trabajado. Útil para auditar el ciclo de vida de un equipo o trabajo.
    Usa el `tarea_uuid` que devuelven las otras herramientas.
    """
    idx_u = _idx_usuarios()
    t = next((x for x in _api_get("tareas") if x.get("uuid") == tarea_uuid), None)
    if not t:
        return {"error": f"No existe la tarea {tarea_uuid}"}

    obs = sorted(
        [o for o in _api_get("tarea_observaciones")
         if o.get("tarea_uuid") == tarea_uuid and _vivo(o)],
        key=lambda o: _ts(o.get("fecha_utc")))
    rechazos = sorted(
        [r for r in _api_get("tarea_rechazos")
         if r.get("tarea_uuid") == tarea_uuid],
        key=lambda r: _ts(r.get("fecha_utc")))
    sesiones = [s for s in _api_get("tarea_sesiones")
                if s.get("tarea_uuid") == tarea_uuid]
    total_segs = sum(_segundos(s.get("inicio_utc"), s.get("fin_utc"))
                     for s in sesiones)

    return {
        "tarea": {
            "titulo": t.get("titulo"),
            "descripcion": t.get("descripcion"),
            "estado": t.get("estado"),
            "prioridad": t.get("prioridad"),
            "creador": _nombre(idx_u, t.get("creador_uuid")),
            "fecha_inicio": t.get("fecha_inicio"),
            "fecha_vencimiento": t.get("fecha_vencimiento"),
        },
        "observaciones": [{
            "tipo": o.get("tipo"), "texto": o.get("texto"),
            "fecha": o.get("fecha_utc"),
            "autor": _nombre(idx_u, o.get("usuario_uuid")),
        } for o in obs],
        "rechazos_validacion": [{
            "observacion": r.get("observacion"), "fecha": r.get("fecha_utc"),
            "admin": _nombre(idx_u, r.get("admin_uuid")),
        } for r in rechazos],
        "sesiones_de_trabajo": len(sesiones),
        "tiempo_total_trabajado": _hms(total_segs),
    }


@mcp.tool()
def carga_operadores(dias: int = 30) -> list[dict]:
    """Carga de trabajo por operador en los últimos `dias`: tareas asignadas,
    tareas activas, tiempo trabajado y si tiene una sesión abierta ahora.
    Útil para evaluar operatividad y distribución del equipo.
    """
    usuarios = [u for u in _api_get("usuarios")
                if _vivo(u) and u.get("rol") == "operador"]
    idx_t = {t["uuid"]: t for t in _api_get("tareas") if t.get("uuid")}
    asign = _api_get("tarea_asignaciones")
    sesiones = _api_get("tarea_sesiones")
    desde = ""
    if dias and dias > 0:
        desde = _ts((datetime.now(timezone.utc) - timedelta(days=dias))
                    .isoformat())

    out = []
    for u in usuarios:
        uu = u.get("uuid")
        tareas_asig = [a.get("tarea_uuid") for a in asign
                       if a.get("usuario_uuid") == uu]
        activas = sum(1 for tu in tareas_asig
                      if (idx_t.get(tu, {}).get("estado") in _ESTADOS_ACTIVOS))
        mis_ses = [s for s in sesiones if s.get("usuario_uuid") == uu
                   and (not desde or _ts(s.get("inicio_utc")) >= desde)]
        segs = sum(_segundos(s.get("inicio_utc"), s.get("fin_utc"))
                   for s in mis_ses)
        out.append({
            "operador": u.get("nombre"),
            "cargo": u.get("cargo"),
            "tareas_asignadas": len(tareas_asig),
            "tareas_activas": activas,
            "tiempo_trabajado": _hms(segs),
            "sesion_abierta_ahora": any(not s.get("fin_utc") for s in mis_ses),
        })
    out.sort(key=lambda x: (x["tareas_activas"]), reverse=True)
    return out


@mcp.tool()
def buscar(texto: str, limite: int = 40) -> dict:
    """Busca un término (equipo, código, palabra clave) en títulos y
    descripciones de tareas y en el texto de las observaciones/notas.
    """
    q = (texto or "").lower()
    idx_t = {t["uuid"]: t for t in _api_get("tareas") if t.get("uuid")}
    tareas_m = []
    for t in _api_get("tareas"):
        if not _vivo(t):
            continue
        if q in (t.get("titulo") or "").lower() or q in (t.get("descripcion") or "").lower():
            tareas_m.append({
                "uuid": t.get("uuid"), "titulo": t.get("titulo"),
                "estado": t.get("estado"), "prioridad": t.get("prioridad"),
            })
    obs_m = []
    for o in _api_get("tarea_observaciones"):
        if not _vivo(o):
            continue
        if q in (o.get("texto") or "").lower():
            obs_m.append({
                "tarea": idx_t.get(o.get("tarea_uuid"), {}).get("titulo"),
                "tarea_uuid": o.get("tarea_uuid"),
                "tipo": o.get("tipo"), "texto": o.get("texto"),
                "fecha": o.get("fecha_utc"),
            })
    return {"tareas": tareas_m[:limite], "observaciones": obs_m[:limite]}


if __name__ == "__main__":
    mcp.run()
