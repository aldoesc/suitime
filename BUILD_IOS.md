# Build iOS de Gestor Operacional SUI

iOS no se puede compilar desde Windows porque Apple solo permite Xcode en macOS.
Este documento cubre las tres formas prácticas de obtener un `.ipa`.

## Opción A — GitHub Actions (recomendada, gratis)

El repo incluye [`.github/workflows/build-ios.yml`](.github/workflows/build-ios.yml).

### Primer build (sin firma — para TestFlight manual / sideloading)

1. Empuja el código a GitHub (si aún no lo hiciste):
   ```powershell
   git remote add origin git@github.com:<tu-usuario>/gestor-sui.git
   git push -u origin main
   ```
2. En GitHub, ve a **Actions → Build iOS → Run workflow**.
3. Elige `export_method = development` y dale a **Run**.
4. Tras ~10–15 min baja el artefacto `gestor-sui-ios.ipa` desde la página del run.

### Subir a TestFlight sin firmar en CI

- Abre `Transporter.app` en un Mac (gratis en Mac App Store).
- Arrastra el `.ipa` → Deliver.
- El build firmado por Apple aparecerá en App Store Connect → TestFlight.

### Firma automática en CI (build listo para usuarios reales)

Necesitas una cuenta **Apple Developer** activa ($99/año).

1. En tu Mac, exporta el certificado *Apple Distribution* como `.p12`:
   ```bash
   # Keychain Access → My Certificates → Apple Distribution → exportar .p12
   base64 -i distribution.p12 -o distribution.p12.b64
   ```
2. Descarga el provisioning profile desde
   https://developer.apple.com/account/resources/profiles y codifícalo:
   ```bash
   base64 -i sui.mobileprovision -o sui.mobileprovision.b64
   ```
3. En GitHub: **Settings → Secrets and variables → Actions → New secret**:
   - `APPLE_TEAM_ID` → tu Team ID (10 chars, ej. `ABC1234XYZ`)
   - `APPLE_CERTIFICATE_BASE64` → contenido de `distribution.p12.b64`
   - `APPLE_CERTIFICATE_PASSWORD` → la password del `.p12`
   - `APPLE_PROVISIONING_PROFILE_BASE64` → contenido de `sui.mobileprovision.b64`
4. Descomenta el bloque **"Import signing assets"** en `build-ios.yml`.
5. Corre de nuevo con `export_method = app-store`.

---

## Opción B — Codemagic (alternativa con UI más amigable)

1. Crea cuenta en https://codemagic.io (500 min/mes gratis con Flutter).
2. Conecta el repo GitHub.
3. En *Build* elige **Flutter App → iOS**.
4. Codemagic detecta `pyproject.toml` y llama a `flet build ipa` por ti.
5. Genera y firma el `.ipa` en su runner macOS.

---

## Opción C — Build local en Mac

Si tienes acceso a un Mac (propio o en MacinCloud ~$30/mes):

```bash
# Una sola vez:
brew install python@3.11 cocoapods
xcode-select --install            # si no tienes Xcode CLI tools
pip3 install flet-cli

# Cada vez que compiles:
pwsh ./build.ps1 ipa -IosTeamId ABC1234XYZ -IosExportMethod development
```

El script bloquea el target `ipa` si detecta Windows.

---

## Instalar el .ipa en tu iPhone para pruebas

**Sin cuenta Apple Developer** (solo el tuyo, caduca a 7 días):

1. Descarga **AltStore** (https://altstore.io) o **Sideloadly** en un Windows.
2. Conecta iPhone por USB → arrastra el `.ipa`.
3. Ajustes → General → VPN y administración de dispositivos → confía en el perfil.

**Con cuenta Apple Developer** ($99/año, sin caducidad):

- Build con `export_method = ad-hoc` + UDID del dispositivo en el provisioning
  profile → instala con Apple Configurator 2 o vía Xcode Devices.

**Producción (App Store):**

- Build con `export_method = app-store` → Transporter.app → TestFlight → revisión
  de Apple (24–48h) → disponible en App Store.

---

## Checklist de info.plist

Ya está configurado en [`pyproject.toml`](pyproject.toml) `[tool.flet.ios]`:

- `NSCameraUsageDescription` — explica por qué pides cámara (obligatorio si usas
  la cámara, sino Apple rechaza).
- `NSPhotoLibraryUsageDescription` — idem para galería.

Si añades más permisos (ubicación, micrófono, etc.), actualiza ese bloque antes
del build.
