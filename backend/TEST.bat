@echo off
cd /d "%~dp0"
title Running Unit Tests
call "%~dp0..\use-runtime.bat" || exit /b 1
echo Using root runtime: %FAUST_PYTHON%
"%FAUST_PYTHON%" -m pytest "%~dp0tests"
pause