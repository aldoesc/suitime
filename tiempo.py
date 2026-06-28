"""Helpers de tiempo. UTC en BD, visualización en zona local configurada en constants.TIMEZONE."""
from datetime import datetime, timedelta, timezone

from constants import TIMEZONE

# Offsets fijos conocidos (sin DST). Perú/Colombia/Ecuador son UTC-5 todo el año.
_OFFSETS_FIJOS = {
    "America/Lima": -5,
    "America/Bogota": -5,
    "America/Guayaquil": -5,
    "America/Mexico_City": -6,
}

try:
    from zoneinfo import ZoneInfo  # type: ignore
    TZ_LOCAL = ZoneInfo(TIMEZONE)
except Exception:
    # Fallback para sistemas sin tzdata (Windows sin el paquete `tzdata` instalado).
    _off = _OFFSETS_FIJOS.get(TIMEZONE, 0)
    TZ_LOCAL = timezone(timedelta(hours=_off), name=TIMEZONE)

TZ_UTC = timezone.utc

_FMT_SQL = "%Y-%m-%d %H:%M:%S"


def ahora_utc() -> datetime:
    return datetime.now(TZ_UTC).replace(microsecond=0)


def ahora_utc_sql() -> str:
    return ahora_utc().strftime(_FMT_SQL)


def parse_sql_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    s = str(s).split(".")[0]  # recorta microsegundos si los trae
    try:
        dt = datetime.strptime(s, _FMT_SQL)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_UTC)
    return dt


def a_local(dt_utc: datetime | None) -> datetime | None:
    if dt_utc is None:
        return None
    return dt_utc.astimezone(TZ_LOCAL)


def formatear_local(sql_utc: str | None, con_hora: bool = True) -> str:
    dt = parse_sql_utc(sql_utc)
    if dt is None:
        return ""
    loc = a_local(dt)
    return loc.strftime("%d-%m-%Y %H:%M" if con_hora else "%d-%m-%Y")


def formatear_duracion(segundos: int | float) -> str:
    if segundos is None or segundos < 0:
        return "0m"
    s = int(segundos)
    h, r = divmod(s, 3600)
    m, _ = divmod(r, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


_DIA = timedelta(days=1)


def inicio_fin_semana_local(hoy: datetime | None = None) -> tuple[str, str]:
    """Devuelve (lunes, domingo) de la semana actual en zona local, como YYYY-MM-DD."""
    if hoy is None:
        hoy = datetime.now(TZ_LOCAL)
    else:
        hoy = hoy.astimezone(TZ_LOCAL)
    lunes = hoy - (hoy.weekday() * _DIA)
    domingo = lunes + 6 * _DIA
    return lunes.strftime("%Y-%m-%d"), domingo.strftime("%Y-%m-%d")
