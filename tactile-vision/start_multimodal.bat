@echo off
chcp 65001 >nul
cd /d %~dp0

echo [Tactile Compiler] Starting multimodal product runtime...
if not defined DASHSCOPE_API_KEY if not exist config.env (
  echo.
  echo [WARNING] DASHSCOPE_API_KEY is not set.
  echo The UI will open, but multimodal calls will remain disabled.
  echo Copy config.example.env to config.env, fill it locally, then restart.
  echo.
)

start "Tactile Compiler Server" cmd /k python app\main.py --host 0.0.0.0 --port 8765

timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8765/

echo.
echo Product console: http://127.0.0.1:8765/
echo RDK display:     http://127.0.0.1:8765/display.html
echo.
echo Keep the server window open.
