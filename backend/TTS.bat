@echo off
title FAUST Backend TTS Service
echo FAUST Backend TTS Service Starting...
cd /d "%~dp0"
set "TTS_ROOT="
if exist "%~dp0tts-hub\GPT-SoVITS-Bundle\api.py" set "TTS_ROOT=%~dp0tts-hub\GPT-SoVITS-Bundle"
if not defined TTS_ROOT if exist "%~dp0tts-hub\GPT-SoVITS-v2pro-20250604-nvidia50\api.py" set "TTS_ROOT=%~dp0tts-hub\GPT-SoVITS-v2pro-20250604-nvidia50"
if not defined TTS_ROOT if exist "%~dp0tts-hub\GPT-SoVITS-v2pro-20250604\api.py" set "TTS_ROOT=%~dp0tts-hub\GPT-SoVITS-v2pro-20250604"
if not defined TTS_ROOT if exist "%~dp0tts-hub\GPT-SoVITS-v2pro-20250604-nvidia50\GPT-SoVITS-v2pro-20250604-nvidia50\api.py" set "TTS_ROOT=%~dp0tts-hub\GPT-SoVITS-v2pro-20250604-nvidia50\GPT-SoVITS-v2pro-20250604-nvidia50"
if not defined TTS_ROOT if exist "%~dp0tts-hub\GPT-SoVITS-v2pro-20250604\GPT-SoVITS-v2pro-20250604\api.py" set "TTS_ROOT=%~dp0tts-hub\GPT-SoVITS-v2pro-20250604\GPT-SoVITS-v2pro-20250604"
if not defined TTS_ROOT (
	echo No supported GPT-SoVITS directory found under backend/tts-hub.
	echo Run backend\download_tts.py first.
	exit /b 1
)
echo Using TTS root: %TTS_ROOT%
cd /d "%TTS_ROOT%"
if exist "%TTS_ROOT%\runtime\python.exe" (
	set "PATH=%TTS_ROOT%\runtime;%PATH%"
	set "TTS_PYTHON=%TTS_ROOT%\runtime\python.exe"
) else (
	echo runtime\python.exe not found in %TTS_ROOT%
	pause
	exit /b 1
)
"%TTS_PYTHON%" api.py -p 5000 -d cuda -dr "%~dp0voices\neuro.wav" -dt "Hold on please, I'm busy. Okay, I think I heard him say he wants me to stream Hollow Knight on Tuesday and Thursday." -dl "en" --bind_addr 127.0.0.1>log_tts.log 2>&1
rem "%TTS_PYTHON%" api.py -p 5000 -d cuda -s SoVITS_weights_v2Pro\xxx_e8_s200.pth -dr "%~dp0voices\neuro.wav" -dt "Hold on please, I'm busy. Okay, I think I heard him say he wants me to stream Hollow Knight on Tuesday and Thursday." -dl "en"
pause