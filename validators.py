from datetime import datetime


class ValidationError(ValueError):
    pass


def requerido(valor, campo: str) -> str:
    if valor is None or str(valor).strip() == "":
        raise ValidationError(f"El campo '{campo}' es obligatorio.")
    return str(valor).strip()


FORMATO_UI = "%d-%m-%Y"
FORMATO_BD = "%Y-%m-%d"


def validar_fecha(valor, campo: str, opcional: bool = True):
    """Acepta DD-MM-YYYY (UI) o YYYY-MM-DD (BD). Devuelve siempre YYYY-MM-DD."""
    if valor is None or str(valor).strip() == "":
        if opcional:
            return None
        raise ValidationError(f"El campo '{campo}' es obligatorio.")
    s = str(valor).strip().replace("/", "-")
    for fmt in (FORMATO_UI, FORMATO_BD):
        try:
            return datetime.strptime(s, fmt).strftime(FORMATO_BD)
        except ValueError:
            continue
    raise ValidationError(f"'{campo}' debe tener formato DD-MM-YYYY.")


def fecha_a_ui(valor_bd) -> str:
    """YYYY-MM-DD → DD-MM-YYYY. Vacío si None."""
    if not valor_bd:
        return ""
    try:
        return datetime.strptime(str(valor_bd), FORMATO_BD).strftime(FORMATO_UI)
    except ValueError:
        return str(valor_bd)


def validar_rango_fechas(inicio, fin):
    if inicio and fin:
        d_ini = datetime.strptime(inicio, "%Y-%m-%d")
        d_fin = datetime.strptime(fin, "%Y-%m-%d")
        if d_fin < d_ini:
            raise ValidationError("La fecha de fin no puede ser anterior a la de inicio.")


def validar_monto(valor, campo: str = "monto"):
    if valor is None or str(valor).strip() == "":
        return None
    try:
        m = float(str(valor).replace(",", "."))
    except ValueError:
        raise ValidationError(f"'{campo}' debe ser numérico.")
    if m < 0:
        raise ValidationError(f"'{campo}' no puede ser negativo.")
    if m > 10_000_000:
        raise ValidationError(f"'{campo}' excede el máximo permitido.")
    return m


def validar_progreso(valor):
    try:
        p = float(valor)
    except (TypeError, ValueError):
        raise ValidationError("El progreso debe ser numérico.")
    if p < 0 or p > 100:
        raise ValidationError("El progreso debe estar entre 0 y 100.")
    return p
