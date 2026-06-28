# MCP SUItime — consulta operacional

Servidor MCP de **solo lectura** que lee la base de datos compartida de SUItime
a través de su **API cloud** (`https://api-suitime.hexa38.com`). Ve los datos de
**todos los usuarios y dispositivos**, no sólo los de este equipo.

Sirve para preguntarle a Claude (en lenguaje natural) cosas como:

- ¿Cuál es el estado general de operatividad hoy?
- ¿Qué equipos tienen fallas abiertas sin reparar?
- ¿Qué tareas están en curso y quién las tiene?
- ¿Cuánto tiempo lleva trabajado cada operador este mes?
- Búscame todas las notas que mencionen "sniper" / "visor" / "totem".
- Dame el historial completo de la tarea X.

## Herramientas expuestas

| Herramienta | Qué devuelve |
|---|---|
| `resumen_operacional` | Panorama global: tareas por estado, fallas abiertas, observaciones por tipo, operadores y sesiones de trabajo abiertas ahora. |
| `tareas(estado, limite)` | Lista de tareas filtradas (`activas`, `pendiente`, `en progreso`, `completada`, `validada`, `pendiente_validacion`, `cerradas`, `todas`). |
| `fallas_activas()` | Fallas reportadas sin reparación/cierre posterior. |
| `notas(tipo, dias, limite)` | Notas/observaciones (`nota`, `falla`, `reparacion`, `cierre`, `todas`). |
| `historial_tarea(tarea_uuid)` | Línea de tiempo de una tarea: observaciones, rechazos y tiempo trabajado. |
| `carga_operadores(dias)` | Carga por operador: tareas asignadas/activas, tiempo trabajado, sesión abierta. |
| `buscar(texto, limite)` | Busca un término en títulos/descripciones de tareas y en notas. |

## Requisitos

- Python 3.10+ y el paquete `mcp` (ya instalado en este entorno: `pip install mcp`).
- Conexión a internet (lee de la API cloud).

## Cómo se conecta

La config vive en [`.mcp.json`](../.mcp.json) en la raíz del proyecto. Claude Code
la detecta automáticamente al abrir esta carpeta. La primera vez te pedirá
aprobar el servidor MCP del proyecto.

Variables de entorno opcionales (no hacen falta normalmente):

| Variable | Default | Para qué |
|---|---|---|
| `SUITIME_API_URL` | la de `cloud_config.json` | Cambiar el endpoint de la API. |
| `SUITIME_API_KEY` | la de `cloud_config.json` | Otra API key. |
| `SUITIME_TTL` | `30` | Segundos de caché en memoria de los datos. |

## Probar manualmente

```powershell
$env:PYTHONUTF8="1"
python -c "from mcp_suitime import server as s; import json; print(json.dumps(s.resumen_operacional(), ensure_ascii=False, indent=2))"
```
