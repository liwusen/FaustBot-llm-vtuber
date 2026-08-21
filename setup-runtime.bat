@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "PYTHON_VERSION=3.11.9"
set "PYTHON_EMBED_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
set "GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py"
set "SOURCE_MODE=cn"
set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "NPM_REGISTRY=https://registry.npmmirror.com/"
set "INSTALL_PYTHON="
set "INSTALL_TORCH=0"
set "INSTALL_PY_REQ="
set "INSTALL_SYS_NODE="
set "INSTALL_BUNDLE_NODE="
set "INSTALL_TTS="
set "TORCH_VARIANT=cpu"
set "TTS_VARIANT=standard"
set "SHOW_HELP=0"
set "SKIP_ADMIN_CHECK=0"
set "NODEJS_VERSION=20.18.3"
set "NODEJS_URL=https://nodejs.org/dist/v%NODEJS_VERSION%/node-v%NODEJS_VERSION%-win-x64.zip"
set "NPM_BUNDLE_VERSION=10.8.2"

cd /d "%~dp0"
set "BATCH_PATH=%~f0"
set "RUNTIME_DIR=%CD%\.runtime"
set "PYTHON_EXE=%RUNTIME_DIR%\python.exe"
set "PTH_FILE=%RUNTIME_DIR%\python311._pth"
set "NODEJS_DIR=%CD%\.nodejs"
set "NODEJS_EXE=%NODEJS_DIR%\node.exe"
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
if /i "%~1"=="--fix-py-requirements" (
  set "INSTALL_FIX_PY_REQ=1"
  shift
  goto parse_args_loop
)
if /i "%~1"=="--skip-admin-check" (
  set "SKIP_ADMIN_CHECK=1"
  shift
  goto parse_args_loop
)
if /i "%~1"=="--help" (
  set "SHOW_HELP=1"
  shift
  goto parse_args_loop
)
if /i "%~1"=="--torch" (
  if "%~2"=="" (
    echo 参数错误：--torch 缺少取值
    exit /b 1
  )
  if /i "%~2"=="cu128" (
    set "TORCH_VARIANT=cu128"
    set "INSTALL_TORCH=1"
  ) else if /i "%~2"=="cu121" (
    set "TORCH_VARIANT=cu121"
    set "INSTALL_TORCH=1"
  ) else if /i "%~2"=="cu130" (
    set "TORCH_VARIANT=cu130"
    set "INSTALL_TORCH=1"
  ) else if /i "%~2"=="cpu" (
    set "TORCH_VARIANT=cpu"
    set "INSTALL_TORCH=1"
  ) else (
    echo 参数错误：--torch 仅支持 cu128/cu121/cu130/cpu
    exit /b 1
  )
  shift
  shift
  goto parse_args_loop
)
if /i "%~1"=="--tts" (
  if "%~2"=="" (
    echo 参数错误：--tts 缺少取值
    exit /b 1
  )
  if /i "%~2"=="yes" (
    set "INSTALL_TTS=1"
  ) else if /i "%~2"=="no" (
    set "INSTALL_TTS=0"
  ) else (
    echo 参数错误：--tts 仅支持 yes 或 no
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
    set "INSTALL_SYS_NODE=1"
  ) else if /i "%~2"=="no" (
    set "INSTALL_SYS_NODE=0"
  ) else (
    echo 参数错误：--install-node 仅支持 yes 或 no
    exit /b 1
  )
  shift
  shift
  goto parse_args_loop
)
if /i "%~1"=="--install-bundle-node" (
  if "%~2"=="" (
    echo 参数错误：--install-bundle-node 缺少取值
    exit /b 1
  )
  if /i "%~2"=="yes" (
    set "INSTALL_BUNDLE_NODE=1"
  ) else if /i "%~2"=="no" (
    set "INSTALL_BUNDLE_NODE=0"
  ) else (
    echo 参数错误：--install-bundle-node 仅支持 yes 或 no
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
if "%INSTALL_PY_REQ%"=="" set "INSTALL_PY_REQ=0"
if "%INSTALL_PYTHON%"=="" set "INSTALL_PYTHON=0"
if "%INSTALL_SYS_NODE%"=="" set "INSTALL_SYS_NODE=0"
if "%INSTALL_BUNDLE_NODE%"=="" set "INSTALL_BUNDLE_NODE=0"
if "%INSTALL_TTS%"=="" set "INSTALL_TTS=0"
if "%INSTALL_TORCH%"=="1" if "%INSTALL_PY_REQ%"=="0" set "INSTALL_PY_REQ=1"
if "%INSTALL_FIX_PY_REQ%"=="1" (
  set "INSTALL_TORCH=0"
  set "INSTALL_PY_REQ=1"
  set "INSTALL_PYTHON=0"
  set "INSTALL_SYS_NODE=0"
  set "INSTALL_BUNDLE_NODE=0"
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
    if "%INSTALL_SYS_NODE%"=="1" (
      set "INSTALL_SYS_NODE_TEXT=yes"
    ) else (
      set "INSTALL_SYS_NODE_TEXT=no"
    )
    if "%INSTALL_BUNDLE_NODE%"=="1" (
      set "INSTALL_BUNDLE_NODE_TEXT=yes"
    ) else (
      set "INSTALL_BUNDLE_NODE_TEXT=no"
    )
    if "%INSTALL_TTS%"=="1" (
      set "INSTALL_TTS_TEXT=yes"
    ) else (
      set "INSTALL_TTS_TEXT=no"
    )
    set "FAUST_SETUP_ARGS=--torch %TORCH_VARIANT% --source %SOURCE_MODE% --install-python !INSTALL_PYTHON_TEXT! --install-node !INSTALL_SYS_NODE_TEXT! --install-bundle-node !INSTALL_BUNDLE_NODE_TEXT! --tts !INSTALL_TTS_TEXT!"
    if /i "%INSTALL_TTS%"=="1" set "FAUST_SETUP_ARGS=!FAUST_SETUP_ARGS! --tts-variant %TTS_VARIANT%"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%BATCH_PATH%' -Verb RunAs -ArgumentList $env:FAUST_SETUP_ARGS"
    exit /b 0
  )
) else (
  echo 跳过管理员权限检查。
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
if "%INSTALL_SYS_NODE%"=="1" (
  set "INSTALL_SYS_NODE_TEXT=yes"
) else (
  set "INSTALL_SYS_NODE_TEXT=no"
)
if "%INSTALL_BUNDLE_NODE%"=="1" (
  set "INSTALL_BUNDLE_NODE_TEXT=yes"
) else (
  set "INSTALL_BUNDLE_NODE_TEXT=no"
)
if "%INSTALL_TTS%"=="1" (
  set "INSTALL_TTS_TEXT=yes"
) else (
  set "INSTALL_TTS_TEXT=no"
)

echo Torch 版本：%TORCH_VARIANT%
echo 源：%SOURCE_MODE%
echo 安装 Python 基础环境：%INSTALL_PYTHON_TEXT%
echo 安装 PyTorch：%INSTALL_TORCH_TEXT% (%TORCH_VARIANT%)
echo 安装 Python requirements：%INSTALL_PY_REQ_TEXT%
echo 安装 Node.js 依赖（frontend/mc-operator）：%INSTALL_SYS_NODE_TEXT%
echo 安装 Bundled Node.js 运行时（MCP）：%INSTALL_BUNDLE_NODE_TEXT%
echo 下载 TTS：%INSTALL_TTS_TEXT% (%TTS_VARIANT%)
echo.
echo 已确认，开始执行。

if "%INSTALL_PYTHON%"=="1" (
  echo.
  echo [1/6] 安装 Python 环境
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
  echo [2/6] 安装 PyTorch
  "%PYTHON_EXE%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo .runtime 中没有可用的 pip，请先安装 Python 基础环境。
    goto fail
  )
  if "%SOURCE_MODE%"=="cn" (
    set "TORCH_INDEX_FLAG=-f https://mirrors.aliyun.com/pytorch-wheels/%TORCH_VARIANT%/"
    echo 安装 PyTorch %TORCH_VARIANT% 版（%SOURCE_MODE% 源）...
    %PIP_CMD% install torch torchvision torchaudio !TORCH_INDEX_FLAG! --no-index --user
  ) else (
    set "TORCH_INDEX_FLAG=--index-url https://download.pytorch.org/whl/%TORCH_VARIANT%"
    echo 安装 PyTorch %TORCH_VARIANT% 版（%SOURCE_MODE% 源）...
    %PIP_CMD% install torch torchvision torchaudio !TORCH_INDEX_FLAG! --user
  )
  
  if errorlevel 1 goto fail
)

if "%INSTALL_PY_REQ%"=="1" (
  echo.
  echo [3/6] 安装 Python 依赖
  "%PYTHON_EXE%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo .runtime 中没有可用的 pip，请先安装 Python 基础环境。
    goto fail
  )
  %PIP_CMD% install -r "%CD%\requirements.txt" -i %PIP_INDEX_URL%
  if errorlevel 1 goto fail
)

if "%INSTALL_SYS_NODE%"=="1" (
  echo.
  echo [4/6] 安装 Node.js 依赖（frontend/mc-operator）
  if not exist "%FRONTEND_DIR%\package.json" (
    echo 未找到 frontend\package.json
    goto fail
  )
  if not exist "%MC_OPERATOR_DIR%\package.json" (
    echo 未找到 backend\minecraft\mc-operator\package.json
    goto fail
  )
  pushd "%FRONTEND_DIR%"
  call npm install --registry=%NPM_REGISTRY%
  if errorlevel 1 (
    popd
    goto fail
  )
  popd
  pushd "%MC_OPERATOR_DIR%"
  call npm install --registry=%NPM_REGISTRY%
  if errorlevel 1 (
    popd
    goto fail
  )
  popd
)
if "%INSTALL_BUNDLE_NODE%"=="1" (
  echo.
  echo [5/6] 安装 Node.js 便携版 + MCP server
  if not exist "%NODEJS_DIR%" mkdir "%NODEJS_DIR%"
  if not exist "%NODEJS_EXE%" (
    echo 下载 Node.js %NODEJS_VERSION%...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%NODEJS_URL%' -OutFile '%NODEJS_DIR%\node.zip'"
    if errorlevel 1 goto fail
    echo 解压 Node.js...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%NODEJS_DIR%\node.zip' -DestinationPath '%NODEJS_DIR%' -Force"
    if errorlevel 1 goto fail
    for /d %%i in ("%NODEJS_DIR%\node-v*-win-x64") do (
      move "%%i\*" "%NODEJS_DIR%\" >nul 2>&1
      rmdir "%%i" /s /q
    )
    del "%NODEJS_DIR%\node.zip"
    if not exist "%NODEJS_EXE%" (
      echo 未找到 .nodejs\node.exe
      goto fail
    )
    pushd "%NODEJS_DIR%"
    if not exist package.json (
      "%NODEJS_EXE%" -e "require('fs').writeFileSync('package.json','{\"name\":\"faustbot-mcp\",\"private\":true}')"
      if errorlevel 1 (
        popd
        goto fail
      )
    )
  )

  if not exist "%NODEJS_DIR%\node_modules\npm\bin\npm-cli.js" (
    echo bundled Node.js 未携带 npm，开始补齐 npm %NPM_BUNDLE_VERSION%...
    if not exist "%NODEJS_DIR%\node_modules" mkdir "%NODEJS_DIR%\node_modules"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%NPM_REGISTRY%npm/-/npm-%NPM_BUNDLE_VERSION%.tgz' -OutFile '%NODEJS_DIR%\npm.tgz'"
    if errorlevel 1 (
      popd
      goto fail
    )
    tar -xzf "%NODEJS_DIR%\npm.tgz" -C "%NODEJS_DIR%\node_modules"
    if errorlevel 1 (
      del "%NODEJS_DIR%\npm.tgz" >nul 2>&1
      popd
      goto fail
    )
    if exist "%NODEJS_DIR%\node_modules\npm" rmdir /s /q "%NODEJS_DIR%\node_modules\npm"
    if exist "%NODEJS_DIR%\node_modules\package" ren "%NODEJS_DIR%\node_modules\package" npm
    del "%NODEJS_DIR%\npm.tgz" >nul 2>&1
  )
  call "%NODEJS_DIR%\npm.cmd" --version >nul 2>&1
  if errorlevel 1 (
    echo bundled npm 初始化失败
    popd
    goto fail
  )
  echo 安装 MCP server 依赖...
  call "%NODEJS_DIR%\npm.cmd" install @playwright/mcp playwright --registry=%NPM_REGISTRY% --no-save --prefix "%NODEJS_DIR%\mcp-server"

  if errorlevel 1 (
    popd
    goto fail
  )
  popd
)
if not %INSTALL_TTS% EQU 1 goto :skip_dl_tts
echo.
echo [6/6] 下载 TTS 模型 (本地文字转语音需要)
if not exist "%BACKEND_DIR%\download_tts.py" (
  echo 未找到 backend\download_tts.py
  goto fail
)
"%PYTHON_EXE%" "%CD%\embedded_python_bootstrap.py" "%BACKEND_DIR%\download_tts.py" --gpu-variant %TTS_VARIANT%
if errorlevel 1 goto fail
:skip_dl_tts
echo.
echo 安装完成。
exit /b 0

:show_help
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$lines = @('用法：setup-runtime.bat --torch cu128^|cu121^|cu130^|cpu [--tts yes^|no] [--source cn^|official] [--install-python yes^|no] [--install-node yes^|no] [--install-bundle-node yes^|no] [--tts-variant standard^|nvidia50]','', '参数说明：','参数 --torch: PyTorch 版本。cu128=GPU CUDA 12.8，cu121=GPU CUDA 12.1，cu130=GPU CUDA 13.0，cpu=CPU 版；省略此参数则不安装 PyTorch。','参数 --tts: 是否下载 TTS 模型。yes=下载，no=不下载。','参数 --source: 依赖源。cn=国内镜像，official=官方源。','参数 --install-python: 是否安装 Python + pip + wheel 等基础环境。','参数 --install-node: 是否安装 frontend 和 mc-operator 的 Node.js 依赖。','参数 --install-bundle-node: 是否安装 .nodejs 便携版 Node.js 与 MCP server 依赖。','参数 --tts-variant: TTS 包类型。standard=普通显卡，nvidia50=50 系显卡。','参数 --fix-py-requirements: 仅安装 Python 依赖（requirements.txt），不安装 PyTorch 等其它组件。','', '示例：','setup-runtime.bat --torch cu128 --tts yes --source cn --install-python yes --install-node yes --install-bundle-node yes --tts-variant nvidia50','setup-runtime.bat --torch cpu --tts no --source official --install-python yes --install-node yes --install-bundle-node yes','', '提示：TTS 下载后可通过前端组件页面管理启停。'); $lines -join [Environment]::NewLine"
exit /b 0

:fail
echo.
echo 安装失败，请根据上面的提示处理后重试。
pause
exit /b 1
