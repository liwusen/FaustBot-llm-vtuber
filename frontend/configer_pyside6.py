import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional
import os
import requests
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)
from pathlib import Path
os.chdir(os.path.dirname(os.path.abspath(__file__)))
API_BASE = "http://127.0.0.1:13900"
TIME_DISPLAY = "yyyy-MM-dd HH:mm:ss"

PUBLIC_PROVIDER_KEYS = [
    "GUI_OPERATOR_LLM_MODEL",
    "GUI_OPERATOR_LLM_BASE",
    "SECURITY_VERIFIER_API_ENDPOINT",
    "SECURITY_VERIFIER_LLM_MODEL",
    "SECURITY_SYS_ENABLED",
    "RAG_ENABLED",
    "MC_OPERATOR_URL",
    "MC_EVENT_TRIGGER_ENABLED",
]
PRIVATE_PROVIDER_KEYS = [
    "DEEPSEEK_API_KEY",
    "SEARCH_API_KEY",
    "GUI_OPERATOR_LLM_KEY",
    "SECURITY_VERIFIER_LLM_KEY",
    "RAG_OPENAI_API_KEY",
]
LIVE2D_KEYS = [
    "LIVE2D_MODEL_PATH",
    "LIVE2D_MODEL_SCALE",
    "LIVE2D_MODEL_X",
    "LIVE2D_MODEL_Y",
    "FRONTEND_CLICK_THROUGH",
    "FRONTEND_DEFAULT_TTS_LANG",
]
AGENT_FILES = ["AGENT.md", "ROLE.md", "COREMEMORY.md", "TASK.md"]


@dataclass
class FieldWidget:
    key: str
    widget: QWidget
    value_type: str


class ApiError(Exception):
    pass


class RagDetailDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RAG 详情")
        self.resize(860, 620)

        root = QVBoxLayout(self)
        self.meta_view = QPlainTextEdit()
        self.meta_view.setReadOnly(True)
        self.content_edit = QPlainTextEdit()

        root.addWidget(QLabel("元信息"))
        root.addWidget(self.meta_view, 2)
        root.addWidget(QLabel("正文"))
        root.addWidget(self.content_edit, 3)

        btn_row = QHBoxLayout()
        self.close_btn = QPushButton("关闭")
        self.delete_btn = QPushButton("删除")
        self.save_btn = QPushButton("保存")
        btn_row.addStretch(1)
        btn_row.addWidget(self.close_btn)
        btn_row.addWidget(self.delete_btn)
        btn_row.addWidget(self.save_btn)
        root.addLayout(btn_row)


class ConfigerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Faust Configer (PySide6)")
        self.resize(1300, 900)

        self.state: Dict[str, Any] = {
            "config": {},
            "runtime": {},
            "agent_detail": None,
            "selected_agent": None,
            "services": [],
            "selected_service": None,
            "rag": {
                "page": 1,
                "page_size": 10,
                "total": 0,
                "total_pages": 1,
                "search": "",
                "time_from": "",
                "time_to": "",
                "items": [],
                "detail": None,
            },
            "plugins": {
                "items": [],
                "selected_id": None,
                "hot_reload": {},
            },
        }

        self.public_fields: Dict[str, FieldWidget] = {}
        self.private_fields: Dict[str, FieldWidget] = {}
        self.live2d_fields: Dict[str, FieldWidget] = {}
        self.agent_file_edits: Dict[str, QPlainTextEdit] = {}

        self._build_ui()
        self.refresh_all()

    # ---------- API ----------
    def api_request(self, method: str, path: str, payload: Optional[dict] = None, params: Optional[dict] = None):
        url = f"{API_BASE}{path}"
        try:
            resp = requests.request(method=method, url=url, json=payload, params=params, timeout=30)
        except requests.RequestException as e:
            raise ApiError(f"网络请求失败: {e}") from e

        try:
            data = resp.json()
        except Exception:
            data = {}

        if not resp.ok:
            detail = data.get("detail") if isinstance(data, dict) else None
            raise ApiError(detail or f"HTTP {resp.status_code}")

        if isinstance(data, dict) and data.get("error"):
            raise ApiError(str(data.get("error")))

        return data

    # ---------- UI ----------
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        top = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh_all)
        self.save_btn = QPushButton("保存配置")
        self.save_btn.clicked.connect(self.save_config)
        top.addWidget(self.refresh_btn)
        top.addWidget(self.save_btn)
        top.addStretch(1)
        main.addLayout(top)

        self.tabs = QTabWidget()
        main.addWidget(self.tabs, 1)

        self._build_overview_tab()
        self._build_provider_tab()
        self._build_agent_tab()
        self._build_live2d_tab()
        self._build_rag_tab()
        self._build_runtime_tab()
        self._build_plugins_tab()

        status = QStatusBar()
        self.setStatusBar(status)

    def _build_overview_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        cards = QHBoxLayout()
        self.current_agent_label = QLabel("当前 Agent: -")
        self.current_model_label = QLabel("默认模型: -")
        cards.addWidget(self.current_agent_label)
        cards.addWidget(self.current_model_label)
        cards.addStretch(1)
        layout.addLayout(cards)

        self.runtime_summary_view = QPlainTextEdit()
        self.runtime_summary_view.setReadOnly(True)
        layout.addWidget(self.runtime_summary_view, 1)

        self.tabs.addTab(tab, "概览")

    def _build_provider_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)

        self.public_box = QGroupBox("公开配置")
        self.public_form = QFormLayout(self.public_box)

        self.private_box = QGroupBox("API Keys")
        self.private_form = QFormLayout(self.private_box)

        layout.addWidget(self.public_box, 1)
        layout.addWidget(self.private_box, 1)
        self.tabs.addTab(tab, "AI Provider")

    def _build_live2d_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("Live2D 配置")
        self.live2d_form = QFormLayout(group)
        layout.addWidget(group)

        self.model_list = QListWidget()
        layout.addWidget(QLabel("可用模型"))
        layout.addWidget(self.model_list, 1)

        self.model_list.itemDoubleClicked.connect(self._apply_selected_model_path)
        self.tabs.addTab(tab, "Live2D")

    def _build_agent_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)

        left = QVBoxLayout()
        self.agent_list = QListWidget()
        self.agent_list.itemSelectionChanged.connect(self._on_agent_selected)
        left.addWidget(self.agent_list, 1)

        left_btn = QHBoxLayout()
        self.agent_create_btn = QPushButton("新建")
        self.agent_delete_btn = QPushButton("删除")
        self.agent_switch_btn = QPushButton("切换为当前")
        left_btn.addWidget(self.agent_create_btn)
        left_btn.addWidget(self.agent_delete_btn)
        left_btn.addWidget(self.agent_switch_btn)
        left.addLayout(left_btn)

        self.agent_create_btn.clicked.connect(self.create_agent)
        self.agent_delete_btn.clicked.connect(self.delete_agent)
        self.agent_switch_btn.clicked.connect(self.switch_agent)

        right = QVBoxLayout()
        self.agent_editor_tabs = QTabWidget()
        for name in AGENT_FILES:
            editor = QPlainTextEdit()
            self.agent_file_edits[name] = editor
            self.agent_editor_tabs.addTab(editor, name)
        right.addWidget(self.agent_editor_tabs, 1)

        self.agent_save_btn = QPushButton("保存 Agent 文件")
        self.agent_save_btn.clicked.connect(self.save_agent_files)
        self.open_in_default_btn = QPushButton("在默认编辑器中打开")
        self.open_in_default_btn.clicked.connect(self.open_agent_in_default_editor)
        self.del_agent_checkpoint_btn = QPushButton("删除 Agent Checkpoint(对话上下文重置)")
        self.del_agent_checkpoint_btn.clicked.connect(self.del_agent_checkpoint)
        right.addWidget(self.agent_save_btn)
        right.addWidget(self.open_in_default_btn)
        right.addWidget(self.del_agent_checkpoint_btn)

        split = QSplitter(Qt.Horizontal)
        left_holder = QWidget()
        left_holder.setLayout(left)
        right_holder = QWidget()
        right_holder.setLayout(right)
        split.addWidget(left_holder)
        split.addWidget(right_holder)
        split.setSizes([300, 900])

        layout.addWidget(split, 1)
        self.tabs.addTab(tab, "Agent 管理")

    def _build_runtime_tab(self):
        tab = QWidget()
        layout = QGridLayout(tab)

        self.reload_agent_btn = QPushButton("重建 Agent Runtime")
        self.reload_all_btn = QPushButton("重载配置并重建 Runtime")
        self.reload_agent_btn.clicked.connect(self.reload_agent_runtime)
        self.reload_all_btn.clicked.connect(self.reload_all_runtime)
        layout.addWidget(self.reload_agent_btn, 0, 0)
        layout.addWidget(self.reload_all_btn, 0, 1)

        self.service_list = QListWidget()
        self.service_list.itemSelectionChanged.connect(self._on_service_selected)
        layout.addWidget(QLabel("服务列表"), 1, 0)
        layout.addWidget(self.service_list, 2, 0)

        srv_btn = QHBoxLayout()
        self.service_start_btn = QPushButton("启动")
        self.service_stop_btn = QPushButton("停止")
        self.service_restart_btn = QPushButton("重启")
        self.service_start_btn.clicked.connect(lambda: self.service_action("start"))
        self.service_stop_btn.clicked.connect(lambda: self.service_action("stop"))
        self.service_restart_btn.clicked.connect(lambda: self.service_action("restart"))
        srv_btn.addWidget(self.service_start_btn)
        srv_btn.addWidget(self.service_stop_btn)
        srv_btn.addWidget(self.service_restart_btn)
        srv_wrap = QWidget()
        srv_wrap.setLayout(srv_btn)
        layout.addWidget(srv_wrap, 3, 0)

        self.service_log = QPlainTextEdit()
        self.service_log.setReadOnly(True)
        layout.addWidget(QLabel("服务日志"), 1, 1)
        layout.addWidget(self.service_log, 2, 1, 2, 1)

        self.tabs.addTab(tab, "运行控制")

    def _build_plugins_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)

        left = QVBoxLayout()
        self.plugin_list = QListWidget()
        self.plugin_list.itemSelectionChanged.connect(self._on_plugin_selected)
        left.addWidget(QLabel("插件列表"))
        left.addWidget(self.plugin_list, 1)

        top_btn = QHBoxLayout()
        self.plugin_refresh_btn = QPushButton("刷新")
        self.plugin_reload_btn = QPushButton("重载插件")
        self.plugin_enable_btn = QPushButton("启用插件")
        self.plugin_disable_btn = QPushButton("禁用插件")
        top_btn.addWidget(self.plugin_refresh_btn)
        top_btn.addWidget(self.plugin_reload_btn)
        top_btn.addWidget(self.plugin_enable_btn)
        top_btn.addWidget(self.plugin_disable_btn)
        left.addLayout(top_btn)

        hot_btn = QHBoxLayout()
        self.plugin_hot_reload_start_btn = QPushButton("开启热重载")
        self.plugin_hot_reload_stop_btn = QPushButton("关闭热重载")
        self.plugin_hot_reload_status = QLabel("热重载: -")
        hot_btn.addWidget(self.plugin_hot_reload_start_btn)
        hot_btn.addWidget(self.plugin_hot_reload_stop_btn)
        hot_btn.addWidget(self.plugin_hot_reload_status)
        left.addLayout(hot_btn)

        self.plugin_refresh_btn.clicked.connect(self.load_plugins)
        self.plugin_reload_btn.clicked.connect(self.reload_plugins)
        self.plugin_enable_btn.clicked.connect(lambda: self.set_plugin_enabled(True))
        self.plugin_disable_btn.clicked.connect(lambda: self.set_plugin_enabled(False))
        self.plugin_hot_reload_start_btn.clicked.connect(self.start_plugin_hot_reload)
        self.plugin_hot_reload_stop_btn.clicked.connect(self.stop_plugin_hot_reload)

        right = QVBoxLayout()
        self.plugin_meta_view = QPlainTextEdit()
        self.plugin_meta_view.setReadOnly(True)
        right.addWidget(QLabel("插件详情"))
        right.addWidget(self.plugin_meta_view, 2)

        self.plugin_tools_list = QListWidget()
        tool_btn = QHBoxLayout()
        self.plugin_tool_enable_btn = QPushButton("启用 Tool")
        self.plugin_tool_disable_btn = QPushButton("禁用 Tool")
        tool_btn.addWidget(self.plugin_tool_enable_btn)
        tool_btn.addWidget(self.plugin_tool_disable_btn)
        right.addWidget(QLabel("Tools"))
        right.addWidget(self.plugin_tools_list, 1)
        right.addLayout(tool_btn)

        self.plugin_tool_enable_btn.clicked.connect(lambda: self.set_plugin_tool_enabled(True))
        self.plugin_tool_disable_btn.clicked.connect(lambda: self.set_plugin_tool_enabled(False))

        self.plugin_middlewares_list = QListWidget()
        mw_btn = QHBoxLayout()
        self.plugin_mw_enable_btn = QPushButton("启用 Middleware")
        self.plugin_mw_disable_btn = QPushButton("禁用 Middleware")
        mw_btn.addWidget(self.plugin_mw_enable_btn)
        mw_btn.addWidget(self.plugin_mw_disable_btn)
        right.addWidget(QLabel("Middlewares"))
        right.addWidget(self.plugin_middlewares_list, 1)
        right.addLayout(mw_btn)

        self.plugin_mw_enable_btn.clicked.connect(lambda: self.set_plugin_middleware_enabled(True))
        self.plugin_mw_disable_btn.clicked.connect(lambda: self.set_plugin_middleware_enabled(False))

        trig_btn = QHBoxLayout()
        self.plugin_trigger_enable_btn = QPushButton("启用 Trigger 控制")
        self.plugin_trigger_disable_btn = QPushButton("禁用 Trigger 控制")
        trig_btn.addWidget(self.plugin_trigger_enable_btn)
        trig_btn.addWidget(self.plugin_trigger_disable_btn)
        right.addLayout(trig_btn)
        self.plugin_trigger_enable_btn.clicked.connect(lambda: self.set_plugin_trigger_control_enabled(True))
        self.plugin_trigger_disable_btn.clicked.connect(lambda: self.set_plugin_trigger_control_enabled(False))

        split = QSplitter(Qt.Horizontal)
        left_holder = QWidget()
        left_holder.setLayout(left)
        right_holder = QWidget()
        right_holder.setLayout(right)
        split.addWidget(left_holder)
        split.addWidget(right_holder)
        split.setSizes([380, 920])

        layout.addWidget(split, 1)
        self.tabs.addTab(tab, "插件管理")

    def _build_rag_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        filter_row = QHBoxLayout()
        self.rag_search_input = QLineEdit()
        self.rag_search_input.setPlaceholderText("关键词")
        self.rag_time_from = QDateTimeEdit()
        self.rag_time_to = QDateTimeEdit()
        self.rag_time_from.setDisplayFormat(TIME_DISPLAY)
        self.rag_time_to.setDisplayFormat(TIME_DISPLAY)
        self.rag_time_from.setSpecialValueText("不限")
        self.rag_time_to.setSpecialValueText("不限")
        self.rag_time_from.clear()
        self.rag_time_to.clear()

        self.rag_page_size = QComboBox()
        self.rag_page_size.addItems(["10", "20", "50"])

        self.rag_search_btn = QPushButton("搜索")
        self.rag_reset_btn = QPushButton("重置")
        self.rag_search_btn.clicked.connect(lambda: self.load_rag_documents(reset_page=True))
        self.rag_reset_btn.clicked.connect(self.reset_rag_filters)

        filter_row.addWidget(QLabel("搜索"))
        filter_row.addWidget(self.rag_search_input, 2)
        filter_row.addWidget(QLabel("开始"))
        filter_row.addWidget(self.rag_time_from)
        filter_row.addWidget(QLabel("结束"))
        filter_row.addWidget(self.rag_time_to)
        filter_row.addWidget(QLabel("每页"))
        filter_row.addWidget(self.rag_page_size)
        filter_row.addWidget(self.rag_search_btn)
        filter_row.addWidget(self.rag_reset_btn)
        layout.addLayout(filter_row)

        self.rag_table = QTableWidget(0, 5)
        self.rag_table.setHorizontalHeaderLabels(["doc_id", "status", "时间", "chunks", "file_path"])
        self.rag_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rag_table.setSelectionMode(QTableWidget.SingleSelection)
        self.rag_table.itemDoubleClicked.connect(lambda _: self.open_rag_detail())
        layout.addWidget(self.rag_table, 1)

        pager = QHBoxLayout()
        self.rag_page_info = QLabel("-")
        self.rag_prev_btn = QPushButton("上一页")
        self.rag_next_btn = QPushButton("下一页")
        self.rag_open_detail_btn = QPushButton("详情")
        self.rag_delete_btn = QPushButton("删除")
        self.rag_prev_btn.clicked.connect(self.rag_prev_page)
        self.rag_next_btn.clicked.connect(self.rag_next_page)
        self.rag_open_detail_btn.clicked.connect(self.open_rag_detail)
        self.rag_delete_btn.clicked.connect(self.delete_selected_rag)

        pager.addWidget(self.rag_page_info)
        pager.addStretch(1)
        pager.addWidget(self.rag_prev_btn)
        pager.addWidget(self.rag_next_btn)
        pager.addWidget(self.rag_open_detail_btn)
        pager.addWidget(self.rag_delete_btn)
        layout.addLayout(pager)

        self.tabs.addTab(tab, "RAG 记忆库")

        self.rag_detail_dialog = RagDetailDialog(self)
        self.rag_detail_dialog.close_btn.clicked.connect(self.rag_detail_dialog.close)
        self.rag_detail_dialog.save_btn.clicked.connect(self.save_rag_detail)
        self.rag_detail_dialog.delete_btn.clicked.connect(self.delete_rag_detail)

    # ---------- Helpers ----------
    def notify(self, text: str):
        self.statusBar().showMessage(text, 5000)

    def fail(self, title: str, err: Exception | str):
        msg = str(err)
        self.notify(msg)
        QMessageBox.critical(self, title, msg)

    def _clear_form_layout(self, form: QFormLayout):
        while form.count():
            item = form.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _widget_from_value(self, key: str, value: Any) -> FieldWidget:
        value_type = type(value).__name__
        if isinstance(value, bool):
            w = QComboBox()
            w.addItems(["true", "false"])
            w.setCurrentText("true" if value else "false")
            return FieldWidget(key=key, widget=w, value_type=value_type)

        if isinstance(value, str) and len(value) > 80:
            w = QPlainTextEdit(value)
            w.setFixedHeight(90)
            return FieldWidget(key=key, widget=w, value_type=value_type)

        w = QLineEdit("" if value is None else str(value))
        if any(token in key.upper() for token in ["KEY", "TOKEN", "SECRET", "PASSWORD"]):
            w.setEchoMode(QLineEdit.Password)
        return FieldWidget(key=key, widget=w, value_type=value_type)

    def _field_value(self, field: FieldWidget):
        w = field.widget
        if isinstance(w, QComboBox):
            text = w.currentText()
        elif isinstance(w, QPlainTextEdit):
            text = w.toPlainText()
        elif isinstance(w, QLineEdit):
            text = w.text()
        else:
            text = ""

        if field.value_type == "bool":
            return text == "true"
        if field.value_type == "int":
            return int(text) if text != "" else 0
        if field.value_type == "float":
            return float(text) if text != "" else 0.0
        return text

    # ---------- Load / Render ----------
    def load_config_view(self):
        data = self.api_request("GET", "/faust/admin/config")
        self.state["config"] = data

        public_cfg = (data or {}).get("public", {})
        private_cfg = (data or {}).get("private", {})

        self._clear_form_layout(self.public_form)
        self._clear_form_layout(self.private_form)
        self._clear_form_layout(self.live2d_form)
        self.public_fields.clear()
        self.private_fields.clear()
        self.live2d_fields.clear()

        for key in PUBLIC_PROVIDER_KEYS:
            field = self._widget_from_value(key, public_cfg.get(key))
            self.public_fields[key] = field
            self.public_form.addRow(QLabel(key), field.widget)

        for key in PRIVATE_PROVIDER_KEYS:
            field = self._widget_from_value(key, private_cfg.get(key))
            self.private_fields[key] = field
            self.private_form.addRow(QLabel(key), field.widget)

        for key in LIVE2D_KEYS:
            field = self._widget_from_value(key, public_cfg.get(key))
            self.live2d_fields[key] = field
            self.live2d_form.addRow(QLabel(key), field.widget)

    def load_runtime_summary(self):
        data = self.api_request("GET", "/faust/admin/runtime")
        runtime = data.get("runtime") or {}
        self.state["runtime"] = runtime

        self.current_agent_label.setText(f"当前 Agent: {runtime.get('current_agent', '-')}")
        model_path = (runtime.get("public_config") or {}).get("LIVE2D_MODEL_PATH", "-")
        self.current_model_label.setText(f"默认模型: {model_path}")
        self.runtime_summary_view.setPlainText(json.dumps(runtime, ensure_ascii=False, indent=2))

        self.model_list.clear()
        for model in runtime.get("available_models", []) or []:
            self.model_list.addItem(f"{model.get('label', '-')}: {model.get('path', '-')}")

        self.agent_list.clear()
        for agent in runtime.get("agents", []) or []:
            marker = "（当前）" if agent.get("is_current") else ""
            self.agent_list.addItem(f"{agent.get('name', '-')}{marker}")

    def load_services(self):
        data = self.api_request("GET", "/faust/admin/services")
        self.state["services"] = data.get("items") or []
        self.service_list.clear()
        for srv in self.state["services"]:
            status = "运行中" if srv.get("is_running") else "未运行"
            self.service_list.addItem(f"{srv.get('key')} | {srv.get('name')} | {status} | 端口 {srv.get('port')}")

    def load_plugins(self):
        data = self.api_request("GET", "/faust/admin/plugins")
        items = data.get("items") or []
        hot = data.get("hot_reload") or {}

        self.state["plugins"]["items"] = items
        self.state["plugins"]["hot_reload"] = hot

        self.plugin_hot_reload_status.setText(
            f"热重载: {'开启' if hot.get('enabled') else '关闭'} | 轮询 {hot.get('interval_sec', '-') }s"
        )

        selected_id = self.state["plugins"].get("selected_id")
        self.plugin_list.clear()
        row_to_select = -1
        for idx, plugin in enumerate(items):
            pid = str(plugin.get("id") or "")
            enabled = bool(plugin.get("enabled"))
            item = QListWidgetItem(f"[{ 'ON' if enabled else 'OFF' }] {pid}")
            item.setData(Qt.UserRole, pid)
            self.plugin_list.addItem(item)
            if selected_id and selected_id == pid:
                row_to_select = idx

        if row_to_select >= 0:
            self.plugin_list.setCurrentRow(row_to_select)
        elif self.plugin_list.count() > 0:
            self.plugin_list.setCurrentRow(0)

    def _selected_plugin_id(self) -> Optional[str]:
        item = self.plugin_list.currentItem()
        if not item:
            return None
        pid = item.data(Qt.UserRole)
        return str(pid) if pid else None

    def _selected_plugin_record(self) -> Optional[Dict[str, Any]]:
        pid = self._selected_plugin_id()
        if not pid:
            return None
        for plugin in self.state["plugins"].get("items") or []:
            if str(plugin.get("id")) == pid:
                return plugin
        return None

    def _on_plugin_selected(self):
        pid = self._selected_plugin_id()
        self.state["plugins"]["selected_id"] = pid
        plugin = self._selected_plugin_record()
        if not plugin:
            self.plugin_meta_view.setPlainText("")
            self.plugin_tools_list.clear()
            self.plugin_middlewares_list.clear()
            return

        meta = dict(plugin)
        meta.pop("tools", None)
        meta.pop("middlewares", None)
        self.plugin_meta_view.setPlainText(json.dumps(meta, ensure_ascii=False, indent=2))

        self.plugin_tools_list.clear()
        for tool in plugin.get("tools") or []:
            name = str(tool.get("name") or "")
            enabled = bool(tool.get("enabled"))
            item = QListWidgetItem(f"[{ 'ON' if enabled else 'OFF' }] {name}")
            item.setData(Qt.UserRole, name)
            self.plugin_tools_list.addItem(item)

        self.plugin_middlewares_list.clear()
        for mw in plugin.get("middlewares") or []:
            name = str(mw.get("name") or "")
            enabled = bool(mw.get("enabled"))
            prio = mw.get("priority")
            item = QListWidgetItem(f"[{ 'ON' if enabled else 'OFF' }] {name} (prio={prio})")
            item.setData(Qt.UserRole, name)
            self.plugin_middlewares_list.addItem(item)

    def reload_plugins(self):
        try:
            self.api_request("POST", "/faust/admin/plugins/reload", payload={"apply_runtime": True})
            self.load_plugins()
            self.notify("插件已重载并应用到运行时")
        except Exception as e:
            self.fail("重载插件失败", e)

    def set_plugin_enabled(self, enabled: bool):
        pid = self._selected_plugin_id()
        if not pid:
            return
        action = "enable" if enabled else "disable"
        try:
            self.api_request("POST", f"/faust/admin/plugins/{requests.utils.quote(pid, safe='')}/{action}", payload={"apply_runtime": True})
            self.load_plugins()
            self.notify(f"插件 {pid} 已{'启用' if enabled else '禁用'}")
        except Exception as e:
            self.fail("插件开关失败", e)

    def start_plugin_hot_reload(self):
        try:
            self.api_request("POST", "/faust/admin/plugins/hot-reload/start", payload={"interval_sec": 2.0})
            self.load_plugins()
            self.notify("插件热重载已开启")
        except Exception as e:
            self.fail("开启热重载失败", e)

    def stop_plugin_hot_reload(self):
        try:
            self.api_request("POST", "/faust/admin/plugins/hot-reload/stop")
            self.load_plugins()
            self.notify("插件热重载已关闭")
        except Exception as e:
            self.fail("关闭热重载失败", e)

    def set_plugin_tool_enabled(self, enabled: bool):
        pid = self._selected_plugin_id()
        item = self.plugin_tools_list.currentItem()
        if not pid or not item:
            return
        tool_name = item.data(Qt.UserRole)
        if not tool_name:
            return
        action = "enable" if enabled else "disable"
        try:
            self.api_request(
                "POST",
                f"/faust/admin/plugins/{requests.utils.quote(pid, safe='')}/tools/{requests.utils.quote(str(tool_name), safe='')}/{action}",
                payload={"apply_runtime": True},
            )
            self.load_plugins()
            self.notify(f"Tool {tool_name} 已{'启用' if enabled else '禁用'}")
        except Exception as e:
            self.fail("Tool 开关失败", e)

    def set_plugin_middleware_enabled(self, enabled: bool):
        pid = self._selected_plugin_id()
        item = self.plugin_middlewares_list.currentItem()
        if not pid or not item:
            return
        mw_name = item.data(Qt.UserRole)
        if not mw_name:
            return
        action = "enable" if enabled else "disable"
        try:
            self.api_request(
                "POST",
                f"/faust/admin/plugins/{requests.utils.quote(pid, safe='')}/middlewares/{requests.utils.quote(str(mw_name), safe='')}/{action}",
                payload={"apply_runtime": True},
            )
            self.load_plugins()
            self.notify(f"Middleware {mw_name} 已{'启用' if enabled else '禁用'}")
        except Exception as e:
            self.fail("Middleware 开关失败", e)

    def set_plugin_trigger_control_enabled(self, enabled: bool):
        pid = self._selected_plugin_id()
        if not pid:
            return
        action = "enable" if enabled else "disable"
        try:
            self.api_request(
                "POST",
                f"/faust/admin/plugins/{requests.utils.quote(pid, safe='')}/trigger-control/{action}",
                payload={"apply_runtime": False},
            )
            self.load_plugins()
            self.notify(f"插件 {pid} Trigger 控制已{'启用' if enabled else '禁用'}")
        except Exception as e:
            self.fail("Trigger 控制开关失败", e)

    def load_rag_documents(self, reset_page: bool = False):
        rag = self.state["rag"]
        if reset_page:
            rag["page"] = 1

        rag["search"] = self.rag_search_input.text().strip()
        rag["page_size"] = int(self.rag_page_size.currentText())
        rag["time_from"] = self.rag_time_from.text().strip()
        rag["time_to"] = self.rag_time_to.text().strip()

        params = {
            "page": rag["page"],
            "page_size": rag["page_size"],
            "search": rag["search"] or None,
            "time_from": rag["time_from"] or None,
            "time_to": rag["time_to"] or None,
        }
        data = self.api_request("GET", "/faust/admin/rag/documents", params=params)

        rag["items"] = data.get("documents") or []
        p = data.get("pagination") or {}
        rag["page"] = int(p.get("page") or rag["page"])
        rag["page_size"] = int(p.get("page_size") or rag["page_size"])
        rag["total"] = int(p.get("total") or 0)
        rag["total_pages"] = int(p.get("total_pages") or 1)

        self.rag_table.setRowCount(len(rag["items"]))
        for i, doc in enumerate(rag["items"]):
            self.rag_table.setItem(i, 0, QTableWidgetItem(str(doc.get("doc_id", ""))))
            self.rag_table.setItem(i, 1, QTableWidgetItem(str(doc.get("status", ""))))
            self.rag_table.setItem(i, 2, QTableWidgetItem(str(doc.get("updated_at") or doc.get("created_at") or "")))
            self.rag_table.setItem(i, 3, QTableWidgetItem(str(doc.get("chunks_count", 0))))
            self.rag_table.setItem(i, 4, QTableWidgetItem(str(doc.get("file_path") or "")))

        self.rag_page_info.setText(
            f"第 {rag['page']}/{max(rag['total_pages'], 1)} 页 · 每页 {rag['page_size']} · 共 {rag['total']} 条"
        )

    def refresh_all(self):
        try:
            self.load_config_view()
            self.load_runtime_summary()
            self.load_services()
            self.load_rag_documents(reset_page=False)
            self.load_plugins()
            self.notify("已刷新配置与运行状态")
        except Exception as e:
            self.fail("刷新失败", e)

    # ---------- Actions ----------
    def save_config(self):
        try:
            public_values = {k: self._field_value(v) for k, v in self.public_fields.items()}
            private_values = {k: self._field_value(v) for k, v in self.private_fields.items()}
            live2d_values = {k: self._field_value(v) for k, v in self.live2d_fields.items()}
            public_values.update(live2d_values)

            self.api_request("POST", "/faust/admin/config", payload={"public": public_values, "private": private_values})
            self.notify("配置已保存")
            self.load_config_view()
            self.load_runtime_summary()
        except Exception as e:
            self.fail("保存配置失败", e)

    def _apply_selected_model_path(self):
        row = self.model_list.currentRow()
        if row < 0:
            return
        text = self.model_list.currentItem().text()
        path = text.split(":", 1)[1].strip() if ":" in text else text
        target = self.live2d_fields.get("LIVE2D_MODEL_PATH")
        if not target:
            return
        w = target.widget
        if isinstance(w, QLineEdit):
            w.setText(path)

    def _selected_agent_name(self) -> Optional[str]:
        item = self.agent_list.currentItem()
        if not item:
            return None
        text = item.text()
        return text.replace("（当前）", "").strip()

    def _on_agent_selected(self):
        name = self._selected_agent_name()
        if not name:
            return
        self.state["selected_agent"] = name
        try:
            data = self.api_request("GET", f"/faust/admin/agents/{requests.utils.quote(name, safe='')}")
            detail = data.get("detail") or {}
            self.state["agent_detail"] = detail
            files = detail.get("files") or {}
            for f in AGENT_FILES:
                self.agent_file_edits[f].setPlainText(files.get(f, ""))
            self.notify(f"已载入 Agent: {name}")
        except Exception as e:
            self.fail("读取 Agent 失败", e)

    def create_agent(self):
        name, ok = QInputDialog.getText(self, "创建 Agent", "请输入 Agent 名称")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            self.api_request("POST", "/faust/admin/agents", payload={"agent_name": name})
            self.load_runtime_summary()
            self.notify(f"已创建 Agent: {name}")
        except Exception as e:
            self.fail("创建 Agent 失败", e)

    def delete_agent(self):
        name = self.state.get("selected_agent")
        if not name:
            return
        if QMessageBox.question(self, "删除 Agent", f"确定删除 {name} 吗？") != QMessageBox.Yes:
            return
        try:
            self.api_request("DELETE", f"/faust/admin/agents/{requests.utils.quote(name, safe='')}")
            self.state["selected_agent"] = None
            self.state["agent_detail"] = None
            self.load_runtime_summary()
            self.notify(f"已删除 Agent: {name}")
        except Exception as e:
            self.fail("删除 Agent 失败", e)

    def switch_agent(self):
        name = self.state.get("selected_agent")
        if not name:
            return
        try:
            self.api_request("POST", "/faust/admin/agents/switch", payload={"agent_name": name})
            self.load_runtime_summary()
            self.refresh_all()
            self.notify(f"已切换 Agent: {name}")
        except Exception as e:
            self.fail("切换 Agent 失败", e)

    def save_agent_files(self):
        name = self.state.get("selected_agent")
        if not name:
            return
        files = {k: self.agent_file_edits[k].toPlainText() for k in AGENT_FILES}
        try:
            self.api_request(
                "PUT",
                f"/faust/admin/agents/{requests.utils.quote(name, safe='')}/files",
                payload={"files": files},
            )
            self.notify(f"Agent 文件已保存: {name}")
        except Exception as e:
            self.fail("保存 Agent 文件失败", e)
    def open_agent_in_default_editor(self):

        name = self.state.get("selected_agent")
        if not name:
            return
        try:
            os.startfile(Path(f"../backend/agents/{name}/AGENT.md").resolve())
            os.startfile(Path(f"../backend/agents/{name}/ROLE.md").resolve())
            os.startfile(Path(f"../backend/agents/{name}/COREMEMORY.md").resolve())
            os.startfile(Path(f"../backend/agents/{name}/TASK.md").resolve())
        except Exception as e:
            self.fail("打开 Agent 失败", e)
    def reload_agent_runtime(self):
        try:
            self.api_request("POST", "/faust/admin/runtime/reload-agent")
            self.load_runtime_summary()
            self.notify("Agent Runtime 已重建")
        except Exception as e:
            self.fail("重建 Agent Runtime 失败", e)

    def reload_all_runtime(self):
        try:
            self.api_request("POST", "/faust/admin/runtime/reload-all")
            self.load_runtime_summary()
            self.notify("运行时已重载")
        except Exception as e:
            self.fail("重载运行时失败", e)

    def _selected_service_key(self) -> Optional[str]:
        item = self.service_list.currentItem()
        if not item:
            return None
        raw = item.text().split("|", 1)[0].strip()
        return raw or None

    def _on_service_selected(self):
        key = self._selected_service_key()
        if not key:
            return
        self.state["selected_service"] = key
        try:
            data = self.api_request(
                "GET",
                f"/faust/admin/services/{requests.utils.quote(key, safe='')}",
                params={"include_log": "true"},
            )
            item = data.get("item") or {}
            self.service_log.setPlainText(item.get("log_tail") or "暂无日志")
        except Exception as e:
            self.fail("读取服务日志失败", e)

    def service_action(self, action: str):
        key = self.state.get("selected_service")
        if not key:
            return
        try:
            self.api_request("POST", f"/faust/admin/services/{requests.utils.quote(key, safe='')}/{action}")
            self.load_services()
            self.notify(f"服务 {key} 已执行 {action}")
        except Exception as e:
            self.fail("服务操作失败", e)

    # ---------- RAG ----------
    def reset_rag_filters(self):
        self.rag_search_input.clear()
        self.rag_page_size.setCurrentText("10")
        self.rag_time_from.clear()
        self.rag_time_to.clear()
        self.state["rag"]["page"] = 1
        try:
            self.load_rag_documents(reset_page=True)
        except Exception as e:
            self.fail("重置 RAG 过滤失败", e)

    def rag_prev_page(self):
        rag = self.state["rag"]
        if rag["page"] <= 1:
            return
        rag["page"] -= 1
        try:
            self.load_rag_documents(reset_page=False)
        except Exception as e:
            self.fail("分页失败", e)

    def rag_next_page(self):
        rag = self.state["rag"]
        if rag["page"] >= max(rag["total_pages"], 1):
            return
        rag["page"] += 1
        try:
            self.load_rag_documents(reset_page=False)
        except Exception as e:
            self.fail("分页失败", e)

    def _current_rag_doc_id(self) -> Optional[str]:
        row = self.rag_table.currentRow()
        if row < 0:
            return None
        item = self.rag_table.item(row, 0)
        return item.text().strip() if item else None

    def del_agent_checkpoint(self):
        name = self.state.get("selected_agent")
        if not name:
            return
        if QMessageBox.question(self, "删除 Agent Checkpoint", f"确定删除 {name} 的 Agent Checkpoint 吗？这将导致对话上下文重置。") != QMessageBox.Yes:
            return
        try:
            self.api_request("DELETE", f"/faust/admin/agents/{requests.utils.quote(name, safe='')}/checkpoint")
            self.notify(f"{name} 的 Agent Checkpoint 已删除，对话上下文已重置")
        except Exception as e:
            self.fail("删除 Agent Checkpoint 失败", e)
    def open_rag_detail(self):
        doc_id = self._current_rag_doc_id()
        if not doc_id:
            return
        try:
            encoded_doc_id = requests.utils.quote(doc_id, safe='')
            meta_data = self.api_request("GET", f"/faust/admin/rag/documents/{encoded_doc_id}")
            content_data = self.api_request("GET", f"/faust/admin/rag/documents/{encoded_doc_id}/content")

            meta = meta_data.get("document") if isinstance(meta_data, dict) else None
            if not isinstance(meta, dict):
                # 兼容旧结构
                meta = meta_data if isinstance(meta_data, dict) else {"doc_id": doc_id}

            content = ""
            if isinstance(content_data, dict):
                # backend-main 当前会包装成: {"status":"ok", "content": {"text": "..."}}
                wrapped = content_data.get("content")
                if isinstance(wrapped, dict):
                    content = str(wrapped.get("text") or "")
                else:
                    content = str(content_data.get("text") or "")

            detail = {"document": meta, "content": content}
            self.state["rag"]["detail"] = detail

            self.rag_detail_dialog.setWindowTitle(f"RAG 详情 · {meta.get('doc_id', doc_id)}")
            self.rag_detail_dialog.meta_view.setPlainText(json.dumps(meta, ensure_ascii=False, indent=2))
            self.rag_detail_dialog.content_edit.setPlainText(str(content))
            self.rag_detail_dialog.show()
            self.rag_detail_dialog.raise_()
            self.rag_detail_dialog.activateWindow()
        except Exception as e:
            self.fail("打开 RAG 详情失败", e)

    def save_rag_detail(self):
        detail = self.state["rag"].get("detail")
        if not detail:
            return
        doc = detail.get("document") or {}
        doc_id = doc.get("doc_id")
        if not doc_id:
            return
        text = self.rag_detail_dialog.content_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "正文不能为空")
            return
        try:
            self.api_request(
                "PUT",
                f"/faust/admin/rag/documents/{requests.utils.quote(str(doc_id), safe='')}",
                payload={"text": text, "file_path": doc.get("file_path")},
            )
            self.notify(f"RAG 记录已保存: {doc_id}")
            self.open_rag_detail()
            self.load_rag_documents(reset_page=False)
        except Exception as e:
            self.fail("保存 RAG 失败", e)

    def delete_selected_rag(self):
        doc_id = self._current_rag_doc_id()
        if not doc_id:
            return
        if QMessageBox.question(self, "删除 RAG", f"确定删除 {doc_id} 吗？") != QMessageBox.Yes:
            return
        self._delete_rag(doc_id, close_dialog=False)

    def delete_rag_detail(self):
        detail = self.state["rag"].get("detail") or {}
        doc = detail.get("document") or {}
        doc_id = doc.get("doc_id")
        if not doc_id:
            return
        if QMessageBox.question(self, "删除 RAG", f"确定删除 {doc_id} 吗？") != QMessageBox.Yes:
            return
        self._delete_rag(str(doc_id), close_dialog=True)

    def _delete_rag(self, doc_id: str, close_dialog: bool):
        try:
            self.api_request("DELETE", f"/faust/admin/rag/documents/{requests.utils.quote(doc_id, safe='')}")
            if close_dialog:
                self.rag_detail_dialog.close()
            self.state["rag"]["detail"] = None
            self.load_rag_documents(reset_page=False)
            self.notify(f"RAG 记录已删除: {doc_id}")
        except Exception as e:
            self.fail("删除 RAG 失败", e)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Faust Configer")
    window = ConfigerWindow()
    window.show()
    sys.exit(app.exec())
