@echo off
rem midigen installer for Windows: double-click this file, or run  install.bat --audio  from a terminal.
cd /d "%~dp0"
for %%P in (py python python3) do (
  %%P -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && (
    %%P install.py %*
    goto :done
  )
)
echo midigen install: PROBLEM
echo   Python 3.10 or newer was not found.
echo   Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo   then run install.bat again.
:done
echo.
pause
