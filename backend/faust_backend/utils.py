import functools
import inspect
import subprocess
import sys
def show_return_wrapper(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        result = await func(*args, **kwargs)
        print(f"Returning from {func.__name__}:", result)
        return result
    try:
        wrapper.__signature__ = inspect.signature(func)
    except Exception:
        pass
    return wrapper

class CrossPlatformClipboard:
    def __init__(self):
        self.system = sys.platform

    def copy(self, text):
        """跨平台复制文本到剪切板"""
        if self.system == "win32":
            try:
                import pyperclip
                pyperclip.copy(text)
            except ImportError:
                # 使用Windows命令行工具
                subprocess.run(['clip'], input=text, text=True, check=True)
        elif self.system == "darwin":  # macOS
            subprocess.run(['pbcopy'], input=text, text=True, check=True)
        elif self.system.startswith("linux"):  # Linux
            try:
                subprocess.run(['xclip', '-selection', 'clipboard'], 
                             input=text, text=True, check=True)
            except FileNotFoundError:
                subprocess.run(['xsel', '--clipboard', '--input'], 
                             input=text, text=True, check=True)

    def paste(self):
        """跨平台从剪切板粘贴文本"""
        if self.system == "win32":
            try:
                import pyperclip
                return pyperclip.paste()
            except ImportError:
                # 使用PowerShell
                result = subprocess.run(['powershell', '-command', 'Get-Clipboard'], 
                                      capture_output=True, text=True)
                return result.stdout.strip()
        elif self.system == "darwin":  # macOS
            result = subprocess.run(['pbpaste'], capture_output=True, text=True)
            return result.stdout
        elif self.system.startswith("linux"):  # Linux
            try:
                result = subprocess.run(['xclip', '-selection', 'clipboard', '-o'], 
                                      capture_output=True, text=True)
                return result.stdout
            except FileNotFoundError:
                result = subprocess.run(['xsel', '--clipboard', '--output'], 
                                      capture_output=True, text=True)
                return result.stdout
