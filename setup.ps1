<#
.SYNOPSIS
  Instala todo lo necesario para el karaoke en Windows y deja la app lista para arrancar.

.DESCRIPTION
  1. Instala con winget lo que falte: uv, Node.js LTS, ffmpeg y mkcert.
  2. Instala las dependencias de Python (con el procesado de canciones: PyTorch, Demucs, Whisper...).
  3. Instala las dependencias del frontend y lo compila.
  4. Crea los certificados HTTPS locales con mkcert (los móviles solo usan el micrófono con HTTPS).

  Se puede volver a ejecutar sin problema: lo que ya está instalado se salta.
  Lo que instala por primera vez queda apuntado en .setup-state para que uninstall.cmd lo quite.

.PARAMETER NoProcessing
  No instala el procesado de canciones (PyTorch, Demucs, Whisper: varios GB).

.PARAMETER NoCerts
  No crea certificados HTTPS.

.PARAMETER DryRun
  Solo muestra lo que haría, sin instalar ni cambiar nada.

.EXAMPLE
  .\setup.cmd
  .\setup.cmd -NoProcessing
#>
param(
    [switch]$NoProcessing,
    [switch]$NoCerts,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$StateFile = Join-Path $PSScriptRoot ".setup-state"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Green
}

function Write-Warn([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Yellow
}

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    Write-Host "    > $Exe $($Arguments -join ' ')" -ForegroundColor DarkGray
    if ($DryRun) { return }
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Falló: $Exe $($Arguments -join ' ') (código $LASTEXITCODE)"
    }
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# --- .setup-state: what this script added to the computer (read by uninstall.ps1) ---

function Get-State {
    if (Test-Path -LiteralPath $StateFile) { return @(Get-Content -LiteralPath $StateFile -Encoding UTF8) }
    return @()
}

function Add-State([string]$Line) {
    if ($DryRun) { return }
    if ((Get-State) -notcontains $Line) {
        Add-Content -LiteralPath $StateFile -Value $Line -Encoding UTF8
    }
}

function Get-CacheDirs {
    $hf = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:USERPROFILE ".cache\huggingface" }
    $torch = if ($env:TORCH_HOME) { $env:TORCH_HOME } else { Join-Path $env:USERPROFILE ".cache\torch" }
    return @($hf, $torch)
}

function Install-Tool([string]$Command, [string]$WingetId, [string]$Label, [string]$Url) {
    if (Test-Command $Command) {
        Write-Ok "$Label ya está instalado"
        return
    }
    if (-not (Test-Command "winget")) {
        throw "Falta $Label y no hay winget para instalarlo. Instálalo a mano: $Url"
    }
    Write-Host "    Instalando $Label..."
    Invoke-Checked "winget" @("install", "--id", $WingetId, "--exact", "--silent",
        "--accept-source-agreements", "--accept-package-agreements")
    Add-State "tool winget:$WingetId"
    Update-SessionPath
    if (-not $DryRun -and -not (Test-Command $Command)) {
        throw "$Label se instaló pero no aparece en el PATH. Cierra esta ventana, abre otra y vuelve a ejecutar setup."
    }
}

try {
    if ($DryRun) { Write-Warn "Modo prueba (-DryRun): no se instala ni se cambia nada." }

    # The app downloads its models later: remember which caches did not exist before the first setup.
    if ((Get-State) -notcontains "caches-recorded") {
        foreach ($dir in Get-CacheDirs) {
            if (-not (Test-Path -LiteralPath $dir)) { Add-State "cache-new $dir" }
        }
        Add-State "caches-recorded"
    }

    Write-Step "Herramientas del sistema"
    Install-Tool "uv" "astral-sh.uv" "uv (Python)" "https://docs.astral.sh/uv/"
    Install-Tool "node" "OpenJS.NodeJS.LTS" "Node.js LTS" "https://nodejs.org/"
    Install-Tool "ffmpeg" "Gyan.FFmpeg" "ffmpeg" "https://ffmpeg.org/download.html"
    if (-not $NoCerts) {
        Install-Tool "mkcert" "FiloSottile.mkcert" "mkcert" "https://github.com/FiloSottile/mkcert"
    }

    Write-Step "Dependencias de Python"
    if ($NoProcessing) {
        Invoke-Checked "uv" @("sync")
    } else {
        Write-Host "    Incluye el procesado de canciones (PyTorch con CUDA, Demucs, Whisper): puede tardar y ocupar varios GB."
        Invoke-Checked "uv" @("sync", "--extra", "processing")
    }

    Write-Step "Frontend"
    Invoke-Checked "npm" @("install", "--no-fund", "--no-audit")
    Invoke-Checked "npm" @("run", "build")

    if (-not $NoCerts) {
        Write-Step "Certificados HTTPS"
        if (-not $DryRun) {
            $caroot = (& mkcert -CAROOT).Trim()
            if (-not (Test-Path -LiteralPath (Join-Path $caroot "rootCA.pem"))) { Add-State "ca-created" }
        }
        Write-Host "    mkcert instalará una autoridad local de confianza (Windows puede pedir confirmación)."
        Invoke-Checked "mkcert" @("-install")
        Invoke-Checked "uv" @("run", "python", "scripts/make_certs.py")
    }

    Write-Step "Listo"
    Write-Ok "Arranca el karaoke con:  uv run python -m server"
    if (-not $NoCerts) {
        Write-Ok "Abre https://127.0.0.1:8000/ en el portátil."
        if (-not $DryRun) {
            $caroot = (& mkcert -CAROOT).Trim()
            Write-Ok "Para quitar el aviso de seguridad en los móviles, instala en ellos: $caroot\rootCA.pem (ver README)."
        }
    } else {
        Write-Ok "Abre http://127.0.0.1:8000/ en el portátil (sin HTTPS los móviles no podrán usar el micrófono)."
    }
    Write-Ok "Para desinstalarlo todo: uninstall.cmd"
} catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
