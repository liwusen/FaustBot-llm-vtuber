import psutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List
from faust_backend.logger import get_logger

log = get_logger("faust.service")

BACKEND_ROOT = Path(__file__).resolve().parent.parent
STARTUP_WAIT_SECONDS = 15
TAIL_LOG_LINES = 120
CREATE_NEW_CONSOLE = subprocess.CREATE_NEW_CONSOLE

def find_process_by_port(port):
    for conn in psutil.net_connections():
        if conn.status == 'LISTEN' and conn.laddr.port == port:
            try:
                process = psutil.Process(conn.pid)
                return {
                    'pid': conn.pid,
                    'name': process.name(),
                    'exe': process.exe(),
                    'cmdline': ' '.join(process.cmdline())
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return {
                    'pid': conn.pid,
                    'name': 'Unknown',
                    'exe': 'Access Denied',
                    'cmdline': 'Access Denied'
                }
    return None


SERVICES: List[Dict[str, Any]] = [
    {
        'key': 'tts',
        'name': 'TTS Service',
        'description': '语音合成后端',
        'port': 5000,
        'script': 'TTS.bat',
        'log_file': 'tts-hub/GPT-SoVITS-Bundle/log_tts.log'
    },
    {
        'key': 'asr',
        'name': 'ASR Service',
        'description': '语音识别后端',
        'port': 1000,
        'script': 'ASR.bat',
        'log_file': 'log_asr.log'
    },
    {
        'key': 'mc_operator',
        'name': 'mc-operator',
        'description': 'Minecraft 指令桥',
        'port': 18901,
        'script': 'minecraft/mc-operator/mc.bat',
        'log_file': 'minecraft/mc-operator/log.log'
    },
]

def get_service_keys():
    return [service['key'] for service in SERVICES]

def _service_map() -> Dict[str, Dict[str, Any]]:
    return {item['key']: item for item in SERVICES}


def get_service_definition(service_key: str) -> Dict[str, Any]:
    service = _service_map().get((service_key or '').strip())
    if not service:
        raise KeyError(f'未知服务: {service_key}')
    return service


def resolve_service_path(relative_path):
    return (BACKEND_ROOT / relative_path).resolve()


def wait_for_service(service, timeout=STARTUP_WAIT_SECONDS):
    deadline = time.time() + timeout
    while time.time() < deadline:
        process_info = find_process_by_port(service['port'])
        if process_info:
            return process_info
        time.sleep(0.5)
    return None


def read_log_tail(log_path: Path | None, lines: int = TAIL_LOG_LINES) -> str:
    if not log_path:
        return ''
    try:
        if not log_path.exists():
            return f'Log file not found: {log_path}'
        raw = log_path.read_bytes()
        for encoding in ('utf-8', 'utf-8-sig', 'gbk', 'cp936', 'latin-1'):
            try:
                text = raw.decode(encoding)
                return '\n'.join(text.splitlines()[-lines:])
            except UnicodeDecodeError:
                continue
        text = raw.decode('utf-8', errors='replace')
        return '\n'.join(text.splitlines()[-lines:])
    except Exception as e:
        return f'Error reading log file: {str(e)}'


def start_service(service_key: str, wait: bool = True):
    service = get_service_definition(service_key)
    existing = find_process_by_port(service['port'])
    if existing:
        return service_status(service_key, include_log=False)

    cmd = resolve_service_path(service['script'])
    if not cmd.exists():
        raise FileNotFoundError(f"启动脚本不存在: {cmd}")

    subprocess.Popen(
        ['cmd', '/c', str(cmd)],
        cwd=str(cmd.parent),
        # stdout=subprocess.DEVNULL,
        # stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
        shell=False,
        daemon=True
    )
    if wait:
        started_info = wait_for_service(service)
        if not started_info:
            raise TimeoutError(f"{service['name']} did not become ready on port {service['port']} within {STARTUP_WAIT_SECONDS}s.")
    return service_status(service_key)


def check_and_start_services():
    for service in SERVICES:
        process_info = find_process_by_port(service['port'])
        if process_info:
            log.info("%s is already running (PID: %s)", service['name'], process_info['pid'])
        else:
            log.info("%s is not running. Starting it...", service['name'])
            try:
                start_service(service['key'])
            except Exception as e:
                log.error("Failed to start %s: %s", service['name'], e)


def stop_service(service_key: str):
    service = get_service_definition(service_key)
    process_info = find_process_by_port(service['port'])
    if process_info:
        try:
            p = psutil.Process(process_info['pid'])
            p.terminate()
            p.wait(timeout=5)
            return service_status(service_key)
        except Exception as e:
            raise RuntimeError(f"Error terminating {service['name']} (PID: {process_info['pid']}): {str(e)}")
    else:
        return service_status(service_key)


def restart_service(service_key: str):
    stop_service(service_key)
    return start_service(service_key)


def get_log_content(service):
    log_path = resolve_service_path(service['log_file'])
    return read_log_tail(log_path, lines=2000)


def service_status(service_key: str, include_log: bool = True):
    service = get_service_definition(service_key)
    process_info = find_process_by_port(service['port'])
    result = {
        'key': service['key'],
        'name': service['name'],
        'description': service.get('description', ''),
        'is_running': bool(process_info),
        'process_info': process_info,
        'port': service['port'],
        'script': str(resolve_service_path(service['script'])),
        'log_file': str(resolve_service_path(service['log_file']))
    }
    if include_log:
        result['log_tail'] = read_log_tail(resolve_service_path(service['log_file']))
    return result


def list_services(include_log: bool = False):
    return [service_status(service['key'], include_log=include_log) for service in SERVICES]


def print_service_status():
    status = list_services(include_log=False)
    for info in status:
        print(f"{info['name']}: {'Running' if info['is_running'] else 'Not Running'}")
        print(f"  Registered Port: {info['port']}")
        print(f"  Start Script: {info['script']}")
        print(f"  Log File: {info['log_file']}")
        if info['is_running']:
            print(f"  PID: {info['process_info']['pid']}")
            print(f"  Name: {info['process_info']['name']}")
            print(f"  Executable: {info['process_info']['exe']}")
            print(f"  Command Line: {info['process_info']['cmdline']}")


def ensure_core_services_started():
    results = []
    for service_key in ('asr', 'tts'):
        try:
            results.append(start_service(service_key, wait=False))
        except Exception as exc:
            results.append({'key': service_key, 'status': 'error', 'error': str(exc)})
    return results


if __name__ == "__main__":
    for item in list_services(include_log=False):
        print(item)