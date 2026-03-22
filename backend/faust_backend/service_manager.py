import psutil
import subprocess
import time
from pathlib import Path
import os

BASE_DIR = Path(__file__).parent
STARTUP_WAIT_SECONDS = 15

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


services = [
    {
        'name': 'TTS Service',
        'port': 5000,
        'cmd_line': "../TTS.bat",
        'log_file': "../tts-hub/GPT-SoVITS-Bundle/log_tts.log"
    },
    {
        'name': 'ASR Service',
        'port': 1000,
        'cmd_line': "../ASR.bat",
        'log_file': "../log_asr.log"
    },
    {
        'name': 'mc-operator',
        'port': 18901,
        'cmd_line': "../minecraft/mc-operator/mc.bat",
        'log_file': "../minecraft/mc-operator/log.log"
    }
]


def resolve_service_path(relative_path):
    return (BASE_DIR / relative_path).resolve()


def wait_for_service(service, timeout=STARTUP_WAIT_SECONDS):
    deadline = time.time() + timeout
    while time.time() < deadline:
        process_info = find_process_by_port(service['port'])
        if process_info:
            return process_info
        time.sleep(0.5)
    return None


def start_service(service):
    cmd = resolve_service_path(service['cmd_line'])
    if not cmd.exists():
        raise FileNotFoundError(f"启动脚本不存在: {cmd}")

    creationflags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
    subprocess.Popen(
        ['cmd', '/c', str(cmd)],
        cwd=str(cmd.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        shell=False,
    )


def check_and_start_services():
    for service in services:
        process_info = find_process_by_port(service['port'])
        if process_info:
            print(f"{service['name']} is already running (PID: {process_info['pid']}, Name: {process_info['name']})")
        else:
            print(f"{service['name']} is not running. Starting it...")
            try:
                start_service(service)
            except Exception as e:
                print(f"Failed to start {service['name']}: {e}")
                continue

            started_info = wait_for_service(service)
            if started_info:
                print(f"{service['name']} started successfully (PID: {started_info['pid']}, Name: {started_info['name']})")
            else:
                print(f"{service['name']} did not become ready on port {service['port']} within {STARTUP_WAIT_SECONDS}s.")


def kill_service(service):
    process_info = find_process_by_port(service['port'])
    if process_info:
        try:
            p = psutil.Process(process_info['pid'])
            p.terminate()
            p.wait(timeout=5)
            print(f"{service['name']} (PID: {process_info['pid']}) has been terminated.")
        except Exception as e:
            print(f"Error terminating {service['name']} (PID: {process_info['pid']}): {str(e)}")
    else:
        print(f"{service['name']} is not running.")


def get_log_content(service):
    log_path = resolve_service_path(service['log_file'])
    try:
        if not log_path.exists():
            return f"Log file not found: {log_path}"

        raw = log_path.read_bytes()
        for encoding in ('utf-8', 'utf-8-sig', 'gbk', 'cp936', 'latin-1'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', errors='replace')
    except Exception as e:
        return f"Error reading log file: {str(e)}"


def get_service_status():
    status = {}
    for service in services:
        process_info = find_process_by_port(service['port'])
        status[service['name']] = {
            'is_running': bool(process_info),
            'process_info': process_info,
            'port': service['port'],
            'cmd_line': str(resolve_service_path(service['cmd_line'])),
            'log_file': str(resolve_service_path(service['log_file']))
        }
    return status


def print_service_status():
    status = get_service_status()
    for service_name, info in status.items():
        print(f"{service_name}: {'Running' if info['is_running'] else 'Not Running'}")
        print(f"  Registered Port: {info['port']}")
        print(f"  Start Script: {info['cmd_line']}")
        print(f"  Log File: {info['log_file']}")
        if info['is_running']:
            print(f"  PID: {info['process_info']['pid']}")
            print(f"  Name: {info['process_info']['name']}")
            print(f"  Executable: {info['process_info']['exe']}")
            print(f"  Command Line: {info['process_info']['cmdline']}")


def demo():
    print_service_status()
    check_and_start_services()
    print_service_status()
    os.system("pause")
    # kill all
    for service in services:
        kill_service(service)
    print_service_status()
    print(get_log_content(services[0]))
    print(get_log_content(services[1]))
    print(get_log_content(services[2]))


if __name__ == "__main__":
    demo()