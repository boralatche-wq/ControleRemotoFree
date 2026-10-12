@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Instale o Python em python.org (marque "Add to PATH") e rode de novo. & pause & exit /b 1)
if not exist .venv (
  echo Criando ambiente e instalando (so na primeira vez)...
  python -m venv .venv
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
.venv\Scripts\python app.py
pause
