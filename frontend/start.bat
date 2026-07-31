@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PACKAGED_EXE=%~dp0FaustLive2DFrontend.exe"
if exist "%PACKAGED_EXE%" (
	echo Packaged frontend detected: "%PACKAGED_EXE%"
	start "" "%PACKAGED_EXE%"
	exit /b 0
)

@REM set "FAUST_RUNTIME_ROOT=%~dp0..\.runtime"
@REM if exist "%FAUST_RUNTIME_ROOT%\python.exe" (
@REM 	echo Root runtime detected: "%FAUST_RUNTIME_ROOT%\python.exe"
@REM ) else (
@REM 	echo Root runtime not found yet. Run ..\setup-runtime.bat before starting services that need Python.
@REM )
npm start