@echo off
title FAUST Backend TTS Service
echo FAUST Backend TTS Service Starting...
cd /d "%~dp0"

set "TTS_ROOT="
if defined FAUST_TTS_ROOT if exist "%FAUST_TTS_ROOT%\api.py" set "TTS_ROOT=%FAUST_TTS_ROOT%"

if not defined TTS_ROOT for /d %%D in ("%~dp0tts-hub\GPT-SoVITS-v5-*") do if not defined TTS_ROOT if exist "%%~fD\api.py" set "TTS_ROOT=%%~fD"
if not defined TTS_ROOT if exist "%~dp0tts-hub\GPT-SoVITS-Bundle\api.py" set "TTS_ROOT=%~dp0tts-hub\GPT-SoVITS-Bundle"
if not defined TTS_ROOT for /d %%D in ("%~dp0tts-hub\GPT-SoVITS-v2pro-*") do if not defined TTS_ROOT if exist "%%~fD\api.py" set "TTS_ROOT=%%~fD"
if not defined TTS_ROOT for /d %%D in ("%~dp0tts-hub\*") do if not defined TTS_ROOT for /d %%E in ("%%~fD\*") do if exist "%%~fE\api.py" set "TTS_ROOT=%%~fE"

if not defined TTS_ROOT (
	echo No supported GPT-SoVITS directory found under backend/tts-hub.
	echo Download the V5 package from the configurator Components page first.
	if not defined FAUST_TTS_ROOT pause
	exit /b 1
)

echo Using TTS root: %TTS_ROOT%
cd /d "%TTS_ROOT%"

if exist "%TTS_ROOT%\runtime\python.exe" (
	set "PATH=%TTS_ROOT%\runtime;%PATH%"
	set "TTS_PYTHON=%TTS_ROOT%\runtime\python.exe"
) else (
	echo runtime\python.exe not found in %TTS_ROOT%
	if not defined FAUST_TTS_ROOT pause
	exit /b 1
)

set "WEIGHT_ARGS="
if not exist "%TTS_ROOT%\GPT_SoVITS\module\models_v5.py" goto :legacy

set "V5_MODEL=%FAUST_TTS_V5_MODEL%"
if not defined V5_MODEL set "V5_MODEL=v5turbo"
set "V5_DIR=%TTS_ROOT%\GPT_SoVITS\pretrained_models\gsv-v5-pretrained"
set "V5_SOVITS="
if /i "%V5_MODEL%"=="v5dev" if exist "%V5_DIR%\s2Gv5dev.pth" set "V5_SOVITS=%V5_DIR%\s2Gv5dev.pth"
if not defined V5_SOVITS if exist "%V5_DIR%\s2Gv5turbo.pth" set "V5_SOVITS=%V5_DIR%\s2Gv5turbo.pth"
if defined V5_SOVITS goto :v5_gpt

echo V5 code detected but no V5 SoVITS weights found in "%V5_DIR%".
echo Expected s2Gv5turbo.pth or s2Gv5dev.pth. Re-download or upgrade V5 first.
if not defined FAUST_TTS_ROOT pause
exit /b 1

:v5_gpt
set "V5_GPT=%TTS_ROOT%\GPT_SoVITS\pretrained_models\s1v3.ckpt"
if not exist "%V5_GPT%" (
	echo V5 GPT weight not found: %V5_GPT%
	if not defined FAUST_TTS_ROOT pause
	exit /b 1
)
echo Using V5 model: %V5_MODEL%
set "WEIGHT_ARGS=-s "%V5_SOVITS%" -g "%V5_GPT%""
goto :run

:legacy
echo Legacy GPT-SoVITS package detected (no V5 weights); starting with package defaults.

:run
"%TTS_PYTHON%" api.py -p 5000 -d cuda -dr "%~dp0voices\neuro.wav" -dt "Hold on please, I'm busy. Okay, I think I heard him say he wants me to stream Hollow Knight on Tuesday and Thursday." -dl "en" %WEIGHT_ARGS% --bind_addr 127.0.0.1>"%~dp0log_tts.log" 2>&1
set "TTS_EXIT=%ERRORLEVEL%"
if not defined FAUST_TTS_ROOT pause
exit /b %TTS_EXIT%
