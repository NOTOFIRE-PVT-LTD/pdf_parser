@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3.12 -m venv .venv 2>nul || python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Starting Tender Parser API (FastAPI)...
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
endlocal
