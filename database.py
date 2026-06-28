import logging
import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager

from security import hash_password

# Directorio del proyecto (útil para migración de datos viejos y modo dev).
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def _user_data_dir() -> str:
    """Directorio persistente del usuario. Sobrevive a actualizaciones del app.

    Orden de preferencia:
      1. GESTOR_DATA_DIR (override explícito, útil en tests).
      2. FLET_APP_STORAGE_DATA — lo setea Flet en builds nativos
         (desktop/Android/iOS) y apunta a una ruta segura por plataforma.
      3. %APPDATA%\\GestorSUI\\ en Windows,
         ~/Library/Application Support/GestorSUI/ en macOS,
         $XDG_DATA_HOME/GestorSUI/ o ~/.local/share/GestorSUI/ en Linux.
      4. Fallback: _PROJECT_DIR (modo dev corriendo `python main.py`).
    """
    candidato = os.environ.get("GESTOR_DATA_DIR")
    if not candidato:
        candidato = os.environ.get("FLET_APP_STORAGE_DATA")
    if not candidato:
        if sys.platform == "win32":
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
        elif sys.platform == "darwin":
            base = os.path.join(os.path.expanduser("~"),
                                "Library", "Application Support")
        elif sys.platform.startswith("linux"):
            base = (os.environ.get("XDG_DATA_HOME")
                    or os.path.join(os.path.expanduser("~"), ".local", "share"))
        else:
            base = ""
        if base and os.path.isdir(base):
            candidato = os.path.join(base, "GestorSUI")
    if not candidato:
        # Último recurso — modo desarrollador: al lado de los .py
        candidato = _PROJECT_DIR
    try:
        os.makedirs(candidato, exist_ok=True)
    except OSError:
        candidato = _PROJECT_DIR
    return candidato


_DATA_DIR = _user_data_dir()


def data_dir() -> str:
    """Devuelve el directorio persistente del usuario (misma raíz que la BD)."""
    return _DATA_DIR


def _resolver_ruta(valor: str, nombre_default: str) -> str:
    """Si `valor` es absoluto, lo usa; si es relativo, lo ancla en `_DATA_DIR`.

    Si el valor parece una URL o está vacío, cae al default. Esto evita bugs
    del tipo GESTOR_UPLOADS='https://…' → ruta rota.
    """
    candidato = (valor or "").strip()
    if candidato.lower().startswith(("http://", "https://", "webdav://", "dav://")):
        candidato = ""  # una URL no es una ruta local válida
    ruta = candidato or nombre_default
    if not os.path.isabs(ruta):
        ruta = os.path.join(_DATA_DIR, ruta)
    return ruta


def _migrar_ficheros_legacy(nombres: list[str]) -> None:
    """Si el usuario tenía la BD/log/config junto a los .py (v1.0.0), los mueve
    a _DATA_DIR. Así el upgrade a v1.0.1+ no pierde datos."""
    if _DATA_DIR == _PROJECT_DIR:
        return  # modo dev, no migramos
    for nombre in nombres:
        viejo = os.path.join(_PROJECT_DIR, nombre)
        nuevo = os.path.join(_DATA_DIR, nombre)
        try:
            if os.path.isfile(viejo) and not os.path.exists(nuevo):
                shutil.copy2(viejo, nuevo)
        except OSError:
            pass


_migrar_ficheros_legacy(["gestor_operacional.db", "gestor.log", "cloud_config.json"])


DB_NAME = _resolver_ruta(os.environ.get("GESTOR_DB", ""), "gestor_operacional.db")
UPLOAD_DIR = _resolver_ruta(os.environ.get("GESTOR_UPLOADS", ""), "uploads")
LOG_FILE = _resolver_ruta(os.environ.get("GESTOR_LOG", ""), "gestor.log")

# Migrar carpeta uploads/ completa si existía en el proyecto
_uploads_legacy = os.path.join(_PROJECT_DIR, "uploads")
if (_DATA_DIR != _PROJECT_DIR and os.path.isdir(_uploads_legacy)
        and not os.path.isdir(UPLOAD_DIR)):
    try:
        shutil.copytree(_uploads_legacy, UPLOAD_DIR)
    except OSError:
        pass

os.makedirs(UPLOAD_DIR, exist_ok=True)

# Forzar UTF-8 en stderr/stdout antes de configurar logging. En Flet builds
# Windows el runtime arranca con cp1252 y log.warning con caracteres "→",
# tildes, etc. crashea al StreamHandler — el stacktrace resultante envenena
# el módulo logging y silencia TODOS los logs posteriores.
import sys as _sys  # noqa: E402
for _stream in (_sys.stderr, _sys.stdout):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    rol TEXT NOT NULL,
    nombre TEXT NOT NULL,
    email TEXT,
    telefono TEXT,
    tipo_operador TEXT,
    cargo TEXT,
    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS tareas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    descripcion TEXT,
    fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fecha_inicio DATE,
    fecha_vencimiento DATE,
    recurrencia TEXT,
    prioridad TEXT DEFAULT 'media',
    estado TEXT DEFAULT 'pendiente',
    es_grupal INTEGER DEFAULT 0,
    creador_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    validado_por INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    fecha_validacion TIMESTAMP,
    notas_operador TEXT,
    observaciones_admin TEXT,
    objetivo_id INTEGER REFERENCES objetivos(id) ON DELETE SET NULL,
    porcentaje_objetivo REAL DEFAULT 0,
    rechazos_count INTEGER DEFAULT 0,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS tarea_participantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarea_id INTEGER REFERENCES tareas(id) ON DELETE CASCADE,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    estado TEXT DEFAULT 'pendiente',
    fecha_completado TIMESTAMP,
    UNIQUE(tarea_id, usuario_id)
);

CREATE TABLE IF NOT EXISTS tarea_asignaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarea_id INTEGER REFERENCES tareas(id) ON DELETE CASCADE,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    UNIQUE(tarea_id, usuario_id)
);

CREATE TABLE IF NOT EXISTS tarea_adjuntos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarea_id INTEGER REFERENCES tareas(id) ON DELETE CASCADE,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    nombre_archivo TEXT,
    ruta_archivo TEXT,
    tipo TEXT,
    monto REAL,
    fecha_subida TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS objetivos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    descripcion TEXT,
    fecha_inicio DATE,
    fecha_fin DATE,
    progreso REAL DEFAULT 0,
    responsable_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    tipo TEXT DEFAULT 'manual',
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS tarea_sesiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarea_id INTEGER NOT NULL REFERENCES tareas(id) ON DELETE CASCADE,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    inicio_utc TIMESTAMP NOT NULL,
    fin_utc TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tarea_rechazos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarea_id INTEGER NOT NULL REFERENCES tareas(id) ON DELETE CASCADE,
    admin_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    observacion TEXT NOT NULL,
    fecha_utc TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS tarea_observaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarea_id INTEGER NOT NULL REFERENCES tareas(id) ON DELETE CASCADE,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    tipo TEXT NOT NULL DEFAULT 'nota',
    texto TEXT NOT NULL,
    fecha_utc TIMESTAMP NOT NULL,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_rechazos_tarea ON tarea_rechazos(tarea_id);
CREATE INDEX IF NOT EXISTS idx_observaciones_tarea ON tarea_observaciones(tarea_id);

CREATE INDEX IF NOT EXISTS idx_sesiones_tarea ON tarea_sesiones(tarea_id);
CREATE INDEX IF NOT EXISTS idx_sesiones_abiertas ON tarea_sesiones(usuario_id, fin_utc);

-- Tareas ocultas POR USUARIO (preferencia de UI, NO se sincroniza al cloud).
-- "Ocultar" no es lo mismo que "borrar": la tarea sigue existiendo y los demás
-- usuarios la ven; sólo desaparece del lobby de quien la ocultó. Se puede
-- mostrar de nuevo desde la sección "Tareas ocultas" en su papelera.
CREATE TABLE IF NOT EXISTS tarea_oculta_para (
    tarea_id INTEGER NOT NULL REFERENCES tareas(id) ON DELETE CASCADE,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    hidden_at TEXT NOT NULL,
    PRIMARY KEY (tarea_id, usuario_id)
);
CREATE INDEX IF NOT EXISTS idx_tarea_oculta_usuario
    ON tarea_oculta_para(usuario_id);

-- Cola de sincronización: cada escritura local se encola para push al cloud.
CREATE TABLE IF NOT EXISTS sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tabla TEXT NOT NULL,
    operacion TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    metodo TEXT NOT NULL DEFAULT 'POST',
    datos TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    intentos INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sync_queue_id ON sync_queue(id);
"""


# Tablas con uuid + updated_at para sync. Lista usada por la migración.
TABLAS_SYNC = [
    "usuarios", "objetivos", "tareas",
    "tarea_participantes", "tarea_asignaciones",
    "tarea_sesiones", "tarea_rechazos", "tarea_observaciones", "tarea_adjuntos",
]


def _migrar_password_a_hash(conn: sqlite3.Connection) -> None:
    """Si la tabla antigua tenía columna 'password' en texto plano, la migra a password_hash."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()]
    if "password_hash" in cols:
        return
    if "password" not in cols:
        # Tabla nueva con otro esquema — nada que migrar
        conn.execute("ALTER TABLE usuarios ADD COLUMN password_hash TEXT")
        return
    log.warning("Migrando contraseñas en texto plano a bcrypt hash...")
    conn.execute("ALTER TABLE usuarios ADD COLUMN password_hash TEXT")
    usuarios = conn.execute("SELECT id, password FROM usuarios").fetchall()
    for u in usuarios:
        conn.execute(
            "UPDATE usuarios SET password_hash=? WHERE id=?",
            (hash_password(u["password"] or "changeme"), u["id"]),
        )
    # No podemos DROP COLUMN en SQLite antiguo; dejamos la columna obsoleta pero vacía.
    try:
        conn.execute("UPDATE usuarios SET password = NULL")
    except sqlite3.Error:
        pass
    log.warning("Migración completada. Revisa/rota credenciales por defecto.")


def _pull_usuarios_inicial(conn: sqlite3.Connection) -> int:
    """Si el cloud sync está habilitado y el cloud tiene usuarios, los upserta
    SINCRÓNICAMENTE al device local antes de continuar. Devuelve la cantidad
    de usuarios bajados (0 si cloud vacío o inaccesible).

    Por qué síncrono: si el seed se salta porque el cloud tiene usuarios,
    pero el pull todavía no corrió, el usuario se encuentra una pantalla de
    login con 0 usuarios locales y "Credenciales incorrectas" cuando intenta
    entrar. Hacer el pull en línea evita esa ventana de tiempo.

    Si el cloud está inaccesible (offline, error HTTP, sin config), devuelve
    0 sin levantar excepción — el seed normal corre como fallback.
    """
    try:
        from cloud_sync import init_sync
        sync = init_sync()
    except Exception:
        return 0
    if not sync or not sync.habilitado:
        return 0
    try:
        r = sync._api_call("GET", "/usuarios?since=", timeout=5)
    except Exception:
        return 0
    if not r or not r.get("ok"):
        return 0
    data = r.get("data") or []
    if not data:
        return 0
    # Upsert por uuid en la tabla local. Sólo columnas que existen aquí.
    info_cols = conn.execute("PRAGMA table_info(usuarios)").fetchall()
    cols_locales = {c["name"] for c in info_cols}
    aplicados = 0
    for u in data:
        cols = [k for k in u.keys() if k in cols_locales and k != "id"]
        if not cols:
            continue
        placeholders = ",".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "uuid")
        try:
            conn.execute(
                f"INSERT INTO usuarios ({','.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT(uuid) DO UPDATE SET {updates}",
                [u[c] for c in cols],
            )
            aplicados += 1
        except sqlite3.Error:
            log.exception("No se pudo upsertar usuario %s desde cloud",
                          u.get("username"))
    if aplicados:
        log.info("Pull inicial síncrono: %d usuarios bajados al device.",
                 aplicados)
    return aplicados


def _seed_usuarios(conn: sqlite3.Connection) -> None:
    """Crea el usuario admin inicial (Christian Balbin) si aún no existe ninguno.
    Credenciales por defecto se pueden sobrescribir con variables de entorno:
      GESTOR_ADMIN_USER, GESTOR_ADMIN_PASS, GESTOR_ADMIN_NOMBRE,
      GESTOR_ADMIN_EMAIL, GESTOR_ADMIN_TELEFONO.

    IMPORTANTE: si el cloud sync está habilitado y el cloud ya tiene usuarios,
    saltamos el seed. Esto evita que cada install nuevo cree un "admin local"
    con uuid distinto al del cloud, terminando con DOS admins por device tras
    el primer pull. El primer ciclo de sync va a traer el admin del cloud.
    """
    count = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
    if count > 0:
        return
    # Antes de seedear local, intentar bajar usuarios del cloud SÍNCRONAMENTE.
    # Si el cloud tiene admin/operadores, los usamos en lugar de crear un
    # admin local con uuid distinto (que terminaría duplicando al hacer pull).
    bajados = _pull_usuarios_inicial(conn)
    if bajados > 0:
        log.info("Seed admin saltado: %d usuarios bajados desde cloud.", bajados)
        return
    admin_user = os.environ.get("GESTOR_ADMIN_USER", "admin")
    admin_pass = os.environ.get("GESTOR_ADMIN_PASS")
    admin_nombre = os.environ.get("GESTOR_ADMIN_NOMBRE", "Christian Balbin")
    admin_email = os.environ.get("GESTOR_ADMIN_EMAIL", "cmbalbin@hotmail.com")
    admin_tel = os.environ.get("GESTOR_ADMIN_TELEFONO", "+51 993 457 560")
    if not admin_pass:
        admin_pass = "admin123"
        log.warning(
            "Admin creado con contrasena por defecto '%s'. Cambiala tras el primer login "
            "o define GESTOR_ADMIN_PASS antes de iniciar.",
            admin_pass,
        )
    import uuid as _uuid
    from datetime import datetime, timezone
    ahora_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO usuarios (username, password_hash, rol, nombre, email, telefono,
                                 cargo, uuid, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
        (admin_user, hash_password(admin_pass), "admin", admin_nombre,
         admin_email, admin_tel, "Gerente",
         str(_uuid.uuid4()), ahora_iso),
    )
    new_id = cur.fetchone()[0]
    log.info("Usuario admin inicial creado: %s (%s) id=%s",
             admin_user, admin_nombre, new_id)
    # Push al cloud: si la sync está habilitada, encolamos este admin para
    # que llegue al worker. Así, cuando un segundo dispositivo arranque
    # contra el mismo cloud, va a ver al admin existente y NO va a crear
    # uno duplicado (gracias al `_cloud_tiene_usuarios()` de arriba).
    try:
        from sync_helpers import encolar_fila
        encolar_fila("usuarios", new_id)
    except Exception:
        log.exception("No se pudo encolar el admin seed para push (no crítico)")


def _migrar_columnas_extra(conn: sqlite3.Connection) -> None:
    cols_usu = [r["name"] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()]
    if "cargo" not in cols_usu:
        conn.execute("ALTER TABLE usuarios ADD COLUMN cargo TEXT")
    if "telefono" not in cols_usu:
        conn.execute("ALTER TABLE usuarios ADD COLUMN telefono TEXT")

    cols_obj = [r["name"] for r in conn.execute("PRAGMA table_info(objetivos)").fetchall()]
    if "tipo" not in cols_obj:
        conn.execute("ALTER TABLE objetivos ADD COLUMN tipo TEXT DEFAULT 'manual'")

    cols_tar = [r["name"] for r in conn.execute("PRAGMA table_info(tareas)").fetchall()]
    if "objetivo_id" not in cols_tar:
        conn.execute(
            "ALTER TABLE tareas ADD COLUMN objetivo_id INTEGER REFERENCES objetivos(id) ON DELETE SET NULL"
        )
    if "porcentaje_objetivo" not in cols_tar:
        conn.execute("ALTER TABLE tareas ADD COLUMN porcentaje_objetivo REAL DEFAULT 0")
    if "rechazos_count" not in cols_tar:
        conn.execute("ALTER TABLE tareas ADD COLUMN rechazos_count INTEGER DEFAULT 0")


def _migrar_sync_columns(conn: sqlite3.Connection) -> None:
    """Agrega columnas `uuid` y `updated_at` a todas las tablas sincronizables.
    Para las filas existentes sin uuid, genera uno y marca updated_at al momento
    actual para que suban al primer push.
    """
    import uuid as _uuid
    from datetime import datetime, timezone
    ahora_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for tabla in TABLAS_SYNC:
        cols = [r["name"] for r in conn.execute(
            f"PRAGMA table_info({tabla})").fetchall()]
        if "uuid" not in cols:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN uuid TEXT")
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{tabla}_uuid ON {tabla}(uuid)"
            )
        if "updated_at" not in cols:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN updated_at TEXT DEFAULT ''")

        # Backfill uuid en filas existentes. Usamos `rowid AS rid` porque, con
        # row_factory=sqlite3.Row, "rowid" sin alias no siempre queda indexable.
        filas_sin_uuid = conn.execute(
            f"SELECT rowid AS rid FROM {tabla} WHERE uuid IS NULL OR uuid=''"
        ).fetchall()
        for r in filas_sin_uuid:
            conn.execute(
                f"UPDATE {tabla} SET uuid=?, updated_at=? WHERE rowid=?",
                (str(_uuid.uuid4()), ahora_iso, r["rid"]),
            )


def _migrar_soft_delete(conn: sqlite3.Connection) -> None:
    """Agrega columna `deleted_at TEXT` a tareas, objetivos, usuarios y
    tarea_observaciones para soporte de papelera y sync de eliminaciones sin
    que vuelvan al pull. La papelera de notas (tarea_observaciones) hoy es
    device-local: el push omite `deleted_at` para esa tabla porque el worker
    Cloudflare aún no tiene la columna (ver `_OMIT_EN_CLOUD_POR_TABLA` en
    sync_helpers.py)."""
    for tabla in ("tareas", "objetivos", "usuarios", "tarea_observaciones"):
        cols = [r["name"] for r in conn.execute(
            f"PRAGMA table_info({tabla})").fetchall()]
        if "deleted_at" not in cols:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN deleted_at TEXT")
            log.info("Migración soft-delete: columna deleted_at añadida a %s", tabla)


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(SCHEMA)
        _migrar_password_a_hash(conn)
        _migrar_columnas_extra(conn)
        _migrar_sync_columns(conn)
        _migrar_soft_delete(conn)
        _seed_usuarios(conn)
