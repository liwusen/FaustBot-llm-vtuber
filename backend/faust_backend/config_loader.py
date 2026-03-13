import json
import os
from typing import Any, Dict
import copy

p_join=os.path.join
d_name=os.path.dirname
a_path=os.path.abspath


CONFIG_ROOT=d_name(d_name(a_path(__file__)))
CONFIG_FILE_P_PATH = p_join(CONFIG_ROOT, 'faust.config.private.json')
CONFIG_FILE_P_EXAMPLE=p_join(CONFIG_ROOT, 'faust.config.private.example')
DATA_ROOT=p_join(CONFIG_ROOT, 'data')

if not os.path.exists(CONFIG_FILE_P_PATH):
    print("[config_loader] Private config file not found." )
    print("     这说明你没有指定大模型KEY,请自行申请并且填入")
    os.copy(CONFIG_FILE_P_EXAMPLE, CONFIG_FILE_P_PATH)
    print(f"    已经使用模板文件创建了一个新的私密配置文件: {CONFIG_FILE_P_PATH}")
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
    PT_EVAL_TRIGGER_ENABLED=config.get('PY_EVAL_TRIGGER_ENABLED', False)
def print_globals():
    print("Current Global Configuration Variables Of Faust:")
    for k, v in globals().items():
        if not k.startswith("__") and k.isupper() and isinstance(v, (str, int, float, bool, dict, list)):
            print(f"{k}: {v}")
if __name__=="__main__":
    print_globals()