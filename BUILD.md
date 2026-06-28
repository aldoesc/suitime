# Compilación y distribución de Gestor Operacional SUI

Esta guía cubre:

- **Windows** → ejecutable con `flet build windows` → instalador `.exe` con Inno Setup.
- **Android** → APK / AAB con `flet build apk` / `flet build aab`.
- **iOS** → requiere macOS + Xcode (no cubierto aquí en profundidad).

Todo se orquesta desde `build.ps1` (PowerShell) en la raíz del proyecto.

---

## Estructura relevante

```
gestor-sui/
├── main.py                  # entry point
├── pyproject.toml           # metadata + config de flet build
├── build.ps1                # orquestador de builds
├── icono.ico                # icono original (Windows)
├── assets/
│   ├── icon.png             # 1024x1024 — generado automáticamente desde icono.ico
│   └── icon_windows.png     # 256x256 — específico Windows
└── installer/
    └── gestor-sui.iss       # script Inno Setup
```

> El `icon.png` se genera la primera vez ejecutando el comando de conversión
> del apartado **Regenerar iconos** más abajo. Si cambias `icono.ico`, vuelve
> a ejecutarlo.

---

## Prerrequisitos

### Comunes (todo build)

1. **Python 3.10+** con Flet:
   ```powershell
   pip install flet
   ```

2. **Flutter SDK** — Flet usa Flutter internamente para empaquetar.
   - Descarga: https://docs.flutter.dev/get-started/install/windows
   - Descomprimir en `C:\src\flutter` (por ejemplo).
   - Añadir `C:\src\flutter\bin` al PATH.
   - Verificar: `flutter --version` debe responder.
   - Correr una vez `flutter doctor` y resolver los items en rojo
     relevantes para tu target (Windows y/o Android).

### Para compilar Windows

- **Visual Studio 2022 Community** (gratis).
  - Durante la instalación marca el workload **"Desktop development with C++"**.
  - Sin esto, `flet build windows` falla con errores de `cl.exe`.

### Para compilar Android

- **Android Studio**: https://developer.android.com/studio
  - Al abrirlo por primera vez instala SDK + Platform-Tools automáticamente.
  - En *SDK Manager → SDK Tools* marca también **NDK (Side by side)** y
    **CMake**.
- **JDK 17** (lo trae Android Studio embebido, pero conviene uno del sistema).
- Variable de entorno `ANDROID_HOME` → `%LOCALAPPDATA%\Android\Sdk`.
- `flutter doctor --android-licenses` y aceptar todo.

### Para publicar en Play Store (AAB firmado)

Generar keystore **una sola vez** y guardarlo fuera del repo:

```powershell
keytool -genkey -v `
    -keystore gestor-sui.jks `
    -alias sui `
    -keyalg RSA -keysize 2048 -validity 10000
```

Apúntate las contraseñas — si las pierdes, pierdes la capacidad de publicar
actualizaciones de la misma app en Play Store.

---

## Compilar Windows (para Inno Setup)

```powershell
.\build.ps1 windows
```

Esto ejecuta internamente `flet build windows`. El resultado queda en:

```
build\windows\
├── gestor-operacional-sui.exe
├── *.dll
└── data\
```

Todo ese directorio es lo que empaqueta el instalador.

### Generar instalador con Inno Setup

1. Descarga e instala **Inno Setup 6**: https://jrsoftware.org/isdl.php
2. Abre `installer\gestor-sui.iss` con *Inno Setup Compiler*.
3. *Build → Compile* (Ctrl+F9).
4. Salida: `installer\Output\GestorSUI-Setup-1.0.0.exe`

Ese `.exe` es el instalador que distribuyes a los usuarios. Hace:

- Pregunta idioma (español).
- Instala en `C:\Program Files\GestorSUI\` (o perfil de usuario si no hay admin).
- Crea grupo en menú Inicio.
- Crea atajo de escritorio (opcional, desmarcado por defecto).
- Ofrece lanzar la app al terminar.

---

## Compilar APK Android (debug — instalación directa por USB)

```powershell
.\build.ps1 apk
```

Salida: `build\apk\app-debug.apk` (nombre puede variar).

### Instalar en móvil

Activa **Opciones de desarrollador → Depuración USB** en el móvil y conéctalo
por cable:

```powershell
adb install -r .\build\apk\app-debug.apk
```

También puedes pasar el APK al móvil por email/Drive y abrirlo (pedirá
"permitir fuentes desconocidas" la primera vez).

---

## Compilar AAB firmado (Play Store)

```powershell
.\build.ps1 aab `
    -Release `
    -Keystore "C:\ruta\a\gestor-sui.jks" `
    -KeyAlias sui `
    -KeystorePassword "XXXX" `
    -KeyPassword "XXXX"
```

Salida: `build\aab\app-release.aab` — sube ese archivo a Google Play Console.

---

## Regenerar iconos (si cambias `icono.ico`)

```powershell
python -c "from PIL import Image; `
im = Image.open('icono.ico'); `
w,h = im.size; side = max(w,h); `
lienzo = Image.new('RGBA',(side,side),(0,0,0,0)); `
lienzo.paste(im.convert('RGBA'), ((side-w)//2,(side-h)//2)); `
lienzo.resize((1024,1024), Image.LANCZOS).save('assets/icon.png','PNG'); `
lienzo.resize((256,256),  Image.LANCZOS).save('assets/icon_windows.png','PNG'); `
print('Iconos regenerados.')"
```

---

## Limpiar artefactos

```powershell
.\build.ps1 clean
```

Borra `build\`, `.flet\`, `__pycache__\`.

---

## Versionado de releases

Antes de publicar una versión nueva, actualiza en `pyproject.toml`:

```toml
[project]
version = "1.1.0"

[tool.flet.android]
version_name = "1.1.0"
version_code = 2           # siempre > el anterior, entero

[tool.flet.windows]
product_version = "1.1.0"
file_version = "1.1.0.0"
```

Y en `installer\gestor-sui.iss`:

```
#define MyAppVersion "1.1.0"
```

---

## Troubleshooting

| Error | Causa | Solución |
|---|---|---|
| `flet: command not found` | Flet no está en PATH | `pip install flet` con el mismo Python que usa `python main.py` |
| `flutter: command not found` | Flutter SDK no en PATH | Reinstalar o añadir `flutter\bin` al PATH del sistema |
| `cl.exe not found` al compilar Windows | Falta workload C++ en VS | Instalar "Desktop development with C++" desde VS Installer |
| `Android SDK not found` | Falta ANDROID_HOME | Configurar variable de entorno o reinstalar Android Studio |
| `Gradle build failed` al compilar APK | Licencias Android no aceptadas | `flutter doctor --android-licenses` y pulsar `y` a todo |
| APK instala pero la app cruza al abrirse | Falta de permisos o assets faltantes | Revisar `pyproject.toml → [tool.flet.android] permissions` |
| Pantalla blanca al arrancar APK | Flet no encontró `main.py` | Verificar que `module = "main"` esté en `[tool.flet]` y que `main.py` defina `main(page)` |
| Inno Setup: `File not found: ...exe` | No ejecutaste build Windows antes | Correr `.\build.ps1 windows` primero |
