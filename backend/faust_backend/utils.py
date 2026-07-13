import functools
import subprocess
import sys
import time
def show_return_wrapper(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        result = await func(*args, **kwargs)
        print(f"Returning from {func.__name__}:", result)
        return result
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

class PerfTimer:

    def __init__(self, names=None):
        self._timers = {}
        self._active = {}
        self._order = names or []

    def begin(self, name):
        if name not in self._order:
            self._order.append(name)
        self._active[name] = time.perf_counter()

    def end(self, name):
        t = time.perf_counter()
        start = self._active.pop(name, None)
        delta = t - start if start is not None else 0
        self._timers[name] = self._timers.get(name, 0) + delta
        return delta

    def drain(self):
        for name in list(self._active.keys()):
            self.end(name)

    def get(self, name):
        return self._timers.get(name, 0)

    def total(self):
        return sum(self._timers.values())

    def itemize(self):
        items = []
        total = self.total()
        for name in self._order:
            ms = self._timers.get(name, 0) * 1000
            items.append(f"{name}:{ms:.0f}ms")
        items.append(f"= {total * 1000:.0f}ms")
        return " ".join(items)

    def print_pref(self, extra=""):
        line = self.itemize()
        if extra:
            line = f"{line} | {extra}"
        print(line)
        self.reset(keep_order=True)

    def __str__(self):
        line = self.itemize()
        if extra:
            line = f"{line} | {extra}"
        self.reset(keep_order=True)
        return line
    
    def reset(self, keep_order=True):
        self._timers.clear()
        self._active.clear()
        if not keep_order:
            self._order.clear()
