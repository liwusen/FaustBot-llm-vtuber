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
WORKDIR_ROOT = CONFIG_ROOT
SKILL_TEMPLATE_ROOT = p_join(PROJECT_ROOT, 'skill_template')
CONFIG_FILE_P_PATH = p_join(CONFIG_ROOT, 'faust.config.private.json')
CONFIG_FILE_P_EXAMPLE = p_join(PROJECT_ROOT, 'faust.config.private.example.json')
DATA_ROOT = p_join(CONFIG_ROOT, 'data')
MODEL_ROOT = p_join(CONFIG_ROOT, 'models')
LIVE2D_MODEL_ROOT = p_join(MODEL_ROOT, '2D')
VRM_MODEL_ROOT = p_join(MODEL_ROOT, 'VRM')
IMAGE_MODEL_ROOT = p_join(CONFIG_ROOT, 'models', 'image')
PLUGIN_DATA_ROOT = p_join(CONFIG_ROOT, 'plugin_data')
CONFIG_FILE_PATH = p_join(CONFIG_ROOT, 'faust.config.json')
PRIVATE_CONFIG_AUTO_CREATED = False
PRIVATE_CONFIG_WAS_MISSING = False


def _copy_missing_file(src: Path, dst: Path) -> None:
    if dst.exists() or not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)


def _copy_missing_tree(src_root: Path, dst_root: Path) -> None:
    if not src_root.exists() or not src_root.is_dir():
        return
    dst_root.mkdir(parents=True, exist_ok=True)
    for item in src_root.iterdir():
        dst = dst_root / item.name
        if item.is_dir():
            _copy_missing_tree(item, dst)
        elif item.is_file():
            _copy_missing_file(item, dst)


def _parse_version_parts(raw: str) -> tuple[int, ...]:
    text = str(raw or "0").strip()
    if not text:
        return (0,)
    parts: list[int] = []
    for chunk in text.replace('-', '.').split('.'):
        digits = ''.join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts or [0])


def _plugin_manifest_version(plugin_dir: Path) -> tuple[int, ...]:
    manifest = plugin_dir / 'plugin.json'
    if not manifest.exists():
        return (0,)
    try:
        raw = json.loads(manifest.read_text(encoding='utf-8'))
    except Exception:
        return (0,)
    return _parse_version_parts(str(raw.get('version') or '0'))


def _sync_default_plugins(project_root: Path, faustbot: Path) -> None:
    src_plugins = project_root / 'default_plugins'
    if not src_plugins.exists():
        print(f"[config_loader]  WARNING:默认插件目录 {src_plugins} 不存在，跳过复制。")
        return
    dst_plugins = faustbot / 'plugins'
    dst_plugins.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for item in src_plugins.iterdir():
        if item.name == 'plugins.state.json':
            continue
        dest = dst_plugins / item.name
        if not dest.exists():
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            elif item.is_file():
                shutil.copy(item, dest)
            copied.append(item.name)
            continue
        if not item.is_dir() or not dest.is_dir():
            continue
        if _plugin_manifest_version(item) > _plugin_manifest_version(dest):
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(item, dest, dirs_exist_ok=True)
            copied.append(item.name)
    if copied:
        print(f"[config_loader]  已同步默认插件: {', '.join(sorted(copied))}")


def _ensure_model_templates(faustbot: Path, project_root: Path) -> None:
    model_root = faustbot / 'models'
    live2d_root = model_root / '2D'
    vrm_root = model_root / 'VRM'
    image_root = model_root / 'image'
    live2d_root.mkdir(parents=True, exist_ok=True)
    vrm_root.mkdir(parents=True, exist_ok=True)
    image_root.mkdir(parents=True, exist_ok=True)

    frontend_root = project_root.parent / 'frontend'
    live2d_sources = [
        frontend_root / 'models' / '2D',
        frontend_root / '2D',
    ]
    vrm_sources = [
        frontend_root / 'models' / 'live2d' / 'VRM',
        frontend_root / 'models' / 'VRM',
    ]

    for src in live2d_sources:
        _copy_missing_tree(src, live2d_root)
    for src in vrm_sources:
        _copy_missing_tree(src, vrm_root)


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

        _sync_default_plugins(project_root, faustbot)
        print("[config_loader]  ~/.faustbot 初始化完成")

    # Always ensure subdirectories and voice files exist
    for subdir in (
        "data",
        "cache",
        "voices",
        "logs",
        "plugin_data",
        os.path.join("models", "image"),
        os.path.join("models", "2D"),
        os.path.join("models", "VRM"),
    ):
        (faustbot / subdir).mkdir(parents=True, exist_ok=True)

    _sync_default_plugins(project_root, faustbot)

    _ensure_model_templates(faustbot, project_root)

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
    global CHAT_API_KEY, SEARCH_API_KEY, FAUSTBOT_CLOUD_SERVICE_KEY
    global CHAT_MODEL, CHAT_API_BASE, AGENT_NAME, AGENT_ROOT
    global EMBED_API_KEY, EMBED_API_BASE, EMBED_MODEL
    global KB_ENABLED, ARAYA_ENABLED, ARAYA_IDLE_MINUTES
    global RERANK_ENABLED, RERANK_TOP_K, BM25_ONLY
    global MCP_SERVERS
    global SECURITY_SYS_ENABLED
    global TEXT_CHAT_BAR_Y_FACTOR, FRONTEND_QUICK_CONTROLLER_X_OFFSET
    global TTS_MODE, ASR_MODE, OPENAI_TTS_BASE_URL, OPENAI_TTS_MODEL, OPENAI_TTS_VOICE, OPENAI_TTS_RESPONSE_FORMAT, OPENAI_TTS_SPEED, OPENAI_TTS_INSTRUCTIONS
    global OPENAI_ASR_BASE_URL, OPENAI_ASR_MODEL, OPENAI_ASR_LANGUAGE, OPENAI_ASR_PROMPT, OPENAI_ASR_RESPONSE_FORMAT, OPENAI_ASR_TEMPERATURE, OPENAI_ASR_TIMESTAMP_GRANULARITIES
    global OPENAI_ASR_ENERGY_THRESHOLD, OPENAI_ASR_SILENCE_MS, OPENAI_ASR_MIN_SPEECH_MS, OPENAI_ASR_PREROLL_MS
    global TTS_REFER_WAV_PATH, TTS_PROMPT_TEXT, TTS_PROMPT_LANGUAGE
    global EDGE_TTS_VOICE, EDGE_TTS_RATE, EDGE_TTS_PITCH, EDGE_TTS_TIMEOUT_SECONDS
    global FAUSTBOT_CLOUD_BASE_URL, FAUSTBOT_CLOUD_TIMEOUT_SECONDS
    global MM_BRIDGE_MAX_SCAN, MM_BRIDGE_REMOVE_SOURCE, MM_BRIDGE_KEEP_TURNS
    global THINKING_ENABLED, THINKING_PRESET, THINKING_INTENSITY
    _ensure_private_config_exists()
    with open(CONFIG_FILE_P_PATH, 'r', encoding='utf-8') as f:
        private_config = json.load(f)
    with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # —— Main LLM (all text/chat/speech/vision tasks) ——
    CHAT_API_KEY = private_config.get('CHAT_API_KEY', '')
    if not CHAT_API_KEY:
        print("[config_loader] Critical: CHAT_API_KEY is not set in the private config. Some features may not work properly.")
    CHAT_MODEL = config.get('CHAT_MODEL', 'gpt-4o')
    CHAT_API_BASE = config.get('CHAT_API_BASE', 'https://www.dmxapi.cn/v1')

    # —— Embedding LLM (KB vector encoding only) ——
    EMBED_API_KEY = private_config.get('EMBED_API_KEY', '')
    EMBED_API_BASE = str(config.get('EMBED_API_BASE', 'https://www.dmxapi.cn/v1') or 'https://www.dmxapi.cn/v1').strip()
    EMBED_MODEL = str(config.get('EMBED_MODEL', 'text-embedding-3-small') or 'text-embedding-3-small').strip()

    SEARCH_API_KEY = private_config.get('SEARCH_API_KEY', '')
    FAUSTBOT_CLOUD_SERVICE_KEY = private_config.get('FAUSTBOT_CLOUD_SERVICE_KEY', '')

    AGENT_NAME = config.get('AGENT_NAME', 'faust')
    SECURITY_SYS_ENABLED = config.get('SECURITY_SYS_ENABLED', False)
    KB_ENABLED = bool(config.get('KB_ENABLED', True))
    RERANK_ENABLED = bool(config.get('RERANK_ENABLED', False))
    RERANK_TOP_K = int(config.get('RERANK_TOP_K', 5) or 5)
    BM25_ONLY = bool(config.get('BM25_ONLY', False))
    ARAYA_ENABLED = bool(config.get('ARAYA_ENABLED', True))
    MM_BRIDGE_MAX_SCAN = int(config.get('MM_BRIDGE_MAX_SCAN', 6) or 6)
    MM_BRIDGE_REMOVE_SOURCE = bool(config.get('MM_BRIDGE_REMOVE_SOURCE', False))
    MM_BRIDGE_KEEP_TURNS = int(config.get('MM_BRIDGE_KEEP_TURNS', 2) or 2)
    MCP_SERVERS = copy.deepcopy(config.get('mcp_servers', {}) or {})
    ARAYA_IDLE_MINUTES = float(config.get('ARAYA_IDLE_MINUTES', 30) or 30)
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
    THINKING_ENABLED = bool(config.get('THINKING_ENABLED', False))
    THINKING_PRESET = str(config.get('THINKING_PRESET', 'none') or 'none').strip()
    THINKING_INTENSITY = str(config.get('THINKING_INTENSITY', 'medium') or 'medium').strip()
    AGENT_ROOT = p_join(CONFIG_ROOT, "agents", AGENT_NAME)
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
argparser = argparse.ArgumentParser(description="FaustBot Backend Main Service\n命令行参数可以覆盖配置文件中的设置，优先级高于配置文件。\nThis software has super cow powers")
argparser.add_argument("--agent",type=str,default="NONE",action="store",help="Agent name to use")
argparser.add_argument("--no-run-other-backend-services",action="store_true",help="Whether to run other backend services as subprocess like ASR/TTS (default: False)")
argparser.add_argument("--save-in-memory",action="store_true",help="Memory Checkpointer and Store for debugging (default: False)")
DEBUG_FLAG = False
argparser.add_argument("--MOO",action="store_true",help="apt-get:???\n这里没有任何彩蛋!!!")
argparser.add_argument("--no-startup-chat",action="store_true",help="Whether to disable startup chat (default: False)")
argparser.add_argument("--debug",action="store_true",help="Enable debug mode (default: False)")
args, _ = argparser.parse_known_args()
if args.agent != "NONE":
    AGENT_NAME = args.agent
    print(f"[config_loader] Agent name overridden by command line argument: {AGENT_NAME}")
if args.no_run_other_backend_services:
    print(f"[config_loader] Won't running other backend services as subprocess.")
if args.save_in_memory:
    print(f"[config_loader] Memory Checkpointer and Store enabled for debugging.")
if args.debug:
    print(f"[config_loader] Debug mode enabled.")
    DEBUG_FLAG = True
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
    sys.exit(1)
AGENT_ROOT=p_join(CONFIG_ROOT, "agents", AGENT_NAME)
if __name__=="__main__":
    print_globals()