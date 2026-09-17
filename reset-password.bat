@echo off
rem ============================================================
rem  忘记管理员密码？双击本文件按提示重置（Windows）
rem  macOS / Linux 用户请在终端执行：bash scripts/reset_password.sh
rem ============================================================
setlocal
cd /d "%~dp0.."
chcp 65001 >nul
echo === Hermes Console 管理员密码重置 ===
echo （重置后该账号的所有登录会话都会下线）
echo.
set USERNAME=
set /p USERNAME=要重置的用户名（直接回车默认 admin）：
if "%USERNAME%"=="" set USERNAME=admin
where uv >nul 2>nul
if errorlevel 1 (
  echo [提示] 未检测到 uv。请先双击 start.bat 一次（它会自动安装 uv），
  echo        再回来双击本文件。
  pause
  exit /b 1
)
uv run python scripts/console_admin.py reset-password %USERNAME%
echo.
echo 用新密码回到浏览器登录即可。
pause
