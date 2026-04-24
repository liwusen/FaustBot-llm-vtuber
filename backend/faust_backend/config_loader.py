import json
import os
import shutil
from typing import Any, Dict
import copy
import argparse
import random
import sys
p_join=os.path.join
d_name=os.path.dirname
a_path=os.path.abspath


CONFIG_ROOT=d_name(d_name(a_path(__file__)))
CONFIG_FILE_P_PATH = p_join(CONFIG_ROOT, 'faust.config.private.json')
CONFIG_FILE_P_EXAMPLE=p_join(CONFIG_ROOT, 'faust.config.private.example')
DATA_ROOT=p_join(CONFIG_ROOT, 'data')
CONFIG_FILE_PATH= p_join(CONFIG_ROOT, 'faust.config.json')
PRIVATE_CONFIG_AUTO_CREATED = False
PRIVATE_CONFIG_WAS_MISSING = False


def _ensure_private_config_exists():
    global PRIVATE_CONFIG_AUTO_CREATED, PRIVATE_CONFIG_WAS_MISSING
    if os.path.exists(CONFIG_FILE_P_PATH):
        PRIVATE_CONFIG_WAS_MISSING = False
        return
    PRIVATE_CONFIG_WAS_MISSING = True
    print("[config_loader] Private config file not found." )
    print("     这说明你没有指定大模型KEY,请自行申请并且填入")
    if os.path.exists(CONFIG_FILE_P_EXAMPLE):
        shutil.copy(CONFIG_FILE_P_EXAMPLE, CONFIG_FILE_P_PATH)
    else:
        with open(CONFIG_FILE_P_PATH, 'w', encoding='utf-8') as f:
            json.dump({}, f, ensure_ascii=False, indent=4)
    PRIVATE_CONFIG_AUTO_CREATED = True
    print(f"    已经使用模板文件创建了一个新的私密配置文件: {CONFIG_FILE_P_PATH}")


def load_configs():
    global private_config, config
    global CHAT_API_KEY, DEEPSEEK_API_KEY, SEARCH_API_KEY, GUI_OPERATOR_LLM_KEY, SECURITY_VERIFIER_LLM_KEY, KB_OPENAI_API_KEY
    global GUI_OPERATOR_LLM_MODEL, GUI_OPERATOR_LLM_BASE, CHAT_MODEL, CHAT_API_BASE, PT_EVAL_TRIGGER_ENABLED, AGENT_NAME
    global SECURITY_VERIFIER_LLM_API_ENDPOINT, SECURITY_VERIFIER_LLM_MODEL, SECURITY_SYS_ENABLED, AGENT_ROOT
    global KB_ENABLED, KB_EMBED_MODEL, KB_ASYNC_INDEX_ON_WRITE, ARAYA_ENABLED, ARAYA_IDLE_MINUTES
    global TEXT_CHAT_BAR_Y_FACTOR, FRONTEND_QUICK_CONTROLLER_X_OFFSET
    global TTS_MODE, ASR_MODE, OPENAI_TTS_BASE_URL, OPENAI_TTS_MODEL, OPENAI_TTS_VOICE, OPENAI_TTS_RESPONSE_FORMAT, OPENAI_TTS_SPEED, OPENAI_TTS_INSTRUCTIONS
    global OPENAI_ASR_BASE_URL, OPENAI_ASR_MODEL, OPENAI_ASR_LANGUAGE, OPENAI_ASR_PROMPT, OPENAI_ASR_RESPONSE_FORMAT, OPENAI_ASR_TEMPERATURE, OPENAI_ASR_TIMESTAMP_GRANULARITIES
    global OPENAI_ASR_ENERGY_THRESHOLD, OPENAI_ASR_SILENCE_MS, OPENAI_ASR_MIN_SPEECH_MS, OPENAI_ASR_PREROLL_MS
    global OPENAI_TTS_API_KEY, OPENAI_ASR_API_KEY, FAUSTBOT_CLOUD_SERVICE_KEY
    global TTS_REFER_WAV_PATH, TTS_PROMPT_TEXT, TTS_PROMPT_LANGUAGE
    global WHISPER_MODEL, WHISPER_DEVICE, WHISPER_LANGUAGE, WHISPER_PROMPT, WHISPER_FP16, WHISPER_TEMPERATURE, WHISPER_BEST_OF, WHISPER_BEAM_SIZE
    global EDGE_TTS_VOICE, EDGE_TTS_RATE, EDGE_TTS_PITCH, EDGE_TTS_TIMEOUT_SECONDS
    global FAUSTBOT_CLOUD_BASE_URL, FAUSTBOT_CLOUD_DEFAULT_REFER_HASH, FAUSTBOT_CLOUD_TIMEOUT_SECONDS
    _ensure_private_config_exists()
    with open(CONFIG_FILE_P_PATH, 'r', encoding='utf-8') as f:
        private_config = json.load(f)
    with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)

    CHAT_API_KEY = private_config.get('CHAT_API_KEY', private_config.get('DEEPSEEK_API_KEY', ''))
    DEEPSEEK_API_KEY = CHAT_API_KEY  # 兼容旧代码引用
    SEARCH_API_KEY = private_config.get('SEARCH_API_KEY', '')
    GUI_OPERATOR_LLM_KEY = private_config.get('GUI_OPERATOR_LLM_KEY', '')
    SECURITY_VERIFIER_LLM_KEY = private_config.get('SECURITY_VERIFIER_LLM_KEY', '')
    KB_OPENAI_API_KEY = private_config.get('KB_OPENAI_API_KEY', '')
    OPENAI_TTS_API_KEY = private_config.get('OPENAI_TTS_API_KEY', CHAT_API_KEY)
    OPENAI_ASR_API_KEY = private_config.get('OPENAI_ASR_API_KEY', CHAT_API_KEY)
    FAUSTBOT_CLOUD_SERVICE_KEY = private_config.get('FAUSTBOT_CLOUD_SERVICE_KEY', '')

    GUI_OPERATOR_LLM_MODEL = config.get('GUI_OPERATOR_LLM_MODEL', 'gui-plus')
    GUI_OPERATOR_LLM_BASE = config.get('GUI_OPERATOR_LLM_BASE', 'https://www.dmxapi.cn/v1/chat/completions')
    CHAT_MODEL = config.get('CHAT_MODEL', 'gpt-4o')
    CHAT_API_BASE = config.get('CHAT_API_BASE', 'https://www.dmxapi.cn/v1')
    PT_EVAL_TRIGGER_ENABLED=config.get('PY_EVAL_TRIGGER_ENABLED', False)
    AGENT_NAME=config.get('AGENT_NAME', 'faust')
    SECURITY_VERIFIER_LLM_API_ENDPOINT = config.get('SECURITY_VERIFIER_API_ENDPOINT', 'https://www.dmxapi.cn/v1')
    SECURITY_VERIFIER_LLM_MODEL = config.get('SECURITY_VERIFIER_LLM_MODEL', 'qwen3.5-flash')
    SECURITY_SYS_ENABLED = config.get('SECURITY_SYS_ENABLED', False)
    KB_ENABLED = bool(config.get('KB_ENABLED', True))
    KB_EMBED_MODEL = str(config.get('KB_EMBED_MODEL', 'text-embedding-3-small') or 'text-embedding-3-small').strip()
    KB_ASYNC_INDEX_ON_WRITE = bool(config.get('KB_ASYNC_INDEX_ON_WRITE', True))
    ARAYA_ENABLED = bool(config.get('ARAYA_ENABLED', True))
    ARAYA_IDLE_MINUTES = float(config.get('ARAYA_IDLE_MINUTES', 30) or 30)
    TEXT_CHAT_BAR_Y_FACTOR = float(config.get('TEXT_CHAT_BAR_Y_FACTOR', 0.53) or 0.53)
    FRONTEND_QUICK_CONTROLLER_X_OFFSET = int(config.get('FRONTEND_QUICK_CONTROLLER_X_OFFSET', -12) or -12)
    TTS_MODE = str(config.get('TTS_MODE', 'local') or 'local').strip().lower()
    ASR_MODE = str(config.get('ASR_MODE', 'local') or 'local').strip().lower()
    OPENAI_TTS_BASE_URL = str(config.get('OPENAI_TTS_BASE_URL', 'https://api.openai.com/v1') or 'https://api.openai.com/v1').strip()
    OPENAI_TTS_MODEL = str(config.get('OPENAI_TTS_MODEL', 'gpt-4o-mini-tts') or 'gpt-4o-mini-tts').strip()
    OPENAI_TTS_VOICE = str(config.get('OPENAI_TTS_VOICE', 'alloy') or 'alloy').strip()
    OPENAI_TTS_RESPONSE_FORMAT = str(config.get('OPENAI_TTS_RESPONSE_FORMAT', 'mp3') or 'mp3').strip()
    OPENAI_TTS_SPEED = float(config.get('OPENAI_TTS_SPEED', 1.0) or 1.0)
    OPENAI_TTS_INSTRUCTIONS = str(config.get('OPENAI_TTS_INSTRUCTIONS', '') or '')
    OPENAI_ASR_BASE_URL = str(config.get('OPENAI_ASR_BASE_URL', 'https://api.openai.com/v1') or 'https://api.openai.com/v1').strip()
    OPENAI_ASR_MODEL = str(config.get('OPENAI_ASR_MODEL', 'gpt-4o-transcribe') or 'gpt-4o-transcribe').strip()
    OPENAI_ASR_LANGUAGE = str(config.get('OPENAI_ASR_LANGUAGE', '') or '').strip()
    OPENAI_ASR_PROMPT = str(config.get('OPENAI_ASR_PROMPT', '') or '')
    OPENAI_ASR_RESPONSE_FORMAT = str(config.get('OPENAI_ASR_RESPONSE_FORMAT', 'json') or 'json').strip()
    OPENAI_ASR_TEMPERATURE = float(config.get('OPENAI_ASR_TEMPERATURE', 0.0) or 0.0)
    OPENAI_ASR_TIMESTAMP_GRANULARITIES = str(config.get('OPENAI_ASR_TIMESTAMP_GRANULARITIES', '') or '').strip()
    FAUSTBOT_CLOUD_BASE_URL = str(config.get('FAUSTBOT_CLOUD_BASE_URL', 'http://127.0.0.1:18980') or 'http://127.0.0.1:18980').strip()
    FAUSTBOT_CLOUD_DEFAULT_REFER_HASH = str(config.get('FAUSTBOT_CLOUD_DEFAULT_REFER_HASH', '') or '').strip()
    FAUSTBOT_CLOUD_TIMEOUT_SECONDS = int(config.get('FAUSTBOT_CLOUD_TIMEOUT_SECONDS', 120) or 120)
    OPENAI_ASR_ENERGY_THRESHOLD = float(config.get('OPENAI_ASR_ENERGY_THRESHOLD', 0.02) or 0.02)
    OPENAI_ASR_SILENCE_MS = int(config.get('OPENAI_ASR_SILENCE_MS', 700) or 700)
    OPENAI_ASR_MIN_SPEECH_MS = int(config.get('OPENAI_ASR_MIN_SPEECH_MS', 250) or 250)
    OPENAI_ASR_PREROLL_MS = int(config.get('OPENAI_ASR_PREROLL_MS', 250) or 250)
    WHISPER_MODEL = str(config.get('WHISPER_MODEL', 'base') or 'base').strip()
    WHISPER_DEVICE = str(config.get('WHISPER_DEVICE', 'auto') or 'auto').strip().lower()
    WHISPER_LANGUAGE = str(config.get('WHISPER_LANGUAGE', '') or '').strip()
    WHISPER_PROMPT = str(config.get('WHISPER_PROMPT', '') or '')
    WHISPER_FP16 = bool(config.get('WHISPER_FP16', False))
    WHISPER_TEMPERATURE = float(config.get('WHISPER_TEMPERATURE', 0.0) or 0.0)
    WHISPER_BEST_OF = int(config.get('WHISPER_BEST_OF', 5) or 5)
    WHISPER_BEAM_SIZE = int(config.get('WHISPER_BEAM_SIZE', 5) or 5)
    
    # TTS 参考音频配置
    TTS_REFER_WAV_PATH = config.get('TTS_REFER_WAV_PATH', p_join(CONFIG_ROOT, 'voices', 'neuro.wav'))
    TTS_PROMPT_TEXT = config.get('TTS_PROMPT_TEXT', 'Hold on please, I\'m busy. Okay, I think I heard him say he wants me to stream Hollow Knight on Tuesday and Thursday.')
    TTS_PROMPT_LANGUAGE = config.get('TTS_PROMPT_LANGUAGE', 'en')
    EDGE_TTS_VOICE = str(config.get('EDGE_TTS_VOICE', 'en-US-AriaNeural') or 'en-US-AriaNeural').strip()
    EDGE_TTS_RATE = str(config.get('EDGE_TTS_RATE', '0%') or '0%').strip()
    EDGE_TTS_PITCH = str(config.get('EDGE_TTS_PITCH', '0%') or '0%').strip()
    EDGE_TTS_TIMEOUT_SECONDS = int(config.get('EDGE_TTS_TIMEOUT_SECONDS', 120) or 120)
    
    AGENT_ROOT=p_join(CONFIG_ROOT, "agents", AGENT_NAME)
    return config, private_config


def reload_configs():
    return load_configs()


load_configs()
    
def print_globals():
    print("Current Global Configuration Variables Of Faust:")
    for k, v in globals().items():
        if not k.startswith("__") and k.isupper() and isinstance(v, (str, int, float, bool, dict, list)):
            print(f"{k}: {v}")
argparser = argparse.ArgumentParser(description="FAUST Backend Main Service\n命令行参数可以覆盖配置文件中的设置，优先级高于配置文件。\nThis agent has super cow powers")
argparser.add_argument("--agent",type=str,default="NONE",action="store",help="Agent name to use")
argparser.add_argument("--no-run-other-backend-services",action="store_true",help="Whether to run other backend services as subprocess like ASR/TTS (default: False)")
argparser.add_argument("--save-in-memory",action="store_true",help="Memory Checkpointer and Store for debugging (default: False)")
argparser.add_argument("--MOO",action="store_true",help="apt-get:???\n这里没有任何彩蛋!!!")
argparser.add_argument("--no-startup-chat",action="store_true",help="Whether to disable startup chat (default: False)")
args = argparser.parse_args()
if args.agent != "NONE":
    AGENT_NAME = args.agent
    print(f"[config_loader] Agent name overridden by command line argument: {AGENT_NAME}")
if args.no_run_other_backend_services:
    print(f"[config_loader] Won't running other backend services as subprocess.")
if args.save_in_memory:
    print(f"[config_loader] Memory Checkpointer and Store enabled for debugging.")
if args.no_startup_chat:
    print(f"[config_loader] Startup chat disabled.")
if args.MOO:
    LIST=[]
    LIST.append("""
                 (__)
                 (oo)
           /------\\/
          / |    ||
         *  /\\---/\\
            ~~   ~~
..."Have you mooed today?"...""")
    print(random.choice(LIST))
    print("[config_loader]Apt-get:MOO!")
    sys.exit(325)
AGENT_ROOT=p_join(CONFIG_ROOT, "agents", AGENT_NAME)
if __name__=="__main__":
    print_globals()