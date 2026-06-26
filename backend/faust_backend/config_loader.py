import json
import os
import shutil
from typing import Any, Dict
import copy
import argparse
import random
import sys
from pathlib import Path
p_join=os.path.join
d_name=os.path.dirname
a_path=os.path.abspath


# 项目基础设施根目录（backend/ 代码所在目录）
PROJECT_ROOT = d_name(d_name(a_path(__file__)))
# 用户数据根目录（~/.faustbot），存放配置、Agent 数据、缓存等
CONFIG_ROOT = os.path.join(os.path.expanduser("~"), ".faustbot")
CONFIG_FILE_P_PATH = p_join(CONFIG_ROOT, 'faust.config.private.json')
CONFIG_FILE_P_EXAMPLE = p_join(PROJECT_ROOT, 'faust.config.private.example.json')
DATA_ROOT = p_join(CONFIG_ROOT, 'data')
CONFIG_FILE_PATH = p_join(CONFIG_ROOT, 'faust.config.json')
PRIVATE_CONFIG_AUTO_CREATED = False
PRIVATE_CONFIG_WAS_MISSING = False


def _ensure_faustbot_init():
    faustbot = Path(CONFIG_ROOT)
    project_root = Path(PROJECT_ROOT)

    if not faustbot.exists():
        print("[config_loader] 初始化 ~/.faustbot 目录...")
        faustbot.mkdir(parents=True, exist_ok=True)

        src_agents = project_root / "agents_template"
        if not src_agents.exists():
            src_agents = project_root / "agents"
        if src_agents.exists():
            dst_agents = faustbot / "agents"
            shutil.copytree(src_agents, dst_agents, dirs_exist_ok=True)
            print(f"[config_loader]  已复制 {src_agents.name}/ → {dst_agents}")

        for example_file in project_root.glob("*.example.json"):
            dst_name = example_file.name.replace(".example.json", ".json")
            if not (faustbot / dst_name).exists():
                shutil.copy(example_file, faustbot / dst_name)
                print(f"[config_loader]  已创建 {faustbot / dst_name}")

        blive_example = project_root / "blive_config.example.json"
        if blive_example.exists() and not (faustbot / "blive_config.json").exists():
            shutil.copy(blive_example, faustbot / "blive_config.json")
            print(f"[config_loader]  已创建 {faustbot / 'blive_config.json'}")

        src_plugins = project_root / "default_plugins"
        if src_plugins.exists():
            dst_plugins = faustbot / "plugins"
            dst_plugins.mkdir(parents=True, exist_ok=True)
            for item in src_plugins.iterdir():
                dest = dst_plugins / item.name
                if not dest.exists():
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    elif item.is_file() and item.name != "plugins.state.json":
                        shutil.copy(item, dest)
            print(f"[config_loader]  已复制 default_plugins/ → {dst_plugins}")

        print("[config_loader]  ~/.faustbot 初始化完成")

    # Always ensure subdirectories and voice files exist
    for subdir in ("data", "cache", "voices", "logs"):
        (faustbot / subdir).mkdir(parents=True, exist_ok=True)

    src_voices = project_root / "voices"
    if src_voices.exists():
        dst_voices = faustbot / "voices"
        for item in src_voices.iterdir():
            dest = dst_voices / item.name
            if not dest.exists():
                if item.is_file():
                    shutil.copy(item, dest)
                elif item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)


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
    global MEMORY_GRAPH_ENABLED, MEMORY_IMAGE_ENABLED, MEMORY_IMAGE_VLM_MODEL
    global RERANK_ENABLED, RERANK_API_BASE, RERANK_MODEL, RERANK_API_KEY, RERANK_TOP_K
    global BM25_ONLY
    global TEXT_CHAT_BAR_Y_FACTOR, FRONTEND_QUICK_CONTROLLER_X_OFFSET
    global TTS_MODE, ASR_MODE, OPENAI_TTS_BASE_URL, OPENAI_TTS_MODEL, OPENAI_TTS_VOICE, OPENAI_TTS_RESPONSE_FORMAT, OPENAI_TTS_SPEED, OPENAI_TTS_INSTRUCTIONS
    global OPENAI_ASR_BASE_URL, OPENAI_ASR_MODEL, OPENAI_ASR_LANGUAGE, OPENAI_ASR_PROMPT, OPENAI_ASR_RESPONSE_FORMAT, OPENAI_ASR_TEMPERATURE, OPENAI_ASR_TIMESTAMP_GRANULARITIES
    global OPENAI_ASR_ENERGY_THRESHOLD, OPENAI_ASR_SILENCE_MS, OPENAI_ASR_MIN_SPEECH_MS, OPENAI_ASR_PREROLL_MS
    global OPENAI_TTS_API_KEY, OPENAI_ASR_API_KEY, FAUSTBOT_CLOUD_SERVICE_KEY
    global TTS_REFER_WAV_PATH, TTS_PROMPT_TEXT, TTS_PROMPT_LANGUAGE
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
    RERANK_API_KEY = private_config.get('RERANK_API_KEY', '')
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
    RERANK_ENABLED = bool(config.get('RERANK_ENABLED', False))
    RERANK_API_BASE = str(config.get('RERANK_API_BASE', 'https://api.openai.com/v1') or 'https://api.openai.com/v1').strip()
    RERANK_MODEL = str(config.get('RERANK_MODEL', 'Qwen3-Reranker-4B') or 'Qwen3-Reranker-4B').strip()
    RERANK_TOP_K = int(config.get('RERANK_TOP_K', 5) or 5)
    BM25_ONLY = bool(config.get('BM25_ONLY', False))
    MEMORY_GRAPH_ENABLED = bool(config.get('MEMORY_GRAPH_ENABLED', True))
    MEMORY_IMAGE_ENABLED = bool(config.get('MEMORY_IMAGE_ENABLED', True))
    MEMORY_IMAGE_VLM_MODEL = str(config.get('MEMORY_IMAGE_VLM_MODEL', 'gpt-4o') or 'gpt-4o').strip()
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


_ensure_faustbot_init()
load_configs()
    
def print_globals():
    print("Current Global Configuration Variables Of Faust:")
    mod = sys.modules[__name__]
    for k, v in vars(mod).items():
        if not k.startswith("_") and k.isupper() and isinstance(v, (str, int, float, bool, dict, list)):
            print(f"{k}: {v}")
argparser = argparse.ArgumentParser(description="FAUST Backend Main Service\n命令行参数可以覆盖配置文件中的设置，优先级高于配置文件。\nThis agent has super cow powers")
argparser.add_argument("--agent",type=str,default="NONE",action="store",help="Agent name to use")
argparser.add_argument("--no-run-other-backend-services",action="store_true",help="Whether to run other backend services as subprocess like ASR/TTS (default: False)")
argparser.add_argument("--save-in-memory",action="store_true",help="Memory Checkpointer and Store for debugging (default: False)")
argparser.add_argument("--MOO",action="store_true",help="apt-get:???\n这里没有任何彩蛋!!!")
argparser.add_argument("--no-startup-chat",action="store_true",help="Whether to disable startup chat (default: False)")
args, _ = argparser.parse_known_args()
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