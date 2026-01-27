"""
Faust 后端服务管理器
"""
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton, QTextEdit, QLabel, QCheckBox
from PyQt5.QtCore import QThread, pyqtSignal
import os
import sys
import subprocess
import threading
import queue
import time
import locale

os.chdir(os.path.dirname(os.path.abspath(__file__)))
# Windows 专用进程标记
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def find_services():
    SERVICES=[('声音识别',"ASR.bat"),
              ('BERT',"BERT.bat"),
              ('人声生成',"TTS.bat"),
              ('图像识别',"OCR.bat"),
                ('后端主服务',"MAIN.bat")
              ]

    return SERVICES


class ProcessReaderThread(QThread):
    new_text = pyqtSignal(str, str)  # service_name, text

    def __init__(self, service_name, stream):
        super().__init__()
        self.service_name = service_name
        self.stream = stream
        self._running = True

    def run(self):
        try:
            while self._running:
                line = self.stream.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                try:
                    # 如果是 bytes，使用系统首选编码（Windows 上通常为 cp936/gbk）解码；
                    # 如果已经是文本字符串，直接使用。
                    if isinstance(line, bytes):
                        enc = locale.getpreferredencoding(False) or 'utf-8'
                        text = line.decode(enc, errors='replace')
                    else:
                        text = str(line)
                except Exception:
                    text = str(line)
                self.new_text.emit(self.service_name, text)
        except Exception as e:
            self.new_text.emit(self.service_name, f"读日志线程出错: {e}\n")

    def stop(self):
        self._running = False
        try:
            self.wait(1000)
        except Exception:
            pass


class BackendManager(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Faust Backend Manager')
        self.resize(900, 600)

        self.services = {}  # name -> (path, proc)
        self.readers = {}   # name -> reader thread
        self.log_buffers = {}  # name -> list of recent log lines
        self.max_buffer_lines = 5000

        self.init_ui()
        self.refresh_services()

    def init_ui(self):
        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        self.list_widget = QListWidget()
        self.refresh_btn = QPushButton('Refresh')
        left.addWidget(QLabel('Detected services in Faust Backend:'))
        left.addWidget(self.list_widget)
        left.addWidget(self.refresh_btn)

        center = QVBoxLayout()
        self.start_btn = QPushButton('Start')
        self.start_all_btn = QPushButton('Start All')
        self.stop_btn = QPushButton('Stop')
        center.addWidget(self.start_btn)
        center.addWidget(self.start_all_btn)
        center.addWidget(self.stop_btn)
        center.addStretch()

        right = QVBoxLayout()
        right.addWidget(QLabel('Service Log:'))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        right.addWidget(self.log_view)

        layout.addLayout(left, 2)
        layout.addLayout(center, 1)
        layout.addLayout(right, 3)

        # signals
        self.refresh_btn.clicked.connect(self.refresh_services)
        self.start_btn.clicked.connect(self.start_selected)
        self.start_all_btn.clicked.connect(self.start_all)
        self.stop_btn.clicked.connect(self.stop_selected)
        self.list_widget.currentItemChanged.connect(self.on_selection_changed)

    def refresh_services(self):
        self.list_widget.clear()
        # 保留已经运行的进程引用，如果名称相同
        found = find_services()
        new_services = {}
        for name, path in found:
            old = self.services.get(name)
            if old and old[1]:
                # keep running process
                new_services[name] = (path, old[1])
            else:
                new_services[name] = (path, None)
            self.list_widget.addItem(name)
            # ensure buffer exists
            self.log_buffers.setdefault(name, [])
        self.services = new_services
        self.log_view.append(f'已检测到 {len(found)} 个服务')

    def on_selection_changed(self, item):
        if not item:
            return
        name = item.text()
        self.show_log_for(name)

    def show_log_for(self, name):
        self.log_view.clear()
        # no persistent logs saved; if process running, we could have buffer
        self.log_view.append(f'日志：{name}\n')
        # 显示 buffer 中的历史日志
        buf = self.log_buffers.get(name, [])
        if buf:
            # 显示最近的若干条（缓冲内已限制长度）
            for line in buf:
                self.log_view.append(line)

    def start_selected(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        name = item.text()
        path, proc = self.services.get(name, (None, None))
        if not path:
            return
        if proc and proc.poll() is None:
            self.log_view.append(f'{name} 已在运行（PID={proc.pid}）')
            return

        # delegate to start_service
        self.start_service(name, path)

    def start_service(self, name, path):
        """Start a single service by name/path and attach a reader buffer."""
        # refresh path/proc tuple
        path, proc = self.services.get(name, (path, None))
        if proc and proc.poll() is None:
            self.log_view.append(f'{name} 已在运行（PID={proc.pid}）')
            return

        cmd = path

        cwd = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()
        enc = locale.getpreferredencoding(False) or 'utf-8'

        if not cmd:
            self.log_view.append('无法决策启动命令')
            return

        run_detached = False
        try:
            if isinstance(cmd, list):
                if run_detached:
                    proc = subprocess.Popen(cmd, cwd=cwd, creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding=enc, errors='replace')
                else:
                    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding=enc, errors='replace')
            else:
                if run_detached:
                    proc = subprocess.Popen(cmd, cwd=cwd, shell=True, creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding=enc, errors='replace')
                else:
                    proc = subprocess.Popen(cmd, cwd=cwd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding=enc, errors='replace')

            # store process
            self.services[name] = (path, proc)
            # ensure buffer exists
            self.log_buffers.setdefault(name, [])
            msg = f'已启动 {name} (PID={proc.pid})'
            self.log_view.append(msg)
            # also store manager messages in buffer
            self.log_buffers.setdefault(name, []).append(msg)

            # 启动读取线程
            reader = ProcessReaderThread(name, proc.stdout)
            reader.new_text.connect(self.on_new_process_text)
            reader.start()
            self.readers[name] = reader

        except Exception as e:
            msg = f'启动失败: {e}'
            self.log_view.append(msg)
            self.log_buffers.setdefault(name, []).append(msg)

    def stop_selected(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        name = item.text()
        path, proc = self.services.get(name, (None, None))
        if not proc:
            msg = f'{name} 未在运行'
            self.log_view.append(msg)
            self.log_buffers.setdefault(name, []).append(msg)
            return
        try:
            # 尝试优雅终止
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            msg = f'{name} 已停止'
            self.log_view.append(msg)
            self.log_buffers.setdefault(name, []).append(msg)
        except Exception as e:
            msg = f'停止失败: {e}'
            self.log_view.append(msg)
            self.log_buffers.setdefault(name, []).append(msg)
        finally:
            # stop reader thread
            reader = self.readers.get(name)
            if reader:
                reader.stop()
                del self.readers[name]
            self.services[name] = (path, None)
            # mark in buffer
            self.log_buffers.setdefault(name, []).append(f'Service marked stopped by manager')

    def on_new_process_text(self, service_name, text):
        # 始终将日志写入对应服务的缓冲
        buf = self.log_buffers.setdefault(service_name, [])
        buf.append(text)
        # limit buffer size
        if len(buf) > self.max_buffer_lines:
            del buf[0:len(buf) - self.max_buffer_lines]

        # 如果当前选中该服务，则显示到 UI
        cur = self.list_widget.currentItem()
        if cur and cur.text() == service_name:
            # 保持自动滚动行为
            self.log_view.append(text)

    def start_all(self):
        """Start all detected services (按列表顺序)。"""
        for name in list(self.services.keys()):
            path, proc = self.services.get(name, (None, None))
            # 启动尚未运行的服务
            if not proc or proc.poll() is not None:
                self.start_service(name, path)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = BackendManager()
    w.show()
    sys.exit(app.exec_())
