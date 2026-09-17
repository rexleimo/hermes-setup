@echo off
rem ============================================================
rem  Hermes Console 一键启动（Windows · 双击即用，无需终端经验）
rem  首次运行自动装依赖；启动成功后自动打开浏览器。
rem  关闭本窗口即停止控制台。
rem  服务器对外：先 set HERMES_CONSOLE_HOST=0.0.0.0 再双击（先配好密钥/HTTPS/白名单）
rem ============================================================
setlocal
cd /d "%~dp0"
if not defined PORT set PORT=%HERMES_CONSOLE_PORT%
if not defined PORT set PORT=8420
if not defined HOST set HOST=%HERMES_CONSOLE_HOST%
if not defined HOST set HOST=0.0.0.0

rem ---- 找 uv（没有就用 python 现装一个；官方源失败自动换国内镜像重试） ----
where uv >nul 2>nul
if errorlevel 1 (
  echo [提示] 未检测到 uv，正在尝试用 pip 安装...
  where py >nul 2>nul && py -m pip install -q uv
  where python >nul 2>nul && python -m pip install -q uv
  where uv >nul 2>nul
  if errorlevel 1 (
    echo [提示] 官方源安装失败（多为网络原因），改用国内镜像重试...
    where py >nul 2>nul && py -m pip install -q uv -i https://pypi.tuna.tsinghua.edu.cn/simple
    where python >nul 2>nul && python -m pip install -q uv -i https://pypi.tuna.tsinghua.edu.cn/simple
    where uv >nul 2>nul
    if errorlevel 1 (
      echo.
      echo [失败] 自动安装 uv 没成功。请依次尝试后重新双击本文件：
      echo    1^) 安装 Python：https://www.python.org/downloads/
      echo    2^) 开代理后再双击本文件
      echo    3^) 手动在命令行执行：pip install uv
      echo    仍有问题请截图反馈：https://github.com/rexleimo/hermes-setup/issues
      echo.
      pause
      exit /b 1
    )
  )
)

rem ---- 安装/更新依赖（幂等，秒级） ----
echo [1/2] 准备依赖（首次约 1 分钟）...
uv sync --quiet
if errorlevel 1 (
  echo [失败] 依赖安装出错，最常见原因是网络无法访问 PyPI。
  echo    可设置国内镜像后重新双击本文件，在本窗口输入：
  echo    set UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
  echo    或把本窗口截图反馈：https://github.com/rexleimo/hermes-setup/issues
  pause
  exit /b 1
)

rem ---- 延迟 3 秒自动打开浏览器，然后前台启动服务 ----
if /i not "%HOST%"=="127.0.0.1" if /i not "%HOST%"=="localhost" (
  echo [警告] 监听地址为 %HOST%（非本机回环），管理后台将暴露给网络：
  echo   请务必设置 HERMES_CONSOLE_SECRET，并前置 HTTPS 且用 HERMES_CONSOLE_ALLOWED_IPS 限制来源。
)
echo [2/2] 启动控制台 http://%HOST%:%PORT% （本机/局域网/公网均可达，关窗即停）
rem 浏览器打开回环地址：部分新版浏览器打不开 0.0.0.0，127.0.0.1 恒可达
set BROWSE_HOST=%HOST%
if /i "%HOST%"=="0.0.0.0" set BROWSE_HOST=127.0.0.1
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://%BROWSE_HOST%:%PORT%"
uv run uvicorn app.main:app --host %HOST% --port %PORT%
