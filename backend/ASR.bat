@echo off
title FAUST Backend ASR Service
cd /d "%~dp0"
echo FAUST Backend ASR Service Starting...
call "%~dp0..\use-runtime.bat" || exit /b 1
echo Using root runtime: %FAUST_PYTHON%
"%FAUST_PYTHON%" "%~dp0..\embedded_python_bootstrap.py" "%~dp0asr_api_v2.py" >log_asr.log 2>&1
echo Running ASR service...