@echo off
chcp 65001 >nul
cd /d %~dp0

echo.
echo ================================================
echo   Tactile Compiler P0 - LAN Renderer
echo ================================================
echo.
echo 1. This window will start the local web server.
echo 2. Keep this window open.
echo 3. On this PC open: http://127.0.0.1:8765
echo 4. On the RDK screen open: http://THIS_PC_IP:8765
echo.
echo Current IPv4 addresses:
ipconfig | findstr /i "IPv4"
echo.
echo Starting server on 0.0.0.0:8765 ...
echo.

python app\main.py --demo synthetic --host 0.0.0.0 --port 8765
pause
