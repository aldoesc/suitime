# SUItime — Gestión Operacional

App multiplataforma para gestión de tareas, tiempos y operatividad de equipos técnicos. Un solo código Python compila a **Windows** y **Android APK**.

## Funcionalidades

- **Tareas** con ciclo de vida completo: pendiente → en progreso → validación → cierre
- **Registro de tiempo** de trabajo por operador y sesión
- **Fallas y reparaciones** con historial trazable
- **Adjuntos fotográficos** desde cámara o galería
- **Sincronización cloud** offline-first con Cloudflare Workers + D1
- **Servidor MCP** para consultas operacionales en lenguaje natural via IA

## Stack

| Capa | Tecnología |
|------|-----------|
| UI / Desktop | Flet 0.21+ |
| UI / Android | Flet build → Flutter → APK |
| Base de datos local | SQLite + bcrypt |
| Sync cloud | Cloudflare Workers + D1 (offline-first) |
| IA / MCP | FastMCP (servidor de consultas) |
| CI/CD | GitHub Actions |
| Empaquetado | PyInstaller (Windows), Flet build (Android) |

## Arquitectura

```
main.py            # Entrada (Flet)
database.py        # SQLite local + esquema
cloud_sync.py      # Motor offline-first (queue + backoff + pull/push)
tiempo.py          # Lógica de sesiones de trabajo
validators.py      # Validación de datos
security.py        # Autenticación bcrypt
mcp_suitime/
└── server.py      # Servidor MCP (consultas IA sobre la API cloud)
```

## Instalación

```bash
pip install -r requirements.txt
python main.py
```

## Build multiplataforma

```bash
# Windows
flet build windows

# Android APK
flet build apk

# Android APK (release, requiere keystore)
flet build apk --release
```

## Servidor MCP (consultas IA)

```bash
pip install mcp
# Configurar en .mcp.json o Claude Desktop
# Herramientas: resumen_operacional, tareas, fallas_activas, notas, historial_tarea, carga_operadores, buscar
```

## Variables de entorno

Configurar en `cloud_config.json` (no versionado):
```json
{
  "api_url": "https://tu-worker.workers.dev",
  "api_key": "tu-api-key",
  "dispositivo_id": "nombre-del-equipo"
}
```

---

Desarrollado por [Aldo Escobar](https://hexa38.com) · Hexa38
