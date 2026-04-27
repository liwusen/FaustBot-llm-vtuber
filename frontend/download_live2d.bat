@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "REPO_ROOT=%~dp0.."
if not exist "%REPO_ROOT%\use-runtime.bat" set "REPO_ROOT=%~dp0..\..\.."
call "%REPO_ROOT%\use-runtime.bat" || exit /b 1
echo Using root runtime: %FAUST_PYTHON%
set "BOOTSTRAP_PATH=%REPO_ROOT%\embedded_python_bootstrap.py"
if not exist "%BOOTSTRAP_PATH%" set "BOOTSTRAP_PATH=%~dp0embedded_python_bootstrap.py"
"%FAUST_PYTHON%" "%BOOTSTRAP_PATH%" "%~dp0live2d_downloader.py"