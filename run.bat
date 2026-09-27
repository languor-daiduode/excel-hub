@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0..\venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERROR] Python venv was not found:
    echo %PY%
    pause
    exit /b 1
)

echo [1/2] Refreshing Excel data...
"%PY%" "%CD%\pipeline\run.py" --refresh
if errorlevel 1 (
    echo [ERROR] Data refresh failed.
    pause
    exit /b 1
)

echo.
echo [2/2] Starting dashboard...
echo URL: http://127.0.0.1:8501
echo Keep this window open. Open the URL above in your browser.
"%PY%" -m streamlit run "%CD%\app\dashboard.py" --server.headless=true --server.address=127.0.0.1 --server.port=8501 --browser.gatherUsageStats=false
pause

