@echo off
setlocal EnableDelayedExpansion
if not "%~1"=="" goto args
set /p LINK=Paste a Mobalytics / pobb.in link or PoB code: 
python "%~dp0pob_tester.py" --skills "!LINK!"
pause
goto :eof
:args
python "%~dp0pob_tester.py" %*
