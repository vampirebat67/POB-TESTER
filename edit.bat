@echo off
setlocal EnableDelayedExpansion

rem Find a working 64-bit Python: python, py launcher, then any uv-installed Python
set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  for /d %%D in ("%APPDATA%\uv\python\cpython-3.*-windows-x86_64*") do (
    if exist "%%D\python.exe" set "PY="%%D\python.exe""
  )
)
if not defined PY (
  echo Could not find Python. Install 64-bit Python 3 from python.org and try again.
  pause
  exit /b 1
)

rem Opens the build editor in your browser. Optionally pass a link or PoB code.
%PY% "%~dp0pob_tester.py" --edit %*
if errorlevel 1 pause
