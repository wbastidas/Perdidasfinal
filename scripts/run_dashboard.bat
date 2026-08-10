@echo off
REM PTNT-BAL — Tablero de analisis (Streamlit) para escritorio
cd /d "%~dp0.."
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
ptnt dashboard -c config\base.yaml
