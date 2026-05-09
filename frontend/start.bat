@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PACKAGED_EXE=%~dp0FaustLive2DFrontend.exe"
if not exist "%PACKAGED_EXE%" set "PACKAGED_EXE=%~dp0dist\win-unpacked\FaustLive2DFrontend.exe"
if exist "%PACKAGED_EXE%" (
	echo Packaged frontend detected: "%PACKAGED_EXE%"
	start "" "%PACKAGED_EXE%"
	exit /b 0
)

set "FAUST_RUNTIME_ROOT=%~dp0..\.runtime"
if exist "%FAUST_RUNTIME_ROOT%\python.exe" (
	echo Root runtime detected: "%FAUST_RUNTIME_ROOT%\python.exe"
) else (
	echo Root runtime not found yet. Run ..\setup-runtime.bat before starting services that need Python.
)
npm start