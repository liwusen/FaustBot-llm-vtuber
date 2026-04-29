@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "PYTHON_VERSION=3.11.9"
set "PYTHON_EMBED_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
set "GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py"
set "SOURCE_MODE=cn"
set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "NPM_REGISTRY=https://registry.npmmirror.com/"
set "INSTALL_PYTHON=0"
set "INSTALL_TORCH=0"
set "INSTALL_PY_REQ=0"
set "INSTALL_LOCAL_INFER_REQ=0"
set "INSTALL_NODE=0"
set "INSTALL_TTS=0"
set "TORCH_VARIANT=cpu"
set "TTS_VARIANT=standard"
set "INFER_MODE=cloud"
set "SHOW_HELP=0"
set "MODE_PROVIDED=0"
set "SKIP_ADMIN_CHECK=0"

cd /d "%~dp0"
set "BATCH_PATH=%~f0"
set "RUNTIME_DIR=%CD%\.runtime"
set "PYTHON_EXE=%RUNTIME_DIR%\python.exe"
set "PTH_FILE=%RUNTIME_DIR%\python311._pth"
set "PIP_CMD=%PYTHON_EXE% -m pip"
set "FRONTEND_DIR=%CD%\frontend"
set "MC_OPERATOR_DIR=%CD%\backend\minecraft\mc-operator"
set "BACKEND_DIR=%CD%\backend"

echo -----------------------------------------
echo FaustBot 安装程序
echo 使用命令行参数安装 Python、PyTorch、Python 依赖、Node.js 依赖和 TTS 模型。
echo -----------------------------------------

if /i "%GITHUB_ACTIONS%"=="true" set "SKIP_ADMIN_CHECK=1"

if not "%~1"=="" goto parse_args_loop
goto after_parse

:parse_args_loop
if "%~1"=="" goto after_parse
if /i "%~1"=="--help" (
  set "SHOW_HELP=1"
  shift
  goto parse_args_loop
)
if /i "%~1"=="--mode" (
  if "%~2"=="" (
    echo 参数错误：--mode 缺少取值
    exit /b 1
  )
  if /i "%~2"=="local" (
    set "INFER_MODE=local"
    set "MODE_PROVIDED=1"
  ) else if /i "%~2"=="cloud" (
    set "INFER_MODE=cloud"
    set "MODE_PROVIDED=1"
  ) else (
    echo 参数错误：--mode 仅支持 local 或 cloud
    exit /b 1
  )
  shift
  shift
  goto parse_args_loop
)
if /i "%~1"=="--source" (
  if "%~2"=="" (
    echo 参数错误：--source 缺少取值
    exit /b 1
  )
  if /i "%~2"=="cn" (
    set "SOURCE_MODE=cn"
    set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
    set "NPM_REGISTRY=https://registry.npmmirror.com/"
  ) else if /i "%~2"=="official" (
    set "SOURCE_MODE=official"
    set "PIP_INDEX_URL=https://pypi.org/simple"
    set "NPM_REGISTRY=https://registry.npmjs.org/"
  ) else (
    echo 参数错误：--source 仅支持 cn 或 official
    exit /b 1
  )
  shift
  shift
  goto parse_args_loop
)
if /i "%~1"=="--install-python" (
  if "%~2"=="" (
    echo 参数错误：--install-python 缺少取值
    exit /b 1
  )
  if /i "%~2"=="yes" (
    set "INSTALL_PYTHON=1"
  ) else if /i "%~2"=="no" (
    set "INSTALL_PYTHON=0"
  ) else (
    echo 参数错误：--install-python 仅支持 yes 或 no
    exit /b 1
  )
  shift
  shift
  goto parse_args_loop
)
if /i "%~1"=="--install-node" (
  if "%~2"=="" (
    echo 参数错误：--install-node 缺少取值
    exit /b 1
  )
  if /i "%~2"=="yes" (
    set "INSTALL_NODE=1"
  ) else if /i "%~2"=="no" (
    set "INSTALL_NODE=0"
  ) else (
    echo 参数错误：--install-node 仅支持 yes 或 no
    exit /b 1
  )
  shift
  shift
  goto parse_args_loop
)
if /i "%~1"=="--tts-variant" (
  if "%~2"=="" (
    echo 参数错误：--tts-variant 缺少取值
    exit /b 1
  )
  if /i "%~2"=="standard" (
    set "TTS_VARIANT=standard"
  ) else if /i "%~2"=="nvidia50" (
    set "TTS_VARIANT=nvidia50"
  ) else (
    echo 参数错误：--tts-variant 仅支持 standard 或 nvidia50
    exit /b 1
  )
  shift
  shift
  goto parse_args_loop
)
echo 参数错误：不支持 %~1
exit /b 1

:after_parse
if "%SHOW_HELP%"=="1" goto show_help
if "%MODE_PROVIDED%"=="0" (
  echo 参数错误：必须提供 --mode local 或 --mode cloud
  echo.
  goto show_help_with_error
)

if /i "%INFER_MODE%"=="local" (
  set "TORCH_VARIANT=gpu"
  set "INSTALL_TORCH=1"
  set "INSTALL_PY_REQ=1"
  set "INSTALL_LOCAL_INFER_REQ=1"
  set "INSTALL_TTS=1"
) else (
  set "TORCH_VARIANT=cpu"
  set "INSTALL_TORCH=1"
  set "INSTALL_PY_REQ=1"
  set "INSTALL_LOCAL_INFER_REQ=0"
  set "INSTALL_TTS=0"
)

if "%SKIP_ADMIN_CHECK%"=="0" (
  net session >nul 2>&1
  if errorlevel 1 (
    echo 需要管理员权限，正在重新启动...
    if "%INSTALL_PYTHON%"=="1" (
      set "INSTALL_PYTHON_TEXT=yes"
    ) else (
      set "INSTALL_PYTHON_TEXT=no"
    )
    if "%INSTALL_NODE%"=="1" (
      set "INSTALL_NODE_TEXT=yes"
    ) else (
      set "INSTALL_NODE_TEXT=no"
    )
    set "FAUST_SETUP_ARGS=--mode %INFER_MODE% --source %SOURCE_MODE% --install-python !INSTALL_PYTHON_TEXT! --install-node !INSTALL_NODE_TEXT!"
    if /i "%INFER_MODE%"=="local" set "FAUST_SETUP_ARGS=!FAUST_SETUP_ARGS! --tts-variant %TTS_VARIANT%"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%BATCH_PATH%' -Verb RunAs -ArgumentList $env:FAUST_SETUP_ARGS"
    exit /b 0
  )
) else (
  echo 检测到 GitHub Actions，跳过管理员权限检查。
)

if "%INSTALL_PYTHON%"=="1" (
  set "INSTALL_PYTHON_TEXT=yes"
) else (
  set "INSTALL_PYTHON_TEXT=no"
)
if "%INSTALL_TORCH%"=="1" (
  set "INSTALL_TORCH_TEXT=yes"
) else (
  set "INSTALL_TORCH_TEXT=no"
)
if "%INSTALL_PY_REQ%"=="1" (
  set "INSTALL_PY_REQ_TEXT=yes"
) else (
  set "INSTALL_PY_REQ_TEXT=no"
)
if "%INSTALL_LOCAL_INFER_REQ%"=="1" (
  set "INSTALL_LOCAL_INFER_REQ_TEXT=yes"
) else (
  set "INSTALL_LOCAL_INFER_REQ_TEXT=no"
)
if "%INSTALL_NODE%"=="1" (
  set "INSTALL_NODE_TEXT=yes"
) else (
  set "INSTALL_NODE_TEXT=no"
)
if "%INSTALL_TTS%"=="1" (
  set "INSTALL_TTS_TEXT=yes"
) else (
  set "INSTALL_TTS_TEXT=no"
)

echo 模式：%INFER_MODE%
echo 源：%SOURCE_MODE%
echo 安装 Python 基础环境：%INSTALL_PYTHON_TEXT%
echo 安装 PyTorch：%INSTALL_TORCH_TEXT% (%TORCH_VARIANT%)
echo 安装 Python requirements：%INSTALL_PY_REQ_TEXT%
echo 安装本地推理专用依赖：%INSTALL_LOCAL_INFER_REQ_TEXT%
echo 安装 Node.js 依赖：%INSTALL_NODE_TEXT%
echo 下载 TTS：%INSTALL_TTS_TEXT% (%TTS_VARIANT%)
echo.
echo 已确认，开始执行。

if "%INSTALL_PYTHON%"=="1" (
  echo.
  echo [1/5] 安装 Python 环境
  if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"
  echo 下载 Python %PYTHON_VERSION%...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_EMBED_URL%' -OutFile '%RUNTIME_DIR%\python-embed.zip'"
  if errorlevel 1 goto fail
  echo 解压 Python...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%RUNTIME_DIR%\python-embed.zip' -DestinationPath '%RUNTIME_DIR%' -Force"
  if errorlevel 1 goto fail
  if not exist "%PYTHON_EXE%" (
    echo 未找到 .runtime\python.exe
    goto fail
  )
  echo 配置 site-packages...
  if exist "%PTH_FILE%" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$pth = Get-Content '%PTH_FILE%'; $pth = $pth | Where-Object { $_ -notmatch '^#import site$' -and $_ -ne 'Lib\\site-packages' -and $_ -ne 'import site' }; if ($pth -notcontains '.') { $pth += '.' }; $pth += 'Lib\\site-packages'; $pth += 'import site'; Set-Content -Path '%PTH_FILE%' -Value $pth -Encoding ASCII"
    if errorlevel 1 goto fail
  )
  echo 下载 get-pip.py...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%GET_PIP_URL%' -OutFile '%RUNTIME_DIR%\get-pip.py'"
  if errorlevel 1 goto fail
  echo 安装 pip...
  "%PYTHON_EXE%" "%RUNTIME_DIR%\get-pip.py"
  if errorlevel 1 goto fail
  echo 升级 pip、setuptools、wheel...
  %PIP_CMD% install --upgrade pip setuptools wheel -i %PIP_INDEX_URL%
  if errorlevel 1 goto fail
)

if "%INSTALL_TORCH%"=="1" (
  echo.
  echo [2/5] 安装 PyTorch
  "%PYTHON_EXE%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo .runtime 中没有可用的 pip，请先安装 Python 基础环境。
    goto fail
  )
  if /i "%TORCH_VARIANT%"=="gpu" (
    echo 安装 PyTorch GPU 版...
    %PIP_CMD% install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 --extra-index-url %PIP_INDEX_URL%
  ) else (
    echo 安装 PyTorch CPU 版...
    %PIP_CMD% install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu --extra-index-url %PIP_INDEX_URL%
  )
  if errorlevel 1 goto fail
)

if "%INSTALL_PY_REQ%"=="1" (
  echo.
  echo [3/5] 安装 Python 依赖
  "%PYTHON_EXE%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo .runtime 中没有可用的 pip，请先安装 Python 基础环境。
    goto fail
  )
  if "%INSTALL_LOCAL_INFER_REQ%"=="1" (
    if not exist "%CD%\requirements_local_infer.txt" (
      echo 未找到 requirements_local_infer.txt
      goto fail
    )
    echo 安装本地推理专用依赖...
    %PIP_CMD% install -r "%CD%\requirements_local_infer.txt" -i %PIP_INDEX_URL%
    if errorlevel 1 goto fail
  )
  %PIP_CMD% install -r "%CD%\requirements.txt" -i %PIP_INDEX_URL%
  if errorlevel 1 goto fail
)

if "%INSTALL_NODE%"=="1" (
  echo.
  echo [4/5] 安装 Node.js 依赖
  if not exist "%FRONTEND_DIR%\package.json" (
    echo 未找到 frontend\package.json
    goto fail
  )
  if not exist "%MC_OPERATOR_DIR%\package.json" (
    echo 未找到 backend\minecraft\mc-operator\package.json
    goto fail
  )
  pushd "%FRONTEND_DIR%"
  npm install --registry=%NPM_REGISTRY%
  if errorlevel 1 (
    popd
    goto fail
  )
  popd
  pushd "%MC_OPERATOR_DIR%"
  npm install --registry=%NPM_REGISTRY%
  if errorlevel 1 (
    popd
    goto fail
  )
  popd
)

if "%INSTALL_TTS%"=="1" (
  echo.
  echo [5/5] 下载 TTS 模型 (本地文字转语音需要)
  if not exist "%BACKEND_DIR%\download_tts.py" (
    echo 未找到 backend\download_tts.py
    goto fail
  )
  "%PYTHON_EXE%" "%CD%\embedded_python_bootstrap.py" "%BACKEND_DIR%\download_tts.py" --gpu-variant %TTS_VARIANT%
  if errorlevel 1 goto fail
)

echo.
echo 安装完成。
echo 可验证："%PYTHON_EXE%" -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
exit /b 0

:show_help_with_error
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$lines = @('用法：setup-runtime.bat --mode local|cloud [--source cn|official] [--install-python yes|no] [--install-node yes|no] [--tts-variant standard|nvidia50]','', '参数说明：','参数 --mode: 推理模式。local=本地推理，cloud=云端推理。','参数 --source: 依赖源。cn=国内镜像，official=官方源。','参数 --install-python: 是否安装 Python + pip + wheel 等基础环境。','参数 --install-node: 是否安装 frontend 和 mc-operator 的 Node.js 依赖。','参数 --tts-variant: TTS 包类型。standard=普通显卡，nvidia50=50 系显卡。','', '模式默认行为：','local: 安装 GPU 版 PyTorch、requirements_local_infer.txt、requirements.txt、TTS。','cloud: 安装 CPU 版 PyTorch、requirements.txt。','', '示例：','setup-runtime.bat --mode local --source cn --install-python yes --install-node yes --tts-variant nvidia50','setup-runtime.bat --mode cloud --source official --install-python yes --install-node yes'); [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $lines | ForEach-Object { Write-Host $_ }"
exit /b 1

:show_help
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$lines = @('用法：setup-runtime.bat --mode local|cloud [--source cn|official] [--install-python yes|no] [--install-node yes|no] [--tts-variant standard|nvidia50]','', '参数说明：','参数 --mode: 推理模式。local=本地推理，cloud=云端推理。','参数 --source: 依赖源。cn=国内镜像，official=官方源。','参数 --install-python: 是否安装 Python + pip + wheel 等基础环境。','参数 --install-node: 是否安装 frontend 和 mc-operator 的 Node.js 依赖。','参数 --tts-variant: TTS 包类型。standard=普通显卡，nvidia50=50 系显卡。','', '模式默认行为：','local: 安装 GPU 版 PyTorch、requirements_local_infer.txt、requirements.txt、TTS。','cloud: 安装 CPU 版 PyTorch、requirements.txt。','', '示例：','setup-runtime.bat --mode local --source cn --install-python yes --install-node yes --tts-variant nvidia50','setup-runtime.bat --mode cloud --source official --install-python yes --install-node yes'); [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $lines | ForEach-Object { Write-Host $_ }"
exit /b 0

:fail
echo.
echo 安装失败，请根据上面的提示处理后重试。
pause
exit /b 1
