<#
.SYNOPSIS
  Deshace lo que hizo setup en este ordenador, sin tocar lo que ya estaba instalado antes.

.DESCRIPTION
  Siempre quita lo que es solo de este proyecto y se puede regenerar: .venv, node_modules, web/dist y certs.
  Además, según lo apuntado por setup en .setup-state:
  - la autoridad de certificados de mkcert, si la creó setup;
  - las cachés de modelos (Hugging Face, PyTorch) que no existían antes de instalar;
  - las herramientas que instaló con winget (uv, Node.js, ffmpeg, mkcert) y sus cachés.
  La biblioteca de canciones (data/) se conserva salvo con -RemoveData.

.PARAMETER RemoveData
  Borra también data/: canciones importadas, pistas procesadas, letras y la sesión de Spotify.

.PARAMETER Yes
  No pide confirmación.

.PARAMETER DryRun
  Solo muestra lo que haría, sin borrar nada.
#>
param(
    [switch]$RemoveData,
    [switch]$Yes,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$StateFile = Join-Path $PSScriptRoot ".setup-state"
$Problems = 0

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Green
}

function Write-Warn([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Yellow
    $script:Problems++
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# Runs a command; failures are reported but do not stop the uninstall.
function Invoke-Soft([string]$Exe, [string[]]$Arguments) {
    Write-Host "    > $Exe $($Arguments -join ' ')" -ForegroundColor DarkGray
    if ($DryRun) { return }
    try {
        & $Exe @Arguments
        if ($LASTEXITCODE -ne 0) { Write-Warn "$Exe terminó con código $LASTEXITCODE" }
    } catch {
        Write-Warn "No se pudo ejecutar ${Exe}: $($_.Exception.Message)"
    }
}

function Remove-Dir([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Write-Host "    borrar $Path" -ForegroundColor DarkGray
    if ($DryRun) { return }
    try {
        # \\?\ avoids the 260-character limit (PyTorch has very deep paths).
        Remove-Item -LiteralPath ("\\?\" + (Resolve-Path -LiteralPath $Path).Path) -Recurse -Force
    } catch {
        Write-Warn "No se pudo borrar ${Path}: $($_.Exception.Message)"
    }
}

$state = @()
if (Test-Path -LiteralPath $StateFile) { $state = @(Get-Content -LiteralPath $StateFile -Encoding UTF8) }
$tools = @($state | Where-Object { $_ -like "tool winget:*" } | ForEach-Object { $_.Substring(12) })
$newCaches = @($state | Where-Object { $_ -like "cache-new *" } | ForEach-Object { $_.Substring(10) })
$caCreated = $state -contains "ca-created"
$projectDirs = @(".venv", "node_modules", "web\dist", "certs") | ForEach-Object { Join-Path $PSScriptRoot $_ }
if ($RemoveData) { $projectDirs += Join-Path $PSScriptRoot "data" }

try {
    if ($DryRun) { Write-Host "    Modo prueba (-DryRun): no se borra nada." -ForegroundColor Yellow }

    $running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -and $_.Path.StartsWith($PSScriptRoot, [StringComparison]::OrdinalIgnoreCase) } catch { $false }
    })
    if ($running.Count -gt 0 -and -not $DryRun) {
        throw "El karaoke sigue abierto ($($running[0].ProcessName)). Ciérralo y vuelve a ejecutar uninstall."
    }

    Write-Host ""
    Write-Host "Se va a quitar:" -ForegroundColor Cyan
    foreach ($dir in $projectDirs) { if (Test-Path -LiteralPath $dir) { Write-Host "  - $dir" } }
    foreach ($dir in $newCaches) { if (Test-Path -LiteralPath $dir) { Write-Host "  - caché de modelos $dir" } }
    if ($caCreated) { Write-Host "  - la autoridad de certificados local de mkcert" }
    foreach ($id in $tools) { Write-Host "  - $id (instalado por setup)" }
    if (-not $RemoveData -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot "data"))) {
        Write-Host "Se conserva data\ (tu biblioteca de canciones). Usa -RemoveData para borrarla." -ForegroundColor DarkGray
    }
    if ($state.Count -eq 0) {
        Write-Host "No hay .setup-state: setup no apuntó nada, así que no se desinstala ninguna herramienta." -ForegroundColor DarkGray
    }
    if (-not $Yes -and -not $DryRun) {
        $answer = Read-Host "¿Continuar? (s/N)"
        if ($answer -notmatch "^(s|si|sí|y|yes)$") {
            Write-Host "Cancelado."
            exit 0
        }
    }

    Write-Step "Archivos del proyecto"
    foreach ($dir in $projectDirs) { Remove-Dir $dir }

    if ($newCaches.Count -gt 0) {
        Write-Step "Cachés de modelos"
        foreach ($dir in $newCaches) { Remove-Dir $dir }
    }

    if ($caCreated) {
        Write-Step "Autoridad de certificados de mkcert"
        if (Test-Command "mkcert") {
            $caroot = (& mkcert -CAROOT).Trim()
            Invoke-Soft "mkcert" @("-uninstall")
            Remove-Dir $caroot
        } else {
            Write-Warn "mkcert ya no está instalado: quita a mano la CA «mkcert» de los certificados de Windows."
        }
    }

    if ($tools -contains "astral-sh.uv" -and (Test-Command "uv")) {
        Write-Step "Cachés de uv"
        Invoke-Soft "uv" @("python", "uninstall", "--all")
        Invoke-Soft "uv" @("cache", "clean")
    }
    if ($tools -contains "OpenJS.NodeJS.LTS") {
        Remove-Dir (Join-Path $env:LOCALAPPDATA "npm-cache")
    }

    if ($tools.Count -gt 0) {
        Write-Step "Herramientas instaladas por setup"
        foreach ($id in $tools) {
            Invoke-Soft "winget" @("uninstall", "--id", $id, "--exact", "--silent", "--accept-source-agreements")
        }
    }

    if (-not $DryRun -and (Test-Path -LiteralPath $StateFile)) { Remove-Item -LiteralPath $StateFile -Force }

    Write-Step "Listo"
    if ($Problems -gt 0) {
        Write-Host "    Terminado con $Problems aviso(s): revisa los mensajes de arriba." -ForegroundColor Yellow
    } else {
        Write-Ok "Desinstalado. El código del proyecto sigue en esta carpeta; bórrala si ya no la quieres."
    }
} catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
