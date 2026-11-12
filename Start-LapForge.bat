@echo off
setlocal
cd /d "%~dp0"
title LapForge

rem Find Python (3.10 or newer).
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

rem First start only: install the two helper packages LapForge uses.
%PY% -c "import psutil, rich" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  Setting up LapForge for the first time.
    echo  This takes about a minute and needs an internet connection.
    echo.
    %PY% -m pip install --quiet --disable-pip-version-check "psutil>=5.9" "rich>=13.0"
    if errorlevel 1 goto :setupfailed
)

%PY% -m lapforge %*
set "LAPFORGE_EXIT=%errorlevel%"

rem Keep the window open only when it was opened by a double-click.
echo %cmdcmdline% | find /i "%~nx0" >nul
if not errorlevel 1 (
    echo.
    pause
)
exit /b %LAPFORGE_EXIT%

:nopython
echo.
echo  Python was not found on this computer.
echo.
echo  What to do:
echo    1. Download Python from https://www.python.org/downloads/
echo    2. During the setup, tick "Add python.exe to PATH".
echo    3. Double-click Start-LapForge.bat again.
echo.
pause
exit /b 1

:oldpython
echo.
echo  The Python on this computer is too old. LapForge needs Python 3.10 or newer.
echo.
echo  What to do:
echo    1. Download the latest Python from https://www.python.org/downloads/
echo    2. Install it, then double-click Start-LapForge.bat again.
echo.
pause
exit /b 1

:setupfailed
echo.
echo  LapForge could not install what it needs.
echo.
echo  What to do:
echo    1. Check that this computer is connected to the internet.
echo    2. Double-click Start-LapForge.bat again.
echo    3. If it still fails, open a terminal here and run:
echo       python -m pip install "psutil>=5.9" "rich>=13.0"
echo.
pause
exit /b 1
