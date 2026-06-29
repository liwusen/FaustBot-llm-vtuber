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
npm start