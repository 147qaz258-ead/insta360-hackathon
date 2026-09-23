@echo off
cd /d %~dp0
python app\main.py --demo synthetic --host 0.0.0.0 --port 8765
pause
