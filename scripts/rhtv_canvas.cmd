@echo off
REM RHTV 无限画布启动器 — 用本机全局 Playwright 驱动 Edge
REM 先明确设置 RHTV_CANVAS_URL，再运行；不再使用历史默认画布。
setlocal
if not defined RHTV_CANVAS_URL (
  echo 请先设置准确目标：set "RHTV_CANVAS_URL=你的完整画布URL"
  echo 没有明确目标，已停止。
  exit /b 2
)
set "NODE_PATH=%APPDATA%\npm\node_modules"
where node >nul 2>nul
if errorlevel 1 (
  echo 未找到 node。请先安装 Node.js 后再试。
  pause
  exit /b 1
)
echo 正在启动 RHTV 画布专用浏览器... 登录一次，之后会记住。
echo 关闭本黑窗口 = 关闭那个浏览器画布窗口。
node "%~dp0rhtv_canvas.js"
echo.
echo 进程已结束。
pause
