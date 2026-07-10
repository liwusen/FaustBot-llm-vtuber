@echo off
setlocal EnableExtensions

set "FAUST_RUNTIME_ROOT=%~dp0.runtime"
set "FAUST_PYTHON=%FAUST_RUNTIME_ROOT%\python.exe"
set "FAUST_NODEJS_ROOT=%~dp0.nodejs"
set "FAUST_NODEJS=%FAUST_NODEJS_ROOT%\node.exe"

if exist "%FAUST_PYTHON%" (
  endlocal & (
    set "FAUST_RUNTIME_ROOT=%~dp0.runtime"
    set "FAUST_PYTHON=%~dp0.runtime\python.exe"
    set "FAUST_NODEJS_ROOT=%~dp0.nodejs"
    set "FAUST_NODEJS=%~dp0.nodejs\node.exe"
  )
  exit /b 0
)

echo Root runtime not found: "%FAUST_PYTHON%"
echo Run setup-runtime.bat from repository root first.
endlocal
exit /b 1