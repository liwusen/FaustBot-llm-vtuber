@echo off
echo FAUST Backend TTS Service Starting...
cd tts-hub\GPT-SoVITS-Bundle
set "PATH=%~dp0tts-hub\GPT-SoVITS-Bundle\runtime;%PATH%"
runtime\python.exe api.py -p 5000 -d cuda -s role_voice_api/neuro/merge.pth -dr role_voice_api/neuro/01.wav -dt "Hold on please, I'm busy. Okay, I think I heard him say he wants me to stream Hollow Knight on Tuesday and Thursday." -dl "en" --bind_addr 127.0.0.1 >log_tts.log 2>&1
rem runtime\python.exe api.py -p 5000 -d cuda -s SoVITS_weights_v2Pro\xxx_e8_s200.pth -dr role_voice_api/wendy/record.mp3 -dt "Hold on please, I'm busy. Okay, I think I heard him say he wants me to stream Hollow Knight on Tuesday and Thursday." -dl "en"
pause