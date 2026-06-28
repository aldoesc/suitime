"""Motor de sincronización offline-first con Cloudflare Workers + D1.

Patrón:
  - Cada escritura local llama a `sync.queue(tabla, endpoint, metodo, datos)`.
  - Un hilo de fondo cada N segundos: check_connection → push_queued → pull_all.
  - Push: envía pendientes (reintentos con backoff, descarta tras 10 fallos).
  - Pull: consulta `?since=<iso>` y upserta por uuid en BD local (last-write-wins
    por `updated_at`).

Las tablas locales deben tener columnas `uuid` y `updated_at` (ver database.py).
"""
import json
import logging
import os
import platform
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as _uuid
from datetime import datetime, timezone

from database import TABLAS_SYNC, data_dir, get_db

log = logging.getLogger(__name__)

CONFIG_FILE = os.environ.get(
    "GESTOR_CLOUD_CONFIG",
    os.path.join(data_dir(), "cloud_config.json"),
)

_DEFAULTS = {
    "dispositivo_id": "",
    "api_url": "https://api-suitime.hexa38.com",
    # La API key real se define en cloud_config.json (no versionado) o vía
    # la variable de entorno SUITIME_API_KEY. No incluir secretos en el código.
    "api_key": os.environ.get("SUITIME_API_KEY", ""),
    "sync_interval": 30,
    "last_sync": "",  # ISO UTC del último pull exitoso
    # Activo por defecto: así un móvil recién instalado empieza a syncronizar
    # sin exigir al usuario entrar al diálogo. Sigue pudiendo deshabilitarse
    # manualmente desde "Sincronización en la nube".
    "habilitado": True,
    # Integración con Nextcloud Talk (servidor auto-hospedado):
    #   - nextcloud_url      : raíz del Nextcloud (ej. https://nuc.midominio.com)
    #   - talk_token         : token de la sala a la que se envían mensajes
    #   - nextcloud_usuario  : usuario bot que postea
    #   - nextcloud_app_pass : app-password generado en Nextcloud para el bot
    "nextcloud_url": "",
    "talk_token": "",
    "nextcloud_usuario": "",
    "nextcloud_app_pass": "",
    # Legado: se conserva por compatibilidad con configs v1.0.0 con Slack.
    "slack_url": "",
}


def _device_id_default() -> str:
    """Genera un id estable para el dispositivo. Prefiere hostname + random
    corto. Si no se puede leer el hostname, solo UUID corto."""
    try:
        host = socket.gethostname() or platform.node() or "dispositivo"
    except Exception:
        host = "dispositivo"
    host = "".join(c for c in host if c.isalnum() or c in "_-").lower()[:20]
    sufijo = _uuid.uuid4().hex[:6]
    return f"{host or 'dispositivo'}_{sufijo}"


def cargar_config() -> dict:
    data: dict = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception as e:
            log.warning("Error leyendo %s: %s — usando defaults", CONFIG_FILE, e)
            data = {}
    merged = {**_DEFAULTS, **data}
    # Autocompletar dispositivo_id si está vacío (primer arranque en un
    # dispositivo nuevo). Se persiste para que no cambie en cada arranque.
    if not (merged.get("dispositivo_id") or "").strip():
        merged["dispositivo_id"] = _device_id_default()
        try:
            guardar_config(merged)
            log.info("dispositivo_id autogenerado: %s", merged["dispositivo_id"])
        except Exception:
            log.exception("No se pudo persistir dispositivo_id autogenerado")
    return merged


def guardar_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Mapeo tabla → clave primaria en cloud (para upsert). Las tablas relacionales
# usan claves compuestas y endpoints especiales.
_PK_SIMPLE = {
    "usuarios", "objetivos", "tareas",
    "tarea_sesiones", "tarea_rechazos", "tarea_observaciones", "tarea_adjuntos",
}

# ---------------------------------------------------------------------------
# Traducción inversa (PULL): cloud usa *_uuid en los payloads, la BD local usa
# *_id (INTEGER autoincrement). Al hacer pull necesitamos resolver cada uuid
# entrante al id local de su fila. Este mapa es el reverso de
# `sync_helpers._MAP_IDS` y debe mantenerse sincronizado con él.
#
# Formato:  tabla_local -> [ (columna_uuid_remota, columna_id_local, tabla_ref) ]
# ---------------------------------------------------------------------------
_MAP_UUIDS_INVERSO = {
    "tareas": [
        ("creador_uuid", "creador_id", "usuarios"),
        ("validado_por_uuid", "validado_por", "usuarios"),
        ("objetivo_uuid", "objetivo_id", "objetivos"),
    ],
    "objetivos": [
        ("responsable_uuid", "responsable_id", "usuarios"),
    ],
    "tarea_sesiones": [
        ("tarea_uuid", "tarea_id", "tareas"),
        ("usuario_uuid", "usuario_id", "usuarios"),
    ],
    "tarea_rechazos": [
        ("tarea_uuid", "tarea_id", "tareas"),
        ("admin_uuid", "admin_id", "usuarios"),
    ],
    "tarea_observaciones": [
        ("tarea_uuid", "tarea_id", "tareas"),
        ("usuario_uuid", "usuario_id", "usuarios"),
    ],
    "tarea_adjuntos": [
        ("tarea_uuid", "tarea_id", "tareas"),
        ("usuario_uuid", "usuario_id", "usuarios"),
    ],
    "tarea_asignaciones": [
        ("tarea_uuid", "tarea_id", "tareas"),
        ("usuario_uuid", "usuario_id", "usuarios"),
    ],
    "tarea_participantes": [
        ("tarea_uuid", "tarea_id", "tareas"),
        ("usuario_uuid", "usuario_id", "usuarios"),
    ],
}

# Tablas de relación sin uuid propio: upsert por la UNIQUE(tarea_id, usuario_id).
# Se tratan aparte en `_pull_tabla()` porque el flujo genérico exige `uuid`.
_TABLAS_RELACION = {"tarea_asignaciones", "tarea_participantes"}

# Para reset manual: si el usuario pone "last_sync": "" en cloud_config.json,
# el próximo pull trae TODO (updated_at > '' siempre es verdadero).


class CloudSync:
    """Motor de sync. Seguro para instanciación única a nivel de app."""

    def __init__(self, on_ui_update=None):
        self.config = cargar_config()
        self.online = False
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self.on_ui_update = on_ui_update

    # ---------- util ----------
    @property
    def habilitado(self) -> bool:
        return bool(self.config.get("habilitado")) and bool(self.config.get("api_url"))

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "X-API-Key": self.config.get("api_key", ""),
            "X-Dispositivo-ID": self.config.get("dispositivo_id", "unknown"),
            "User-Agent": "GestorSUI/1.0",
        }

    def _api_call(self, method: str, endpoint: str, data=None, timeout=10):
        url = f"{self.config['api_url'].rstrip('/')}{endpoint}"
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers=self._headers(),
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # OJO: usar "->" ASCII, NO la flecha Unicode "→". El handler de
            # consola (sys.stderr) en builds Flet Windows queda en cp1252 y
            # crashea al imprimir Unicode arriba de U+007F. El stacktrace
            # resultante envenenaba el logger entero y NINGÚN push posterior
            # podía registrar fallos — daba la sensación de que la cola
            # estaba estancada sin razón visible.
            try:
                cuerpo = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                cuerpo = ""
            log.warning("API %s %s -> HTTP %s %s",
                        method, endpoint, e.code, cuerpo)
            return None
        except Exception as e:
            log.debug("API %s %s -> %s", method, endpoint, e)
            return None

    # ---------- conexión ----------
    def check_connection(self) -> bool:
        if not self.habilitado:
            self.online = False
            return False
        r = self._api_call("GET", "/health", timeout=5)
        was = self.online
        self.online = bool(r and r.get("ok"))
        if self.online and not was:
            log.info("Conexión cloud restablecida")
        elif not self.online and was:
            log.info("Conexión cloud perdida")
        return self.online

    # ---------- cola ----------
    def queue(self, tabla: str, endpoint: str, metodo: str = "POST",
              datos: dict | None = None) -> None:
        """Encola una operación para push. Llamar desde los repos tras escribir.

        Si ya hay un pendiente para el mismo (endpoint, metodo), actualizamos
        el payload y reiniciamos `intentos`. Esto:
          - Colapsa múltiples ediciones del mismo recurso en una sola pendiente
            (antes: cada edit sumaba; si se editaba 10 veces había 10 filas
            idénticas en la cola).
          - Evita que `bootstrap_push_local()` duplique filas si se llama
            varias veces (usuario clicando "Resincronizar todo" repetidamente).
          - El payload remoto siempre es `INSERT … ON CONFLICT UPDATE` en el
            Worker, así que enviar el estado más reciente es equivalente a
            enviar toda la secuencia.
        """
        if not self.habilitado:
            return
        datos_dict = datos or {}
        datos_json = json.dumps(datos_dict, ensure_ascii=False, default=str)
        # CLAVE de dedup: el `uuid` del recurso identifica la fila únicamente.
        # Sin él, dos POST a /usuarios con uuids distintos colapsarían a uno
        # solo (bug que destrozaba el bootstrap_push_local: 32 filas → 1
        # quedaba en cola). Para DELETEs, el uuid va en el endpoint mismo y
        # `datos` está vacío, así que el dedup por (endpoint, metodo) sí
        # identifica la fila correctamente.
        uuid_recurso = (datos_dict.get("uuid")
                        if isinstance(datos_dict, dict) else None)
        try:
            with get_db() as conn:
                if uuid_recurso:
                    existente = conn.execute(
                        "SELECT id FROM sync_queue WHERE endpoint=? AND metodo=? "
                        "AND json_extract(datos, '$.uuid')=? "
                        "ORDER BY id DESC LIMIT 1",
                        (endpoint, metodo, uuid_recurso),
                    ).fetchone()
                else:
                    existente = conn.execute(
                        "SELECT id FROM sync_queue WHERE endpoint=? AND metodo=? "
                        "ORDER BY id DESC LIMIT 1",
                        (endpoint, metodo),
                    ).fetchone()
                if existente:
                    conn.execute(
                        "UPDATE sync_queue SET datos=?, timestamp=?, intentos=0 "
                        "WHERE id=?",
                        (datos_json, _ahora_iso(), existente["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO sync_queue
                           (tabla, operacion, endpoint, metodo, datos, timestamp)
                           VALUES (?,?,?,?,?,?)""",
                        (tabla, metodo, endpoint, metodo, datos_json, _ahora_iso()),
                    )
        except Exception as e:
            log.error("Error encolando sync: %s", e)

    def pending_count(self) -> int:
        try:
            with get_db() as conn:
                return conn.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0]
        except Exception:
            return 0

    # ---------- push ----------
    def push_queued(self) -> tuple[int, int]:
        if not self.habilitado or not self.online:
            return 0, 0
        with self._lock:
            with get_db() as conn:
                pendientes = conn.execute(
                    "SELECT id, endpoint, metodo, datos, intentos FROM sync_queue "
                    "ORDER BY id ASC LIMIT 100"
                ).fetchall()
            if not pendientes:
                return 0, 0
            exitos = fallos = 0
            for row in pendientes:
                try:
                    datos = json.loads(row["datos"])
                except Exception:
                    datos = {}
                result = self._api_call(
                    row["metodo"], row["endpoint"],
                    datos if row["metodo"] in ("POST", "PUT") else None,
                )
                with get_db() as conn:
                    if result is not None and result.get("ok"):
                        conn.execute("DELETE FROM sync_queue WHERE id=?", (row["id"],))
                        exitos += 1
                    elif (row["intentos"] or 0) >= 10:
                        log.warning("Descartando sync tras 10 intentos id=%s", row["id"])
                        conn.execute("DELETE FROM sync_queue WHERE id=?", (row["id"],))
                        fallos += 1
                    else:
                        conn.execute(
                            "UPDATE sync_queue SET intentos = intentos + 1 WHERE id=?",
                            (row["id"],),
                        )
                        fallos += 1
            if exitos or fallos:
                # Sin este log, push era una caja negra: no había forma de
                # saber si la cola drenaba o se estaba llenando sin parar.
                try:
                    pendientes_restantes = self.pending_count()
                except Exception:
                    pendientes_restantes = -1
                log.info("Push: %d ok, %d fail (pendientes: %d)",
                         exitos, fallos, pendientes_restantes)
            return exitos, fallos

    # ---------- pull ----------
    def _pull_tabla(self, tabla: str, since: str) -> int:
        """Trae filas con updated_at > since y las upserta localmente.

        Maneja dos casos:
          (a) Tablas con uuid propio (usuarios, objetivos, tareas, etc.):
              upsert por uuid. Antes se traducen los *_uuid entrantes a los
              *_id locales correspondientes (sync_helpers lo hace al revés en
              push).
          (b) Tablas de relación sin uuid (tarea_asignaciones,
              tarea_participantes): upsert por la UNIQUE(tarea_id, usuario_id).
              Si alguna de las dos entidades padre aún no existe localmente
              (uuid sin pareja), la fila se salta y queda pendiente del
              siguiente ciclo — por eso pull_all() procesa primero padres.
        """
        r = self._api_call("GET", f"/{tabla}?since={urllib.parse.quote(since)}")
        if not r or not r.get("ok"):
            return 0
        rows = r.get("data") or []
        if not rows:
            log.info("Pull %s: 0 filas (nada nuevo desde %s)", tabla, since or "∅")
            return 0

        cambios = 0
        saltadas = 0
        with get_db() as conn:
            info_cols = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
            local_cols = {c["name"] for c in info_cols}
            # Columnas NOT NULL sin default — si una FK traducida queda None y
            # pertenece aquí, no podemos insertar (revienta IntegrityError y
            # aborta el pull de la tabla entera). Se usa abajo para saltar
            # defensivamente filas con padre ausente.
            cols_requeridas = {c["name"] for c in info_cols
                               if c["notnull"] and c["dflt_value"] is None
                               and c["name"] != "id"}
            mapa_uuids = _MAP_UUIDS_INVERSO.get(tabla, [])

            for row in rows:
                # ----- Paso 1: traducir *_uuid → *_id resolviendo contra locales
                row_traducida = dict(row)
                for col_uuid, col_id, tabla_rel in mapa_uuids:
                    uuid_val = row_traducida.pop(col_uuid, None)
                    if not uuid_val:
                        row_traducida[col_id] = None
                        continue
                    ref = conn.execute(
                        f"SELECT id FROM {tabla_rel} WHERE uuid=?", (uuid_val,),
                    ).fetchone()
                    row_traducida[col_id] = ref["id"] if ref else None

                # ----- Paso 2a: tablas de relación sin uuid
                if tabla in _TABLAS_RELACION:
                    tarea_id = row_traducida.get("tarea_id")
                    usuario_id = row_traducida.get("usuario_id")
                    if not tarea_id or not usuario_id:
                        # El padre aún no está localmente (se resolverá en el
                        # siguiente ciclo). No contamos como cambio.
                        saltadas += 1
                        continue
                    if tabla == "tarea_participantes":
                        estado = row_traducida.get("estado") or "pendiente"
                        fecha_comp = row_traducida.get("fecha_completado")
                        try:
                            conn.execute(
                                "INSERT INTO tarea_participantes "
                                "(tarea_id, usuario_id, estado, fecha_completado) "
                                "VALUES (?,?,?,?) "
                                "ON CONFLICT(tarea_id, usuario_id) DO UPDATE SET "
                                "estado=excluded.estado, "
                                "fecha_completado=excluded.fecha_completado",
                                (tarea_id, usuario_id, estado, fecha_comp),
                            )
                            cambios += 1
                        except Exception:
                            log.exception("Error upsert tarea_participantes "
                                          "(tarea_id=%s usuario_id=%s)",
                                          tarea_id, usuario_id)
                    else:  # tarea_asignaciones (sólo la tupla)
                        try:
                            conn.execute(
                                "INSERT INTO tarea_asignaciones "
                                "(tarea_id, usuario_id) VALUES (?,?) "
                                "ON CONFLICT(tarea_id, usuario_id) DO NOTHING",
                                (tarea_id, usuario_id),
                            )
                            cambios += 1
                        except Exception:
                            log.exception("Error upsert tarea_asignaciones "
                                          "(tarea_id=%s usuario_id=%s)",
                                          tarea_id, usuario_id)
                    continue

                # ----- Paso 2b: tablas con uuid propio
                filtrado = {k: v for k, v in row_traducida.items() if k in local_cols}
                if not filtrado.get("uuid"):
                    saltadas += 1
                    continue
                # Si algún FK traducido desde *_uuid corresponde a una columna
                # NOT NULL y quedó None (padre ausente local), saltamos. El
                # próximo ciclo de pull puede traer el padre y esta fila se
                # aplicará entonces. Antes esto reventaba con IntegrityError
                # y abortaba el pull entero (tarea_sesiones nunca bajaban).
                #
                # Ojo: NO saltamos si la FK es nullable legítimamente
                # (p. ej. tareas.validado_por, tareas.objetivo_id).
                fk_requeridos = [col_id for _, col_id, _ in mapa_uuids
                                 if col_id in cols_requeridas]
                if any(filtrado.get(c) is None for c in fk_requeridos):
                    saltadas += 1
                    continue
                existente = conn.execute(
                    f"SELECT updated_at FROM {tabla} WHERE uuid=?",
                    (filtrado["uuid"],),
                ).fetchone()
                remoto_upd = filtrado.get("updated_at") or ""
                if existente and (existente["updated_at"] or "") >= remoto_upd:
                    continue  # local es igual o más nuevo
                cols = list(filtrado.keys())
                placeholders = ",".join("?" for _ in cols)
                updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "uuid")
                conn.execute(
                    f"INSERT INTO {tabla} ({','.join(cols)}) VALUES ({placeholders}) "
                    f"ON CONFLICT(uuid) DO UPDATE SET {updates}",
                    [filtrado[c] for c in cols],
                )
                cambios += 1

        if saltadas:
            log.info("Pull %s: %d filas aplicadas, %d saltadas (padre ausente/"
                     "sin uuid)", tabla, cambios, saltadas)
        else:
            log.info("Pull %s: %d filas aplicadas", tabla, cambios)
        return cambios

    def pull_all(self) -> int:
        if not self.habilitado or not self.online:
            return 0
        since = self.config.get("last_sync") or ""
        # Tip: si el usuario pone "last_sync": "" en cloud_config.json, la API
        # devuelve TODO (updated_at > '' es siempre verdadero). Útil tras un
        # primer arranque o si los datos locales se desincronizaron.
        log.info("Pull inicio — since=%s", since or "∅ (trae todo)")
        total = 0
        # Orden: primero padres (usuarios, objetivos, tareas) luego hijos
        # relacionales — las tablas de relación dependen de resolver primero
        # los uuid de tareas y usuarios a sus id locales.
        orden = ["usuarios", "objetivos", "tareas",
                 "tarea_asignaciones", "tarea_participantes",
                 "tarea_sesiones", "tarea_rechazos",
                 "tarea_observaciones", "tarea_adjuntos"]
        for tabla in orden:
            try:
                total += self._pull_tabla(tabla, since)
            except Exception as e:
                log.exception("Error pull tabla %s: %s", tabla, e)
        self.config["last_sync"] = _ahora_iso()
        guardar_config(self.config)
        log.info("Pull total: %d filas aplicadas en este ciclo", total)
        return total

    # ---------- bootstrap ----------
    def bootstrap_push_local(self) -> int:
        """Encola todas las filas locales de tablas sincronizables que aún no
        están en la cola. Se usa cuando el usuario activa sync por primera vez
        para que sus datos históricos suban a la nube.
        """
        if not self.habilitado:
            return 0
        # Importamos aquí para evitar ciclo en el arranque de los repos
        from sync_helpers import _construir_payload, encolar_relacion
        total = 0
        with get_db() as conn:
            # Filas con uuid propio (patrón upsert por /tabla)
            for tabla in ("usuarios", "objetivos", "tareas",
                          "tarea_sesiones", "tarea_rechazos",
                          "tarea_observaciones", "tarea_adjuntos"):
                try:
                    filas = conn.execute(
                        f"SELECT * FROM {tabla} WHERE uuid IS NOT NULL AND uuid != ''"
                    ).fetchall()
                except Exception:
                    continue
                for fila in filas:
                    payload = _construir_payload(conn, tabla, fila)
                    if not payload:
                        continue
                    self.queue(tabla, f"/{tabla}", "POST", payload)
                    total += 1
            # Relaciones sin uuid (participantes, asignaciones)
            for tabla in ("tarea_participantes", "tarea_asignaciones"):
                try:
                    filas = conn.execute(
                        f"SELECT tarea_id, usuario_id, "
                        f"{'estado, fecha_completado' if tabla == 'tarea_participantes' else 'NULL AS estado, NULL AS fecha_completado'} "
                        f"FROM {tabla}"
                    ).fetchall()
                except Exception:
                    continue
                for fila in filas:
                    try:
                        encolar_relacion(
                            tabla, fila["tarea_id"], fila["usuario_id"],
                            estado=fila["estado"] if tabla == "tarea_participantes" else None,
                            fecha_completado=(fila["fecha_completado"]
                                              if tabla == "tarea_participantes" else None),
                        )
                        total += 1
                    except Exception:
                        log.exception("Error bootstrap relación %s", tabla)
        log.info("Bootstrap push: %s filas encoladas", total)
        return total

    def _es_primer_sync(self) -> bool:
        return not (self.config.get("last_sync") or "").strip()

    # ---------- full sync ----------
    def full_sync(self) -> dict:
        if not self.habilitado:
            return {"ok": False, "msg": "Sync deshabilitado"}
        self.check_connection()
        if not self.online:
            return {"ok": False, "msg": "Sin conexión"}
        # En el PRIMER sync de un dispositivo: encolamos todas las filas locales
        # para subir datos históricos creados antes de activar sync.
        if self._es_primer_sync():
            try:
                self.bootstrap_push_local()
            except Exception:
                log.exception("Error bootstrap push")
        push_ok, push_fail = self.push_queued()
        pulled = self.pull_all()
        return {
            "ok": True, "pushed": push_ok, "push_errors": push_fail,
            "pulled": pulled, "msg": f"Push {push_ok} | Pull {pulled}",
        }

    # ---------- hilo de fondo ----------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                if self.habilitado:
                    self.full_sync()
                    if self.on_ui_update:
                        try:
                            self.on_ui_update()
                        except Exception:
                            log.exception("on_ui_update falló")
            except Exception:
                log.exception("Error en ciclo de sync")
            time.sleep(max(5, int(self.config.get("sync_interval", 30))))


# ----------------------------------------------------------------------
# Singleton accesible desde los repos (para encolar escrituras sin pasar
# explícitamente la instancia).
# ----------------------------------------------------------------------
_INSTANCIA: CloudSync | None = None


def init_sync(on_ui_update=None) -> CloudSync:
    """Inicializa el singleton global. Llamar una sola vez al arrancar la app."""
    global _INSTANCIA
    if _INSTANCIA is None:
        _INSTANCIA = CloudSync(on_ui_update=on_ui_update)
    return _INSTANCIA


def get_sync() -> CloudSync | None:
    """Devuelve la instancia (None si aún no se inicializó)."""
    return _INSTANCIA
