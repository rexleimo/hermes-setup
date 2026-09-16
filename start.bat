@echo off
rem ============================================================
rem  Hermes Console 一键启动（Windows · 双击即用，无需终端经验）
rem  首次运行自动装依赖；启动成功后自动打开浏览器。
rem  关闭本窗口即停止控制台。
rem ============================================================
setlocal
cd /d "%~dp0"
set PORT=8420

rem ---- 找 uv（没有就用 python 现装一个） ----
where uv >nul 2>nul
if errorlevel 1 (
  echo [提示] 未检测到 uv，正在尝试用 pip 安装...
  where py >nul 2>nul && py -m pip install -q uv
  where python >nul 2>nul && python -m pip install -q uv
  where uv >nul 2>nul
  if errorlevel 1 (
    echo.
    echo [失败] 自动安装 uv 没成功。请二选一后重新双击本文件：
    echo    1^) 安装 Python：https://www.python.org/downloads/
    echo    2^) 手动装 uv：pip install uv
    echo.
    pause
    exit /b 1
  )
)

rem ---- 安装/更新依赖（幂等，秒级） ----
echo [1/2] 准备依赖（首次约 1 分钟）...
uv sync --quiet
if errorlevel 1 (
  echo [失败] 依赖安装出错，请把本窗口截图反馈：https://github.com/rexleimo/hermes-setup/issues
  pause
  exit /b 1
)

rem ---- 延迟 3 秒自动打开浏览器，然后前台启动服务 ----
echo [2/2] 启动控制台 http://127.0.0.1:%PORT% （关窗即停）
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%PORT%"
uv run uvicorn app.main:app --host 127.0.0.1 --port %PORT%
