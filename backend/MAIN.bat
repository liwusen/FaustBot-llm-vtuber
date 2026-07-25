@echo off
cd /d "%~dp0"
title FaustBot Backend Main Service
echo FaustBot Backend Main Service Starting...
call "%~dp0..\use-runtime.bat" || exit /b 1
echo Using root runtime: %FAUST_PYTHON%
"%FAUST_PYTHON%" "%~dp0..\embedded_python_bootstrap.py" "%~dp0main.py" --no-startup-chat --debug
pause