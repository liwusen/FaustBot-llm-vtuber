"""B 组感知源：Windows 系统级信号（winsdk + 注册表）。

显示器与电源、音频设备与麦克风占用、手柄、网络/SSID、环境光、USB 设备、蓝牙外设电量。
所有 winsdk 调用都是 awaitable；单项失败写进 SensorResult.status，不静默丢数据。
"""

from __future__ import annotations

import ctypes
import os
from typing import Any

from dm_api import SensorContext, SensorResult

SOURCES = (
    {'id': 'display_power', 'key': 'ENABLE_DISPLAY_POWER', 'tier': 'green', 'default': True,
     'cadence': 10, 'label': '显示器与电源', 'note': '显示器开关/省电状态/供电方式（winsdk PowerManager）',
     'fields': ('display_status', 'power')},
    {'id': 'audio_devices', 'key': 'ENABLE_AUDIO_DEVICES', 'tier': 'green', 'default': True,
     'cadence': 30, 'label': '音频设备与麦克风', 'note': '当前输出设备是否耳机；麦克风是否被占用（注册表 CapabilityAccessManager）',
     'fields': ('audio_output', 'mic')},
    {'id': 'gamepad', 'key': 'ENABLE_GAMEPAD', 'tier': 'green', 'default': True,
     'cadence': 30, 'label': '手柄', 'note': '是否接入手柄（Gamepad API）', 'fields': ('gamepad_connected',)},
    {'id': 'network', 'key': 'ENABLE_NETWORK_WATCH', 'tier': 'green', 'default': True,
     'cadence': 30, 'label': '网络', 'note': '联网类型/是否计量/VPN/SSID（ConnectionProfile）', 'fields': ('network',)},
    {'id': 'ambient_light', 'key': 'ENABLE_AMBIENT_LIGHT', 'tier': 'green', 'default': True,
     'cadence': 30, 'label': '环境光', 'note': '笔记本环境光传感器（没有该硬件的机器会如实报告不可用）',
     'fields': ('ambient_light',)},
    {'id': 'usb_devices', 'key': 'ENABLE_USB_DEVICES', 'tier': 'green', 'default': True,
     'cadence': 30, 'label': 'USB 设备', 'note': 'USB 设备增减（手机/存储/外设）', 'fields': ('usb_devices',)},
    {'id': 'peripheral_battery', 'key': 'ENABLE_PERIPHERAL_BATTERY', 'tier': 'green', 'default': True,
     'cadence': 300, 'label': '外设电量', 'note': '蓝牙外设电量（键盘/鼠标/耳机）', 'fields': ('peripherals',)},
)

FIELD_LABELS = {
    'display_status': '显示器状态', 'power': '电源', 'audio_output': '音频输出',
    'mic': '麦克风', 'gamepad_connected': '手柄', 'network': '网络',
    'ambient_light': '环境光', 'usb_devices': 'USB 设备', 'peripherals': '外设电量',
}

DISPLAY_STATUS_MAP = {0: 'unknown', 1: 'off', 2: 'on', 3: 'dimmed'}
MIC_CONSENT_PATH = r'SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone'
USB_INTERFACE_SELECTOR = 'System.Devices.InterfaceClassGuid:="{A5DCBF10-6530-11D2-901F-00C04FB951ED}"'
WPD_SELECTOR = 'System.Devices.InterfaceClassGuid:="{6AC27878-A6FA-4155-BA85-F98F491D4F33}"'
BLUETOOTH_SELECTOR = 'System.Devices.Aep.ProtocolId:="{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}"'
HEADPHONE_HINTS = ('headphone', 'headset', 'earbud', 'earphone', 'airpod', 'buds', '耳机', '蓝牙',
                   'bluetooth', 'hands-free', 'wh-', 'wf-')
FILETIME_EPOCH_1970 = 116444736000000000  # 1970-01-01 的 FILETIME 表示


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [('ACLineStatus', ctypes.c_ubyte), ('BatteryFlag', ctypes.c_ubyte),
                ('BatteryLifePercent', ctypes.c_ubyte), ('SystemStatusFlag', ctypes.c_ubyte),
                ('BatteryLifeTime', ctypes.c_ulong), ('BatteryFullLifeTime', ctypes.c_ulong)]


def read_ac_online() -> bool | None:
    """AC 供电状态（GetSystemPowerStatus，比 winsdk 的 PowerSupplyStatus 语义明确）。"""
    try:
        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
        if status.ACLineStatus == 1:
            return True
        if status.ACLineStatus == 0:
            return False
        return None
    except Exception:
        return None


def parse_microphone_usage(entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """解析 CapabilityAccessManager 的麦克风使用记录（纯函数，便于单测）。

    LastUsedTimeStart > 0 且 LastUsedTimeStop < Start 表示"正在使用"（Windows 用 0 表示未结束）。
    """
    active: list[tuple[int, str]] = []
    for name, values in (entries or {}).items():
        if not isinstance(values, dict):
            continue
        try:
            started = int(values.get('LastUsedTimeStart') or 0)
            stopped = int(values.get('LastUsedTimeStop') or 0)
        except (TypeError, ValueError):
            continue
        if started > 0 and stopped < started:
            active.append((started, str(name)))
    if not active:
        return {'in_use': False, 'app': None, 'since': None}
    started, key = max(active)
    label = key if os.sep not in key else os.path.basename(key)
    # 注册表里是 FILETIME（100ns since 1601）；小于 1970 的量级说明不是有效时间戳
    since = started // 10_000_000 - 11644473600 if started >= FILETIME_EPOCH_1970 else None
    return {'in_use': True, 'app': label, 'since': since}


def read_microphone_registry() -> dict[str, dict[str, Any]]:
    """读注册表里的麦克风使用记录；非 Windows 或读不到时返回空 dict。"""
    try:
        import winreg
    except ImportError:
        return {}
    entries: dict[str, dict[str, Any]] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MIC_CONSENT_PATH) as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                name = winreg.EnumKey(key, index)
                values: dict[str, Any] = {}
                try:
                    with winreg.OpenKey(key, name) as sub:
                        for value_index in range(winreg.QueryInfoKey(sub)[1]):
                            value_name, value, _ = winreg.EnumValue(sub, value_index)
                            values[value_name] = value
                except OSError:
                    continue
                if values:
                    entries[name] = values
    except OSError:
        return {}
    return entries


def is_headphone_name(name: str | None) -> bool:
    text = str(name or '').lower()
    return any(hint in text for hint in HEADPHONE_HINTS)


def network_type_from_iana(iana_type: int | None) -> str:
    if iana_type == 71:
        return 'wifi'
    if iana_type in (6, 62):
        return 'ethernet'
    if iana_type in (243, 244):
        return 'vpn'
    return 'unknown'


def summarize_usb_change(previous: set[str] | None, current: set[str], now: float,
                         last_event_at: int | None) -> dict[str, Any]:
    """USB 设备集合差分（纯函数）。"""
    if previous is None:
        return {'count': len(current), 'added': [], 'removed': [], 'last_event_at': last_event_at}
    added = sorted(current - previous)
    removed = sorted(previous - current)
    return {
        'count': len(current),
        'added': added,
        'removed': removed,
        'last_event_at': int(now) if (added or removed) else last_event_at,
    }


# ── winsdk 采集 ───────────────────────────────────────────────

async def _collect_display_power(sctx: SensorContext) -> tuple[dict[str, Any], str | None]:
    from winsdk.windows.system.power import PowerManager
    note: str | None = None
    display_status = 'unknown'
    if hasattr(PowerManager, 'display_status'):
        try:
            display_status = DISPLAY_STATUS_MAP.get(int(PowerManager.display_status), 'unknown')
        except Exception as exc:  # noqa: BLE001
            note = f'显示器状态读取失败: {exc}'
    else:
        note = '该 winsdk 版本无 PowerManager.display_status（只有锁屏/空闲可判断离开）'
    energy = 'unknown'
    try:
        from winsdk.windows.system.power import EnergySaverStatus
        state = PowerManager.energy_saver_status
        if state == EnergySaverStatus.ON:
            energy = 'on'
        elif state in (EnergySaverStatus.OFF, EnergySaverStatus.DISABLED):
            energy = 'off'
    except Exception:
        energy = 'unknown'
    return {
        'display_status': display_status,
        'power': {'ac_online': await sctx.to_thread(read_ac_online), 'energy_saver': energy},
    }, note


async def _default_audio_render(sctx: SensorContext) -> tuple[str | None, bool | None, str | None]:
    from winsdk.windows.media.devices import AudioDeviceRole, MediaDevice
    from winsdk.windows.devices.enumeration import DeviceInformation
    try:
        selector = MediaDevice.get_audio_render_selector()
        default_id = MediaDevice.get_default_audio_render_id(AudioDeviceRole.DEFAULT)
        devices = await DeviceInformation.find_all_async(selector, [])
    except Exception as exc:  # noqa: BLE001
        return None, None, f'音频设备枚举失败: {exc}'
    name = None
    for device in devices:
        if default_id and str(device.id) == str(default_id):
            name = str(device.name)
            break
    if name is None and devices:
        name = str(devices[0].name)
    if name is None:
        return None, None, '未找到默认输出设备'
    return name, is_headphone_name(name), None


async def _collect_audio(sctx: SensorContext) -> tuple[dict[str, Any], str | None]:
    note: str | None = None
    if sctx.is_due('audio_devices'):
        name, headphones, error = await _default_audio_render(sctx)
        if error:
            note = error
        else:
            sctx.memory['b.audio_output'] = {'device': name, 'headphones': headphones}
    mic = parse_microphone_usage(await sctx.to_thread(read_microphone_registry))
    return {
        'audio_output': sctx.memory.get('b.audio_output'),
        'mic': mic,
    }, note


async def _collect_gamepad(sctx: SensorContext) -> dict[str, Any]:
    from winsdk.windows.gaming.input import Gamepad
    return {'gamepad_connected': len(list(Gamepad.gamepads)) > 0}


async def _collect_network(sctx: SensorContext) -> tuple[dict[str, Any], str | None]:
    from winsdk.windows.networking.connectivity import NetworkInformation
    profile = NetworkInformation.get_internet_connection_profile()
    if profile is None:
        return {'network': {'type': 'none', 'metered': None, 'vpn': None, 'ssid': None, 'signal': None}}, None
    iana = None
    try:
        adapter = profile.network_adapter
        iana = int(adapter.iana_interface_type) if adapter is not None else None
    except Exception:
        iana = None
    net_type = network_type_from_iana(iana)
    metered = None
    try:
        metered = int(profile.get_connection_cost().network_cost_type) in (2, 3)
    except Exception:
        metered = None
    ssid = None
    note = None
    try:
        details = profile.wlan_connection_profile_details
        if details is not None:
            ssid = str(details.get_connected_ssid())
    except Exception as exc:  # noqa: BLE001
        note = f'SSID 读取失败: {exc}'
    signal = None
    try:
        signal = int(profile.get_signal_bars())
    except Exception:
        signal = None
    vpn = False
    if net_type == 'vpn':
        vpn = True
    else:
        try:
            for candidate in NetworkInformation.get_connection_profiles():
                name = str(candidate.profile_name or '').lower()
                if int(candidate.get_network_connectivity_level()) > 0 and (
                        'vpn' in name or 'wireguard' in name or 'tap' in name or 'clash' in name):
                    vpn = True
                    break
        except Exception:
            vpn = False
    return {'network': {'type': net_type, 'metered': metered, 'vpn': vpn, 'ssid': ssid, 'signal': signal}}, note


async def _collect_ambient_light(sctx: SensorContext) -> tuple[dict[str, Any], str | None]:
    from winsdk.windows.devices.sensors import LightSensor
    sensor = LightSensor.get_default()
    if sensor is None:
        return {}, '本机无环境光传感器'
    try:
        reading = await sensor.get_current_reading_async()
    except Exception as exc:  # noqa: BLE001
        return {}, f'环境光读取失败: {exc}'
    if reading is None:
        return {}, '环境光无读数'
    return {'ambient_light': {'lux': float(reading.illuminance_in_lux)}}, None


async def _usb_device_names() -> set[str]:
    from winsdk.windows.devices.enumeration import DeviceInformation
    names: set[str] = set()
    for selector in (USB_INTERFACE_SELECTOR, WPD_SELECTOR):
        try:
            for device in await DeviceInformation.find_all_async(selector, []):
                name = str(device.name or '').strip()
                if name:
                    names.add(name)
        except Exception:
            continue
    return names


async def _collect_usb(sctx: SensorContext) -> dict[str, Any]:
    names = await _usb_device_names()
    previous = sctx.memory.get('b.usb')
    summary = summarize_usb_change(previous, names, sctx.now, sctx.memory.get('b.usb_event_at'))
    sctx.memory['b.usb'] = names
    sctx.memory['b.usb_event_at'] = summary['last_event_at']
    return {'usb_devices': summary}


async def _collect_peripheral_battery(sctx: SensorContext) -> tuple[dict[str, Any], str | None]:
    from winsdk.windows.devices.enumeration import DeviceInformation
    found: list[dict[str, Any]] = []
    try:
        devices = await DeviceInformation.find_all_async(
            BLUETOOTH_SELECTOR, ['System.Devices.BatteryLife', 'System.Devices.Aep.DeviceAddress'])
    except Exception as exc:  # noqa: BLE001
        return {}, f'蓝牙设备枚举失败: {exc}'
    for device in devices:
        try:
            properties = device.properties or {}
            raw = properties.get('System.Devices.BatteryLife')
        except Exception:
            continue
        if raw in (None, ''):
            continue
        try:
            percent = int(raw) if not isinstance(raw, (bytes, bytearray)) else int(raw[0])
        except (TypeError, ValueError):
            continue
        found.append({'name': str(device.name or '未知设备'), 'percent': max(0, min(100, percent))})
    if not found:
        return {}, '未从蓝牙设备读到最后一次上报的电量'
    return {'peripherals': found}, None


async def collect(sctx: SensorContext) -> SensorResult:
    result = SensorResult()
    if sctx.enabled('display_power') and sctx.is_due('display_power'):
        try:
            fields, note = await _collect_display_power(sctx)
            result.fields.update(fields)
            if note:
                result.status['display_power'] = note
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('显示器/电源采集失败: %s', exc)
            result.status['display_power'] = f'采集失败: {exc}'
    if sctx.enabled('audio_devices'):
        try:
            fields, note = await _collect_audio(sctx)
            result.fields.update(fields)
            if note:
                result.status['audio_devices'] = note
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('音频设备/麦克风采集失败: %s', exc)
            result.status['audio_devices'] = f'采集失败: {exc}'
    if sctx.enabled('gamepad') and sctx.is_due('gamepad'):
        try:
            result.fields.update(await _collect_gamepad(sctx))
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('手柄采集失败: %s', exc)
            result.status['gamepad'] = f'采集失败: {exc}'
    if sctx.enabled('network') and sctx.is_due('network'):
        try:
            fields, note = await _collect_network(sctx)
            result.fields.update(fields)
            if note:
                result.status['network'] = note
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('网络信息采集失败: %s', exc)
            result.status['network'] = f'采集失败: {exc}'
    if sctx.enabled('ambient_light') and sctx.is_due('ambient_light'):
        fields, error = await _collect_ambient_light(sctx)
        result.fields.update(fields)
        if error:
            result.status['ambient_light'] = error
    if sctx.enabled('usb_devices') and sctx.is_due('usb_devices'):
        try:
            result.fields.update(await _collect_usb(sctx))
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('USB 设备采集失败: %s', exc)
            result.status['usb_devices'] = f'采集失败: {exc}'
    if sctx.enabled('peripheral_battery') and sctx.is_due('peripheral_battery'):
        fields, error = await _collect_peripheral_battery(sctx)
        result.fields.update(fields)
        if error:
            result.status['peripheral_battery'] = error
    return result


# ── 面板显示 ─────────────────────────────────────────────────

def _fmt_display_status(value: Any) -> str:
    return {'on': '点亮', 'off': '熄灭', 'dimmed': '变暗', 'unknown': '未知'}.get(str(value), str(value or '未知'))


def _fmt_power(value: Any) -> str:
    if not value:
        return '未知'
    ac = value.get('ac_online')
    text = '接通电源' if ac else ('电池供电' if ac is False else '供电未知')
    if value.get('energy_saver') == 'on':
        text += ' · 省电模式'
    return text


def _fmt_audio_output(value: Any) -> str:
    if not value:
        return '未知'
    name = str(value.get('device') or '未知设备')
    headphones = value.get('headphones')
    if headphones is True:
        return f'{name}（耳机）'
    if headphones is False:
        return f'{name}（外放）'
    return name


def _fmt_mic(value: Any) -> str:
    if not value:
        return '未知'
    if value.get('in_use'):
        return f'占用中（{value.get("app") or "未知程序"}）'
    return '未被占用'


def _fmt_gamepad(value: Any) -> str:
    return '已接入' if value else '无'


def _fmt_network(value: Any) -> str:
    if not value:
        return '未知'
    names = {'wifi': 'Wi-Fi', 'ethernet': '有线', 'vpn': 'VPN', 'none': '离线', 'unknown': '未知'}
    text = names.get(str(value.get('type')), '未知')
    if value.get('ssid'):
        text += f' · {value["ssid"]}'
    if value.get('signal'):
        text += f' · 信号 {int(value["signal"])}/5'
    if value.get('metered'):
        text += ' · 计量网络'
    if value.get('vpn'):
        text += ' · VPN'
    return text


def _fmt_ambient_light(value: Any) -> str:
    if not value or value.get('lux') is None:
        return '无读数'
    return f'{float(value["lux"]):.0f} lux'


def _fmt_usb(value: Any) -> str:
    if not value:
        return '未知'
    parts = [f'{int(value.get("count") or 0)} 个']
    if value.get('added'):
        parts.append('接入 ' + '、'.join(value['added'][:3]))
    if value.get('removed'):
        parts.append('移除 ' + '、'.join(value['removed'][:3]))
    return ' · '.join(parts)


def _fmt_peripherals(value: Any) -> str:
    if not value:
        return '无数据'
    return '、'.join(f'{item.get("name")} {item.get("percent")}%' for item in value[:4])


FORMATTERS = {
    'display_status': _fmt_display_status,
    'power': _fmt_power,
    'audio_output': _fmt_audio_output,
    'mic': _fmt_mic,
    'gamepad_connected': _fmt_gamepad,
    'network': _fmt_network,
    'ambient_light': _fmt_ambient_light,
    'usb_devices': _fmt_usb,
    'peripherals': _fmt_peripherals,
}
