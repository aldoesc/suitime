import logging
import os

import flet as ft


def _cargar_env_local() -> None:
    """Carga variables simples `CLAVE=valor` desde `.env_local` (si existe)
    ANTES de importar módulos que leen os.environ (database.py)."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env_local")
    if not os.path.exists(ruta):
        return
    try:
        with open(ruta, "r", encoding="utf-8") as fh:
            for linea in fh:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                clave, _, valor = linea.partition("=")
                os.environ.setdefault(clave.strip(), valor.strip())
    except Exception:
        pass


_cargar_env_local()

from cloud_sync import init_sync  # noqa: E402
from database import init_db  # noqa: E402
from repositorio import usuarios_repo  # noqa: E402
from views.admin_view import AdminView  # noqa: E402
from views.operador_view import OperadorView  # noqa: E402
from views.responsive import ancho_input  # noqa: E402

log = logging.getLogger(__name__)


def main(page: ft.Page):
    page.title = "SUItime"
    page.theme_mode = "dark"
    page.padding = 0
    page.bgcolor = "#121212"

    init_db()

    # Inicializar motor de sincronización cloud (singleton global).
    # Si está habilitado en config, el hilo de fondo se encarga de push/pull cada N seg.
    def _notificar_ui_sync():
        try:
            page.pubsub.send_all("__sync_tick__")
        except Exception:
            pass

    sync = init_sync(on_ui_update=_notificar_ui_sync)
    if sync.habilitado:
        sync.start()
        log.info("Cloud sync arrancado (intervalo=%ss)", sync.config.get("sync_interval"))

    usuario_actual = {"id": None, "nombre": None, "rol": None}

    # Flet 0.84 ya no expone `page.snack_bar` como atributo predefinido (lo
    # quitaron al refactor de overlays). Antes esto explotaba con
    # AttributeError en CADA tick de sync, dejando la UI desincronizada.
    # Mantenemos una referencia propia y montamos la SnackBar en `page.overlay`.
    _snack = {"bar": None}

    def on_notification(event):
        # Filtramos el tick interno de sync: no es para mostrar al usuario.
        if event == "__sync_tick__":
            return
        bar = _snack["bar"]
        if bar is None:
            bar = ft.SnackBar(ft.Text(str(event)), duration=3000)
            _snack["bar"] = bar
            try:
                page.overlay.append(bar)
            except Exception:
                # Versiones de Flet sin `page.overlay` (improbable en 0.84,
                # pero defensivo). Caemos en la API legacy si existe.
                try:
                    page.snack_bar = bar  # type: ignore[attr-defined]
                except Exception:
                    return
        else:
            bar.content.value = str(event)
        bar.open = True
        try:
            page.update()
        except Exception:
            log.exception("Error refrescando UI tras notification")

    page.pubsub.subscribe(on_notification)
    contenedor_principal = ft.Container(expand=True)

    def cargar_login():
        w_in = ancho_input(page, max_desktop=320)
        campo_usuario = ft.TextField(
            label="Usuario", width=w_in, border_color="#333333",
            focused_border_color="#00E676",
        )
        campo_password = ft.TextField(
            label="Contraseña", password=True, can_reveal_password=True,
            width=w_in, border_color="#333333", focused_border_color="#00E676",
        )
        mensaje_error = ft.Text("", color="#FF5252", size=13)

        def intentar_login(e):
            try:
                user = usuarios_repo.autenticar(campo_usuario.value or "", campo_password.value or "")
            except Exception as exc:
                log.exception("Error autenticando: %s", exc)
                mensaje_error.value = "Error interno. Intenta de nuevo."
                page.update()
                return
            if user:
                log.info("Login ok usuario=%s rol=%s", user["nombre"], user["rol"])
                usuario_actual.update(user)
                if user["rol"] == "admin":
                    cargar_admin()
                else:
                    cargar_operador()
            else:
                log.warning("Login fallido username=%r", campo_usuario.value)
                mensaje_error.value = "Credenciales incorrectas."
                page.update()

        tarjeta_login = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(ft.Icons.LOCK, size=60, color="#00E676"),
                    ft.Text("SUItime", size=24, weight="bold", color="#FFFFFF"),
                    ft.Text("Ingresa tus credenciales", size=14, color="#9E9E9E"),
                    ft.Container(height=10),
                    campo_usuario,
                    campo_password,
                    mensaje_error,
                    ft.Container(height=5),
                    ft.ElevatedButton(
                        content=ft.Text("Iniciar Sesión", color="#121212"),
                        width=w_in,
                        height=45,
                        style=ft.ButtonStyle(
                            bgcolor="#00E676",
                            shape=ft.RoundedRectangleBorder(radius=8),
                        ),
                        on_click=intentar_login,
                    ),
                ],
                alignment="center",
                horizontal_alignment="center",
                spacing=15,
            ),
            padding=40,
            bgcolor="#1E1E1E",
            border_radius=15,
            shadow=ft.BoxShadow(spread_radius=1, blur_radius=20,
                                color="#000000", offset=ft.Offset(0, 8)),
        )

        contenedor_principal.content = ft.Container(
            content=tarjeta_login,
            alignment=ft.Alignment(0, 0),
            expand=True,
        )
        page.update()

    def cargar_admin():
        contenedor_principal.content = AdminView(page, cargar_login, usuario_actual)
        page.update()

    def cargar_operador():
        contenedor_principal.content = OperadorView(page, cargar_login, usuario_actual)
        page.update()

    page.add(contenedor_principal)
    cargar_login()


if __name__ == "__main__":
    ft.app(target=main)
