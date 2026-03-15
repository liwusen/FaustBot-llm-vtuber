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

if not os.path.exists(CONFIG_FILE_P_PATH):
    print("[config_loader] Private config file not found." )
    print("     这说明你没有指定大模型KEY,请自行申请并且填入")
    shutil.copy(CONFIG_FILE_P_EXAMPLE, CONFIG_FILE_P_PATH)
    print(f"    已经使用模板文件创建了一个新的私密配置文件: {CONFIG_FILE_P_PATH}")
    raise FileNotFoundError(f"Private config file not found: {CONFIG_FILE_P_PATH}")
CONFIG_FILE_PATH= p_join(CONFIG_ROOT, 'faust.config.json')
with open(CONFIG_FILE_P_PATH, 'r', encoding='utf-8') as f:
    private_config = json.load(f)
    DEEPSEEK_API_KEY = private_config.get('DEEPSEEK_API_KEY', '')
    SEARCH_API_KEY = private_config.get('SEARCH_API_KEY', '')
    GUI_OPERATOR_LLM_KEY = private_config.get('GUI_OPERATOR_LLM_KEY', '')
    SERCURITY_VERIFIER_LLM_KEY = private_config.get('SECURITY_VERIFIER_LLM_KEY', '')
with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
    config = json.load(f)
    GUI_OPERATOR_LLM_MODEL = config.get('GUI_OPERATOR_LLM_MODEL', 'gui-plus')
    GUI_OPERATOR_LLM_BASE = config.get('GUI_OPERATOR_LLM_BASE', 'https://www.dmxapi.cn/v1/chat/completions')
    PT_EVAL_TRIGGER_ENABLED=config.get('PY_EVAL_TRIGGER_ENABLED', False)
    AGENT_NAME=config.get('AGENT_NAME', 'faust')
    SECURITY_VERIFIER_LLM_API_ENDPOINT = config.get('SECURITY_VERIFIER_API_ENDPOINT', 'https://www.dmxapi.cn/v1')
    SECURITY_VERIFIER_LLM_MODEL = config.get('SECURITY_VERIFIER_LLM_MODEL', 'qwen3.5-flash')
    SECURITY_SYS_ENABLED = config.get('SECURITY_SYS_ENABLED', False)
    
def print_globals():
    print("Current Global Configuration Variables Of Faust:")
    for k, v in globals().items():
        if not k.startswith("__") and k.isupper() and isinstance(v, (str, int, float, bool, dict, list)):
            print(f"{k}: {v}")
argparser = argparse.ArgumentParser(description="FAUST Backend Main Service\n命令行参数可以覆盖配置文件中的设置，优先级高于配置文件。\nThis agent has super cow powers")
argparser.add_argument("--agent",type=str,default="NONE",action="store",help="Agent name to use (default: faust)")
argparser.add_argument("--run-other-backend-services",action="store_true",help="Whether to run other backend services as subprocess like ASR/TTS (default: False)")
argparser.add_argument("--save-in-memory",action="store_true",help="Memory Checkpointer and Store for debugging (default: False)")
argparser.add_argument("--MOO",action="store_true",help="apt-get:???\n这里没有任何彩蛋!!!")
args = argparser.parse_args()
if args.agent != "NONE":
    AGENT_NAME = args.agent
    print(f"[Faust.backend.config_loader] Agent name overridden by command line argument: {AGENT_NAME}")
if args.run_other_backend_services:
    print(f"[Faust.backend.config_loader] Running other backend services as subprocess.")
if args.MOO:
    LIST=[]
    LIST.append("""
                 (__)
                 (oo)
           /------\/
          / |    ||
         *  /\---/\
            ~~   ~~
..."Have you mooed today?"...""")
    LIST.append("""                 (__)
         _______~(..)~
           ,----\(oo)
          /|____|,'
         * /"\ /\
           ~ ~ ~ ~
..."Have you mooed today?"...""")
    LIST.append("""                     \_/
   m00h  (__)       -(_)-
      \  ~Oo~___     / \
         (..)  |\
___________|_|_|_____________
..."Have you mooed today?"...""")
    print(random.choice(LIST))
    print("[Faust.backend.config_loarder]Apt-get:MOO!")
    sys.exit(325)
if __name__=="__main__":
    print_globals()