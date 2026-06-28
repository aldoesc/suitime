from enum import Enum


class EstadoTarea(str, Enum):
    PENDIENTE = "pendiente"
    EN_PROGRESO = "en progreso"
    COMPLETADA = "completada"
    PENDIENTE_VALIDACION = "pendiente_validacion"
    VALIDADA = "validada"

    @classmethod
    def values(cls):
        return [e.value for e in cls]


class EstadoParticipante(str, Enum):
    PENDIENTE = "pendiente"
    COMPLETADO = "completado"


class Rol(str, Enum):
    ADMIN = "admin"
    OPERADOR = "operador"


class TipoAdjunto(str, Enum):
    FACTURA = "factura"
    DOCUMENTO = "documento"


COLORES_ESTADO = {
    EstadoTarea.PENDIENTE.value: "#FFA726",
    EstadoTarea.EN_PROGRESO.value: "#29B6F6",
    EstadoTarea.COMPLETADA.value: "#66BB6A",
    EstadoTarea.PENDIENTE_VALIDACION.value: "#AB47BC",
    EstadoTarea.VALIDADA.value: "#2E7D32",
}

EXTENSIONES_PERMITIDAS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".doc", ".docx", ".xlsx", ".txt"}
TAMANO_MAX_ARCHIVO = 10 * 1024 * 1024  # 10 MB
PAGINA_SIZE = 50

TIMEZONE = "America/Lima"


class TipoObjetivo(str, Enum):
    MANUAL = "manual"
    SEMANAL_AUTO = "semanal_auto"


class TipoObservacion(str, Enum):
    NOTA = "nota"
    FALLA = "falla"
    REPARACION = "reparacion"
    CIERRE = "cierre"


ETIQUETAS_OBSERVACION = {
    TipoObservacion.NOTA.value: ("Nota", "#42A5F5"),
    TipoObservacion.FALLA.value: ("Falla", "#EF5350"),
    TipoObservacion.REPARACION.value: ("Reparación", "#FFA726"),
    TipoObservacion.CIERRE.value: ("Cierre", "#66BB6A"),
}
