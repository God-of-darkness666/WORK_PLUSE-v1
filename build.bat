@echo off
REM ============================================================
REM  Build both EXE files with PyInstaller
REM  - HRAgent.exe : silent employee-side monitor (no console)
REM  - HRAdmin.exe : HR command GUI (console)
REM  Output goes to the  dist\  folder next to this file.
REM ============================================================
cd /d "%~dp0"

echo [1/4] Installing dependencies...
python -m pip install -r requirements.txt pyinstaller -q

echo [2/4] Cleaning old build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist agent\__pycache__ rmdir /s /q agent\__pycache__
if exist admin\__pycache__ rmdir /s /q admin\__pycache__

echo [3/4] Building HRAgent.exe (silent agent)...
python -m PyInstaller --onefile --noconsole --name HRAgent agent\agent.py

echo [4/4] Building HRAdmin.exe (admin command GUI)...
python -m PyInstaller --onefile --console --name HRAdmin admin\admin_cli.py

echo.
echo ================= SUCCESS =================
echo  dist\HRAgent.exe   - give this to employees (after editing its config.json)
echo  dist\HRAdmin.exe   - run this on the HR/admin machine
echo ===========================================
pause
exit /b 0

:error
echo BUILD FAILED - see the messages above.
pause
exit /b 1
