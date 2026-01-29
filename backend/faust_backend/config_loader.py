import json
import os
from typing import Any, Dict
p_join=os.path.join
d_name=os.path.dirname
a_path=os.path.abspath
CONFIG_ROOT=d_name(d_name(a_path(__file__)))
CONFIG_FILE_P_PATH = p_join(CONFIG_ROOT, 'faust.config.private.json')
if not os.path.exists(CONFIG_FILE_P_PATH):
    print("[config_loader] Private config file not found." )
    print("这说明你没有指定大模型KEY,请自行申请并且填入")
    raise FileNotFoundError(f"Private config file not found: {CONFIG_FILE_P_PATH}")
CONFIG_FILE_PATH= p_join(CONFIG_ROOT, 'faust.config.json')
with open(CONFIG_FILE_P_PATH, 'r', encoding='utf-8') as f:
    private_config = json.load(f)
    DEEPSEEK_API_KEY = private_config.get('DEEPSEEK_API_KEY', '')
    SEARCH_API_KEY = private_config.get('SEARCH_API_KEY', '')
    GUI_OPERATOR_LLM_KEY = private_config.get('GUI_OPERATOR_LLM_KEY', '')
with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
    config = json.load(f)
    GUI_OPERATOR_LLM_MODEL = config.get('GUI_OPERATOR_LLM_MODEL', 'gui-plus')
    GUI_OPERATOR_LLM_BASE = config.get('GUI_OPERATOR_LLM_BASE', 'https://www.dmxapi.cn/v1/chat/completions')