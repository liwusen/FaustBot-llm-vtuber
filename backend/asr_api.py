from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from funasr import AutoModel

import torch
import numpy as np
import os
import sys
import re

from datetime import datetime

# 保存原始的stdout和stderr
original_stdout = sys.stdout
original_stderr = sys.stderr

os.chdir(os.path.dirname(os.path.abspath(__file__)))
# 创建一个可以同时写到文件和终端的类，并过滤ANSI颜色码
class TeeOutput:
    def __init__(self, file1, file2):
        self.file1 = file1
        self.file2 = file2
        # 用于匹配ANSI颜色码的正则表达式
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def write(self, data):
        # 终端输出保持原样（带颜色）
        self.file1.write(data)
        # 文件输出去掉颜色码
        clean_data = self.ansi_escape.sub('', data)
        self.file2.write(clean_data)
        self.file1.flush()
        self.file2.flush()

    def flush(self):
        self.file1.flush()
        self.file2.flush()

    def isatty(self):
        return self.file1.isatty()

    def fileno(self):
        return self.file1.fileno()


# 创建logs目录
LOGS_DIR = "logs"
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

# 设置双重输出
log_file = open(os.path.join(LOGS_DIR, 'asr.log'), 'w', encoding='utf-8')
sys.stdout = TeeOutput(original_stdout, log_file)
sys.stderr = TeeOutput(original_stderr, log_file)

app = FastAPI()

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 创建模型存储目录
MODEL_DIR = os.path.join("asr-hub", "model")
if not os.path.exists(MODEL_DIR):
    os.makedirs(MODEL_DIR)

# 设置设备和数据类型
device = "cuda" if torch.cuda.is_available() else "cpu"
torch.set_default_dtype(torch.float32)

# 初始化模型状态
model_state = {
    "asr_model": None,
    "punc_model": None
}

whisper_state = {
    "model": None,
    "loaded_name": None,
}


def _current_asr_mode() -> str:
    try:
        import faust_backend.config_loader as conf
        return str(getattr(conf, "ASR_MODE", "local") or "local").strip().lower()
    except Exception:
        return "local"


def _resolve_whisper_device() -> str:
    try:
        import faust_backend.config_loader as conf
        device = str(getattr(conf, "WHISPER_DEVICE", "auto") or "auto").strip().lower()
    except Exception:
        device = "auto"
    if device in {"", "auto"}:
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


def _get_whisper_model():
    import faust_backend.config_loader as conf
    model_name = str(getattr(conf, "WHISPER_MODEL", "base") or "base").strip()
    if whisper_state["model"] is None or whisper_state["loaded_name"] != model_name:
        try:
            import whisper
        except Exception as exc:
            raise RuntimeError(f"未安装 openai-whisper: {exc}") from exc
        whisper_state["model"] = whisper.load_model(model_name, device=_resolve_whisper_device())
        whisper_state["loaded_name"] = model_name
    return whisper_state["model"]
@app.on_event("startup")
async def startup_event():
    print("正在加载模型...")

    # 设置环境变量来指定模型下载位置
    asr_model_path = os.path.join(MODEL_DIR, "asr")
    if not os.path.exists(asr_model_path):
        os.makedirs(asr_model_path)

    # 保存原始环境变量
    original_modelscope_cache = os.environ.get('MODELSCOPE_CACHE', '')
    original_funasr_home = os.environ.get('FUNASR_HOME', '')

    # 设置环境变量
    os.environ['MODELSCOPE_CACHE'] = asr_model_path
    os.environ['FUNASR_HOME'] = MODEL_DIR

    if _current_asr_mode() == "whisper":
        print("正在加载 Whisper ASR 模型...")
        _get_whisper_model()
        print("Whisper ASR 模型加载完成")
    else:
        # 加载ASR模型
        print("正在加载ASR模型...")
        model_state["asr_model"] = AutoModel(
            model="iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
            device=device,
            model_type="pytorch",
            dtype="float32"
        )
        print("ASR模型加载完成")

        # 加载标点符号模型
        print("正在加载标点符号模型...")
        model_state["punc_model"] = AutoModel(
            model="iic/punc_ct-transformer_cn-en-common-vocab471067-large",
            model_revision="v2.0.4",
            device=device,
            model_type="pytorch",
            dtype="float32"
        )
    # 恢复原始环境变量
    if original_modelscope_cache:
        os.environ['MODELSCOPE_CACHE'] = original_modelscope_cache
    else:
        os.environ.pop('MODELSCOPE_CACHE', None)

    if original_funasr_home:
        os.environ['FUNASR_HOME'] = original_funasr_home
    else:
        os.environ.pop('FUNASR_HOME', None)
    print("标点符号模型加载完成")


@app.post("/v1/upload_audio")
async def upload_audio(file: UploadFile = File(...)):
    try:
        # 直接读取音频数据到内存
        audio_bytes = await file.read()

        if _current_asr_mode() == "whisper":
            import tempfile
            from pathlib import Path

            model = _get_whisper_model()
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                    temp_path = Path(tmp_file.name)
                    tmp_file.write(audio_bytes)
                import faust_backend.config_loader as conf
                result = model.transcribe(
                    str(temp_path),
                    language=str(getattr(conf, "WHISPER_LANGUAGE", "") or "").strip() or None,
                    prompt=str(getattr(conf, "WHISPER_PROMPT", "") or "").strip() or None,
                    temperature=float(getattr(conf, "WHISPER_TEMPERATURE", 0.0) or 0.0),
                    best_of=int(getattr(conf, "WHISPER_BEST_OF", 5) or 5),
                    beam_size=int(getattr(conf, "WHISPER_BEAM_SIZE", 5) or 5),
                    fp16=bool(getattr(conf, "WHISPER_FP16", False)) and torch.cuda.is_available(),
                )
                return {
                    "status": "success",
                    "filename": file.filename or "uploaded_audio",
                    "text": str((result or {}).get("text") or "").strip(),
                    "raw": result,
                    "mode": "whisper"
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "filename": file.filename or "uploaded_audio",
                    "message": f"Whisper ASR 失败: {exc}"
                }
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except Exception:
                        pass

        # 使用 soundfile 或 librosa 直接从内存中解析音频
        import io
        try:
            import soundfile as sf
            # 直接从内存中读取音频数据
            audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))
            print(f"音频数据形状: {audio_data.shape}, 采样率: {sample_rate}")
        except ImportError:
            print("soundfile 不可用，尝试使用 librosa")
            try:
                import librosa
                audio_data, sample_rate = librosa.load(io.BytesIO(audio_bytes), sr=16000)
                print(f"音频数据形状: {audio_data.shape}, 采样率: {sample_rate}")
            except ImportError:
                return {
                    "status": "error",
                    "message": "需要安装 soundfile 或 librosa 库来处理音频"
                }

        # 进行ASR处理 - 直接传入音频数组
        with torch.no_grad():
            # 确保为单通道 float32 numpy 数组（模型期望 float32）
            try:
                audio_data = np.asarray(audio_data)
                if audio_data.ndim > 1:
                    # 转为单通道（平均各声道）
                    audio_data = np.mean(audio_data, axis=1)
                audio_data = audio_data.astype('float32')
            except Exception as e:
                print(f"处理音频数组时出错（类型/维度转换）：{e}")

            # 语音识别 - 传入 numpy 数组而不是文件路径
            asr_result = model_state["asr_model"].generate(
                input=audio_data,
                dtype="float32"
            )

            # 添加标点符号（标点模型通常期望文本输入；不要给它错误的 dtype）
            if asr_result and len(asr_result) > 0:
                text_input = asr_result[0]["text"]
                final_result = None
                try:
                    # 不传 dtype，这里传入文本让标点模型自行处理tokenization
                    final_result = model_state["punc_model"].generate(
                        input=text_input
                    )
                except Exception as e:
                    # 如果标点模型失败，记录并回退到未标点的文本
                    print(f"标点模型处理失败，回退到原始文本: {e}")

                return {
                    "status": "success",
                    "filename": file.filename or "uploaded_audio",
                    "text": (final_result[0]["text"] if final_result else text_input)
                }
            else:
                return {
                    "status": "error",
                    "filename": file.filename or "uploaded_audio",
                    "message": "语音识别失败"
                }

    except Exception as e:
        print(f"处理音频时出错: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }
if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=1000)