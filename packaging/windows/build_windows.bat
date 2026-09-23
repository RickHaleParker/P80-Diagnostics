@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem Copyright (C) 2026 Rick Hale Parker
rem
rem Build the Windows version of P80 Diagnostics (32-bit, so J2534 drivers work).
rem   1. dist\P80Diagnostics.exe                 one file, no Python needed
rem   2. dist\P80Diagnostics-<version>-Setup.exe installer (needs Inno Setup 6)
rem
rem Run from anywhere by double-clicking, or: packaging\windows\build_windows.bat
rem Needs 32-bit Python 3.11:  winget install Python.Python.3.11 --architecture x86
rem Installer step needs:      winget install JRSoftware.InnoSetup

setlocal
cd /d "%~dp0\..\.."
set "TOP=%CD%"
set "PY=py -3.11-32"

echo === Checking 32-bit Python ===
%PY% -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==4 else 1)"
if errorlevel 1 (
    echo 32-bit Python 3.11 not found. Install it with:
    echo     winget install Python.Python.3.11 --architecture x86
    goto :fail
)

for /f "delims=" %%v in ('%PY% -c "import p80diag; print(p80diag.__version__)"') do set "VER=%%v"
echo Version %VER%

echo === Installing build tools ===
%PY% -m pip install --upgrade pyinstaller pyserial
if errorlevel 1 goto :fail

echo === Building dist\P80Diagnostics.exe ===
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name P80Diagnostics ^
    --icon "%TOP%\p80diag\icon.ico" ^
    --add-data "%TOP%\p80diag\dtc_db.json;p80diag" ^
    --add-data "%TOP%\p80diag\fitment.json;p80diag" ^
    --add-data "%TOP%\p80diag\icon.png;p80diag" ^
    --distpath "%TOP%\dist" --workpath "%TOP%\build\pyinstaller" --specpath "%TOP%\build" ^
    "%TOP%\run_p80diag.py"
if errorlevel 1 goto :fail

echo === Building the installer ===
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo Inno Setup 6 not found, so no installer was made.
    echo The one-file program is ready: dist\P80Diagnostics.exe
    echo To make the installer too:  winget install JRSoftware.InnoSetup  then run this again.
    goto :done
)
"%ISCC%" /Q /DMyAppVersion=%VER% "%TOP%\packaging\windows\P80Diagnostics.iss"
if errorlevel 1 goto :fail
echo Installer ready: dist\P80Diagnostics-%VER%-Setup.exe

:done
echo.
echo === Done ===
pause
exit /b 0

:fail
echo.
echo === Build FAILED ===
pause
exit /b 1
