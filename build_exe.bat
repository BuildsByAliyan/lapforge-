@echo off
setlocal
cd /d "%~dp0"
title LapForge - build LapForge.exe

echo.
echo  ==========================================
echo    LapForge - building LapForge.exe
echo  ==========================================
echo.

set "PY="
python -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=python"
if not defined PY (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY goto :nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :oldpython

echo [1/5] Installing the build requirements...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt "pyinstaller>=6.0"
if errorlevel 1 goto :fail

echo [2/5] Removing old build output...
call :cleanup
if exist dist rmdir /s /q dist

echo [3/5] Creating the launcher...
> _entry.py echo import sys
>> _entry.py echo from lapforge.app import run
>> _entry.py echo sys.exit(run())

echo [4/5] Running PyInstaller (this takes a minute)...
%PY% -m PyInstaller --noconfirm --clean --onefile --console ^
    --name LapForge ^
    --paths . ^
    --collect-submodules lapforge ^
    _entry.py
set "BUILD_RESULT=%errorlevel%"

echo [5/5] Removing temporary files...
call :cleanup
if not "%BUILD_RESULT%"=="0" goto :fail
if not exist "dist\LapForge.exe" goto :fail

echo.
echo  Build finished:  dist\LapForge.exe
echo.
echo  SHA-256 of the file (publish it next to the download so people can check it):
certutil -hashfile "dist\LapForge.exe" SHA256 | findstr /v /c:"hash of" /c:"CertUtil"
echo.
echo  Quick test:
"dist\LapForge.exe" --version
echo.
echo  Tip: double-click dist\LapForge.exe to open the menu.
echo.
pause
exit /b 0

:cleanup
if exist build rmdir /s /q build
if exist LapForge.spec del /q LapForge.spec
if exist lapforge.spec del /q lapforge.spec
if exist _entry.py del /q _entry.py
exit /b 0

:nopython
echo.
echo  [ERROR] Python was not found on this computer.
echo  What to do: install Python 3.10 or newer from https://www.python.org/downloads/
echo  and tick "Add python.exe to PATH" during the setup. Then run this file again.
echo.
pause
exit /b 1

:oldpython
echo.
echo  [ERROR] This Python is too old. LapForge needs Python 3.10 or newer.
echo  What to do: install the latest Python from https://www.python.org/downloads/
echo  and run this file again.
echo.
pause
exit /b 1

:fail
call :cleanup
echo.
echo  [ERROR] The build did not finish.
echo  What to do: scroll up and read the first error message, then run this file again.
echo.
pause
exit /b 1
