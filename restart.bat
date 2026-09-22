@echo off
rem ============================================================
rem  Hermes Console 重启（Windows · 双击即用）
rem  用法：
rem     restart.bat          用默认端口重启（默认 8420）
rem     set PORT=9000 & restart.bat   用指定端口
rem  逻辑：杀掉占用端口的进程（uvicorn）→ 重新调用 start.bat 启动。
rem  关闭本窗口即停止新启动的服务。
rem ============================================================
setlocal
cd /d "%~dp0"

if not defined PORT set PORT=%HERMES_CONSOLE_PORT%
if not defined PORT set PORT=8420

echo [restart] 准备重启 Hermes Console（端口 %PORT%）...

rem ---- 杀掉当前占用端口的进程 -------------------------------------
echo [restart] 查找占用端口 %PORT% 的进程...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%  LISTENING"') do (
  echo [restart] 终止 PID %%a
  taskkill /f /pid %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

rem  重试一次，强杀仍活着的
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%  LISTENING"') do (
  echo [restart] 强杀 PID %%a
  taskkill /f /pid %%a >nul 2>&1
)

echo [restart] 调用 start.bat 重新启动...
call start.bat
endlocal
