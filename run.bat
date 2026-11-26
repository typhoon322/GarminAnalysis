@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
where python >nul 2>nul
if errorlevel 1 (
  echo 需要已安装的 Python 3.11+，并且 python 在 PATH 里。
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe python -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)"
if errorlevel 1 (
  echo 需要 Python 3.11 或更高版本。
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import fastapi,uvicorn,plotly,httpx" 1>nul 2>nul
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
if errorlevel 1 goto fail
echo 本机页面: http://127.0.0.1:8765
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
exit /b %errorlevel%
:fail
echo 启动失败。
pause
exit /b 1
