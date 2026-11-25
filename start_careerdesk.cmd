@echo off
chcp 65001 >nul
setlocal
set "PROJECT_DIR=%~dp0"
if not exist "%PROJECT_DIR%.venv\Scripts\python.exe" (
  echo 未找到项目虚拟环境。请先按使用说明安装。
  pause
  exit /b 1
)
cd /d "%PROJECT_DIR%"
powershell.exe -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8501/_stcore/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200 -and $r.Content.Trim() -eq 'ok') { exit 0 } } catch {} ; exit 1" >nul 2>&1
if not errorlevel 1 (
  echo 工作台已在运行，正在打开页面...
  start "" "http://127.0.0.1:8501/"
  exit /b 0
)
"%PROJECT_DIR%.venv\Scripts\python.exe" -m streamlit run "%PROJECT_DIR%app.py" --server.address 127.0.0.1 --server.port 8501 --server.runOnSave true --server.fileWatcherType watchdog --browser.gatherUsageStats false
endlocal
