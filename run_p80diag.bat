@echo off
rem Start P80 Diagnostics with 32-bit Python (needed for J2534 drivers).
rem Double-click this file. Needs: winget install Python.Python.3.11 --architecture x86
rem                          and: py -3.11-32 -m pip install pyserial
start "" pyw -3.11-32 "%~dp0run_p80diag.py"
