@echo off
REM PTNT-BAL — Visor web de solo lectura (FastAPI) para consulta por terceros
cd /d "%~dp0.."
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
ptnt servir-visor -c config\base.yaml
