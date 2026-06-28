# =============================================================================
# Build automatizado de Gestor Operacional SUI con `flet build`.
#
# Uso:
#   .\build.ps1 windows     # Ejecutable Windows -> build\windows\  (para Inno Setup)
#   .\build.ps1 apk         # APK Android (debug) -> build\apk\
#   .\build.ps1 aab         # Android App Bundle firmado para Play Store
#   .\build.ps1 ipa         # iOS .ipa (SOLO macOS + Xcode) -> build\ipa\
#   .\build.ps1 clean       # Borra build\ y .flet\
#   .\build.ps1 all         # windows + apk
#
# Prerrequisitos (una sola vez — ver BUILD.md para detalles):
#   - Python 3.10+ con Flet instalado (pip install flet)
#   - Flutter SDK en PATH (flutter --version debe responder)
#   - Para windows: Visual Studio 2022 con workload "Desktop development with C++"
#   - Para apk:     Android SDK + NDK + JDK 17 (Android Studio los instala),
#                   variable ANDROID_HOME apuntando al SDK
#   - Para aab:     keystore propio (ver sección AAB abajo)
#   - Para ipa:     macOS con Xcode 15+, CocoaPods (sudo gem install cocoapods),
#                   cuenta Apple Developer si quieres firmar para dispositivo.
#                   En Windows este target NO funciona — usa GitHub Actions
#                   (.github/workflows/build-ios.yml) o Codemagic.
# =============================================================================

param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet('windows','apk','aab','ipa','clean','all')]
    [string]$Target,

    # Opcionales para release firmado (solo `aab` o `apk --release`)
    [string]$Keystore,
    [string]$KeyAlias,
    [string]$KeystorePassword,
    [string]$KeyPassword,
    [switch]$Release,

    # Opcionales para iOS firmado
    [string]$IosTeamId,
    [string]$IosExportMethod = 'development'  # development | ad-hoc | app-store | enterprise
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

# Forzar UTF-8 en IO de Python — sin esto, Flet CLI (que usa rich) explota con
# UnicodeEncodeError al pintar spinners en terminales cp1252.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Require-Cmd($cmd, $mensajeInstalar) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Falta '$cmd' en PATH." -ForegroundColor Red
        Write-Host $mensajeInstalar -ForegroundColor Yellow
        exit 1
    }
}

# Limpieza pre-build. CRÍTICO: el `build_exclude` de Flet en pyproject.toml no
# filtra de forma fiable los patrones recursivos (vimos installer/Output/*.exe
# de 73 MB metidos dentro de app.zip pese a estar listados en exclude). La
# única forma confiable es mover físicamente esos archivos fuera del proyecto
# antes del build y restaurarlos después.
$script:_BUILD_BACKUP_DIR = $null

# Sidecar que mapea nombre-de-archivo-en-backup → ruta absoluta original.
# Se escribe DENTRO del backup dir antes de cada Move-Item, así que si la
# instancia de PowerShell muere (Ctrl+C, exit, crash, pipeline truncado) y
# Restore-PreBuildBackup nunca corre, el siguiente build puede recuperar los
# archivos huérfanos del TEMP leyendo este manifest. Sin esto, perdimos
# icono.ico/icono.png/installers en una iteración previa.
$script:_BUILD_MANIFEST_NAME = '_suitime_restore_manifest.txt'

function Recover-OrphanedBackups {
    # Busca cualquier suitime_build_backup_* en TEMP de runs previos cuyo
    # Restore-PreBuildBackup no haya corrido, y devuelve los archivos a su
    # sitio leyendo el manifest. Tras restaurar, borra el dir.
    $orphans = Get-ChildItem $env:TEMP -Directory -Filter 'suitime_build_backup_*' `
                  -ErrorAction SilentlyContinue
    foreach ($dir in $orphans) {
        $manifest = Join-Path $dir.FullName $script:_BUILD_MANIFEST_NAME
        if (-not (Test-Path $manifest)) {
            # Backup viejo sin manifest → no podemos restaurar con seguridad,
            # lo dejamos. (No lo borramos por si tiene .exe firmados que el
            # usuario quiera rescatar manualmente.)
            continue
        }
        Write-Host "  recuperando backup huérfano: $($dir.Name)"
        Get-Content $manifest | ForEach-Object {
            $parts = $_ -split '\|', 2
            if ($parts.Length -ne 2) { return }
            $name = $parts[0]
            $orig = $parts[1]
            $src  = Join-Path $dir.FullName $name
            if (-not (Test-Path $src)) { return }
            $origDir = Split-Path -Parent $orig
            if ($origDir -and -not (Test-Path $origDir)) {
                New-Item -ItemType Directory -Force -Path $origDir | Out-Null
            }
            if (-not (Test-Path $orig)) {
                Move-Item -Force $src $orig
                Write-Host "    restaurado: $orig"
            } else {
                # El destino ya existe (build posterior generó nuevo). No
                # pisamos: dejamos el backup para inspección manual.
                Write-Host "    SKIP (ya existe destino): $orig"
            }
        }
        # Si queda algo distinto al manifest, no borrar (puede ser huérfano útil).
        $resto = Get-ChildItem $dir.FullName -File |
                 Where-Object { $_.Name -ne $script:_BUILD_MANIFEST_NAME }
        if (-not $resto) {
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $dir.FullName
        }
    }
}

function Add-ToManifest($backupRoot, $name, $originalPath) {
    $manifest = Join-Path $backupRoot $script:_BUILD_MANIFEST_NAME
    Add-Content -Path $manifest -Value "$name|$originalPath" -Encoding UTF8
}

function Clean-PreBuild {
    Write-Step "Preparando snapshot limpio para flet build..."

    # 0) Recuperar backups huérfanos de runs previos antes de tocar nada.
    Recover-OrphanedBackups

    # 1) Mover archivos pesados que Flet snapshotea aunque estén en exclude.
    $backupRoot = Join-Path $env:TEMP ("suitime_build_backup_" +
                                       (Get-Date -Format yyyyMMdd_HHmmss))
    $moved = @()

    # installer/Output/*.exe — los .exe firmados se conservan, sólo los
    # llevamos a un temp y volvemos a su sitio al terminar.
    if (Test-Path 'installer\Output') {
        $exes = Get-ChildItem 'installer\Output' -Filter '*.exe' `
                -ErrorAction SilentlyContinue
        foreach ($f in $exes) {
            if (-not (Test-Path $backupRoot)) {
                New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
            }
            $dest = Join-Path $backupRoot $f.Name
            Move-Item -Force $f.FullName $dest
            $moved += @{ Src = $f.FullName; Dest = $dest }
            Add-ToManifest $backupRoot $f.Name $f.FullName
            Write-Host "  movido fuera del bundle: installer\Output\$($f.Name)"
        }
    }

    # Iconos sueltos en raíz (Inno los usa, Flet no debería verlos).
    foreach ($icono in @('icono.ico', 'icono.png')) {
        if (Test-Path $icono) {
            if (-not (Test-Path $backupRoot)) {
                New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
            }
            $orig = Join-Path (Get-Location) $icono
            $dest = Join-Path $backupRoot $icono
            Move-Item -Force $icono $dest
            $moved += @{ Src = $orig; Dest = $dest }
            Add-ToManifest $backupRoot $icono $orig
            Write-Host "  movido fuera del bundle: $icono"
        }
    }

    if ($moved.Count -gt 0) {
        $script:_BUILD_BACKUP_DIR = $backupRoot
        $script:_BUILD_BACKUP_FILES = $moved
    }

    # 2) Borrar artefactos que se regeneran solos.
    foreach ($p in @('build', '.flet', 'build.zip', 'gestor.log')) {
        if (Test-Path $p) {
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $p
            Write-Host "  borrado: $p"
        }
    }
    # Logs sueltos de iteraciones previas (build_apk.log, build_inno.log…)
    Get-ChildItem -Path '.' -Filter '*.log' -File -ErrorAction SilentlyContinue |
        ForEach-Object {
            Remove-Item -Force -ErrorAction SilentlyContinue $_.FullName
            Write-Host "  borrado: $($_.Name)"
        }
    # __pycache__ recursivos en TODO el proyecto.
    Get-ChildItem -Path '.' -Directory -Recurse -Filter '__pycache__' `
        -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    # 3) BD local del desarrollador — Flet la mete en el bundle; al primer
    # arranque la app crea la suya en %APPDATA%, así que no se necesita.
    if (Test-Path 'gestor_operacional.db') {
        if (-not (Test-Path $backupRoot)) {
            New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
            $script:_BUILD_BACKUP_DIR = $backupRoot
            $script:_BUILD_BACKUP_FILES = @()
        }
        $dest = Join-Path $backupRoot 'gestor_operacional.db'
        $orig = Join-Path (Get-Location) 'gestor_operacional.db'
        Move-Item -Force 'gestor_operacional.db' $dest
        $script:_BUILD_BACKUP_FILES += @{ Src = $orig; Dest = $dest }
        Add-ToManifest $backupRoot 'gestor_operacional.db' $orig
        Write-Host "  movida fuera del bundle: gestor_operacional.db"
    }
}

function Restore-PreBuildBackup {
    if (-not $script:_BUILD_BACKUP_DIR -or
        -not (Test-Path $script:_BUILD_BACKUP_DIR)) { return }
    Write-Host ""
    Write-Step "Restaurando archivos preservados..."
    foreach ($entry in $script:_BUILD_BACKUP_FILES) {
        $destDir = Split-Path -Parent $entry.Src
        if ($destDir -and -not (Test-Path $destDir)) {
            New-Item -ItemType Directory -Force -Path $destDir | Out-Null
        }
        if (Test-Path $entry.Dest) {
            Move-Item -Force $entry.Dest $entry.Src
            Write-Host "  restaurado: $($entry.Src)"
        }
    }
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $script:_BUILD_BACKUP_DIR
    $script:_BUILD_BACKUP_DIR = $null
    $script:_BUILD_BACKUP_FILES = $null
}

# --- Clean ---------------------------------------------------------------
if ($Target -eq 'clean') {
    Write-Step "Limpiando artefactos de build…"
    foreach ($p in @('build', '.flet', '__pycache__')) {
        if (Test-Path $p) {
            Remove-Item -Recurse -Force $p
            Write-Host "  borrado: $p"
        }
    }
    Write-Host "Listo." -ForegroundColor Green
    exit 0
}

# --- Checks comunes ------------------------------------------------------
Require-Cmd 'flet'   'Instala Flet: pip install flet'
Require-Cmd 'flutter' 'Instala Flutter SDK: https://docs.flutter.dev/get-started/install/windows y añade flutter\bin al PATH'

Write-Step "Versiones detectadas"
flet --version
flutter --version | Select-Object -First 1

# --- Windows -------------------------------------------------------------
function Build-Windows {
    Clean-PreBuild
    try {
        Write-Step "Compilando Windows (build\windows\)…"
        flet build windows --verbose
        if ($LASTEXITCODE -ne 0) {
            Write-Host "flet build windows falló." -ForegroundColor Red
            exit $LASTEXITCODE
        }
        $exe = Get-ChildItem "build\windows\*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($exe) {
            Write-Host ""
            Write-Host "OK. Ejecutable: $($exe.FullName)" -ForegroundColor Green
            Write-Host "Siguiente paso → compilar installer con Inno Setup:"
            Write-Host "   1. Abre Inno Setup Compiler"
            Write-Host "   2. File > Open > installer\gestor-sui.iss"
            Write-Host "   3. Build > Compile  (Ctrl+F9)"
        }
    } finally {
        Restore-PreBuildBackup
    }
}

# --- APK Android ---------------------------------------------------------
function Build-Apk {
    Clean-PreBuild
    try {
        Write-Step "Compilando APK Android (build\apk\)…"
        # Sólo arm64-v8a: cubre >98% de dispositivos Android modernos. Las
        # otras arquitecturas (armeabi-v7a, x86_64) sumaban ~37 MB de libs
        # nativas que ningún teléfono real usa.
        # NOTA: el flag `--arch` de Flet sólo afecta builds macOS. Para
        # restringir las ABIs en Android hay que pasar el flag de Flutter
        # vía `--flutter-build-args`.
        $args = @('build', 'apk', '--verbose',
                  '--flutter-build-args=--target-platform=android-arm64')
        if ($Release) {
            if (-not $Keystore) {
                Write-Host "Para --Release necesitas -Keystore, -KeyAlias, -KeystorePassword, -KeyPassword." -ForegroundColor Red
                exit 1
            }
            $args += @(
                '--release',
                '--android-signing-key-store', $Keystore,
                '--android-signing-key-alias', $KeyAlias,
                '--android-signing-key-store-password', $KeystorePassword,
                '--android-signing-key-password', $KeyPassword
            )
        }
        flet @args
        if ($LASTEXITCODE -ne 0) {
            Write-Host "flet build apk falló." -ForegroundColor Red
            exit $LASTEXITCODE
        }
        $apk = Get-ChildItem "build\apk\*.apk" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($apk) {
            Write-Host ""
            Write-Host "OK. APK: $($apk.FullName)" -ForegroundColor Green
            Write-Host "Instalar en móvil conectado por USB con depuración activada:"
            Write-Host "   adb install -r `"$($apk.FullName)`""
        }
    } finally {
        Restore-PreBuildBackup
    }
}

# --- AAB Android (Play Store) --------------------------------------------
function Build-Aab {
    Clean-PreBuild
    try {
        Write-Step "Compilando Android App Bundle (build\aab\)…"
        if (-not $Keystore) {
            Write-Host "Para publicar en Play Store necesitas un keystore propio." -ForegroundColor Red
            Write-Host "Generar uno:"
            Write-Host "   keytool -genkey -v -keystore gestor-sui.jks -alias sui -keyalg RSA -keysize 2048 -validity 10000"
            exit 1
        }
        flet build aab `
            --flutter-build-args=--target-platform=android-arm64 `
            --android-signing-key-store $Keystore `
            --android-signing-key-alias $KeyAlias `
            --android-signing-key-store-password $KeystorePassword `
            --android-signing-key-password $KeyPassword `
            --verbose
        if ($LASTEXITCODE -ne 0) {
            Write-Host "flet build aab falló." -ForegroundColor Red
            exit $LASTEXITCODE
        }
        $aab = Get-ChildItem "build\aab\*.aab" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($aab) {
            Write-Host ""
            Write-Host "OK. AAB: $($aab.FullName)" -ForegroundColor Green
            Write-Host "Sube el .aab a Google Play Console."
        }
    } finally {
        Restore-PreBuildBackup
    }
}

# --- iOS (ipa) — solo macOS ---------------------------------------------
function Build-Ipa {
    # Bloqueo temprano: si no estamos en macOS no tiene sentido seguir.
    $esMac = $false
    try { $esMac = $IsMacOS } catch { $esMac = $false }
    if (-not $esMac) {
        Write-Host ""
        Write-Host "iOS requiere macOS con Xcode. No se puede compilar desde Windows." -ForegroundColor Red
        Write-Host "Opciones:" -ForegroundColor Yellow
        Write-Host "  1) GitHub Actions:  push a main dispara .github/workflows/build-ios.yml"
        Write-Host "  2) Codemagic:       conecta el repo en https://codemagic.io (500 min gratis)"
        Write-Host "  3) MacinCloud:      alquilar Mac remoto y ejecutar: pwsh ./build.ps1 ipa"
        exit 1
    }

    Require-Cmd 'pod' 'Instala CocoaPods: sudo gem install cocoapods'

    Write-Step "Compilando iOS (build/ipa/)…"
    $args = @('build', 'ipa', '--verbose')
    if ($IosTeamId) {
        $args += @('--ios-team-id', $IosTeamId)
    }
    if ($IosExportMethod) {
        $args += @('--ios-export-method', $IosExportMethod)
    }
    flet @args
    if ($LASTEXITCODE -ne 0) {
        Write-Host "flet build ipa falló." -ForegroundColor Red
        exit $LASTEXITCODE
    }
    $ipa = Get-ChildItem "build/ipa/*.ipa" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ipa) {
        Write-Host ""
        Write-Host "OK. IPA: $($ipa.FullName)" -ForegroundColor Green
        Write-Host "Siguiente paso →"
        Write-Host "  - TestFlight: sube el .ipa desde Xcode > Organizer o Transporter.app"
        Write-Host "  - Dispositivo propio: instala con 'xcrun devicectl device install app'"
    }
}

switch ($Target) {
    'windows' { Build-Windows }
    'apk'     { Build-Apk }
    'aab'     { Build-Aab }
    'ipa'     { Build-Ipa }
    'all'     { Build-Windows; Build-Apk }
}

Write-Host ""
Write-Host "=== Build terminado ===" -ForegroundColor Green
