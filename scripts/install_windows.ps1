# =============================================================================
# PTNT-BAL — Instalación automatizada en Windows
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
# =============================================================================
$ErrorActionPreference = "Stop"

Write-Host "== PTNT-BAL :: instalación en Windows ==" -ForegroundColor Cyan

# 1. Verificar Python 3.11+
$py = & python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python no está en el PATH. Instale Python 3.11 x64 y reintente."
    exit 1
}
Write-Host "Python detectado: $py"

# 2. Entorno virtual
if (-not (Test-Path ".venv")) {
    Write-Host "Creando entorno virtual .venv ..."
    python -m venv .venv
}
& .\.venv\Scripts\Activate.ps1

# 3. Dependencias
Write-Host "Actualizando pip e instalando el paquete (todos los extras) ..."
python -m pip install --upgrade pip
pip install -e ".[all]"

# 4. Verificación
Write-Host "Verificando configuración ..."
ptnt verificar-config -c config\base.yaml

Write-Host ""
Write-Host "== Instalación completa ==" -ForegroundColor Green
Write-Host "Siguientes pasos:"
Write-Host "  1) Defina las credenciales por variable de entorno (ver docs\SEGURIDAD.md)"
Write-Host "  2) ptnt generar-sinteticos -o data\entrada\consumos_36m.csv"
Write-Host "  3) ptnt analizar --csv data\entrada\consumos_36m.csv"
Write-Host "  4) ptnt crear-usuario analista --rol analyst"
Write-Host "  5) scripts\run_dashboard.bat   /   scripts\run_visor.bat"
