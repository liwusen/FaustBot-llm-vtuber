from __future__ import annotations

import asyncio
import importlib.util
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins' / 'song-studio'


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f'song_studio_test_{name}', PLUGIN_DIR / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


library = _load('library')
impl = _load('impl')


def _write_wav(path: Path, seconds: float = 0.1) -> None:
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b'\x00\x00' * int(16000 * seconds))


@pytest.fixture()
def lib(tmp_path):
    return library.SongLibrary(tmp_path / 'data')


def test_library_scan_and_find(lib):
    _write_wav(lib.source_dir / 'Tell Your World.wav')
    (lib.source_dir / 'Tell Your World.lrc').write_text('[00:01.00]hello', encoding='utf-8')
    _write_wav(lib.source_dir / 'other song.wav')
    (lib.source_dir / 'readme.txt').write_text('x', encoding='utf-8')

    songs = lib.list_source_songs()
    assert sorted(s['name'] for s in songs) == ['Tell Your World', 'other song']
    by_name = {s['name']: s for s in songs}
    assert by_name['Tell Your World']['lrc'] is not None
    assert by_name['other song']['lrc'] is None

    assert lib.find_song('tell your world')['name'] == 'Tell Your World'
    assert lib.find_song('world')['name'] == 'Tell Your World'
    assert lib.find_song('missing') is None


def test_cache_entry_key_stable_and_param_sensitive(lib):
    song = lib.source_dir / 'a.wav'
    ref = lib.refs_dir / 'ref.wav'
    _write_wav(song)
    _write_wav(ref, seconds=0.2)

    params = {'diffusion_steps': 50, 'semi_tone_shift': 0}
    entry1 = lib.cache_entry(song, ref, params)
    entry2 = lib.cache_entry(song, ref, dict(params))
    assert entry1['key'] == entry2['key']
    assert not entry1['ready']

    entry3 = lib.cache_entry(song, ref, {'diffusion_steps': 30, 'semi_tone_shift': 0})
    assert entry3['key'] != entry1['key']

    entry1['dir'].mkdir(parents=True)
    entry1['final'].write_bytes(b'RIFF')
    lib.write_meta(entry1, {'name': 'a', 'file': str(song)}, ref, params, 12.3)
    assert lib.cache_entry(song, ref, params)['ready']

    assert lib.delete_cache(entry1['key'])
    assert not lib.cache_entry(song, ref, params)['ready']
    assert not lib.delete_cache('nonexistent')


class _FakeCtx:
    plugin_data_dir = None
    plugin_dir = None

    def __init__(self, data_dir: Path, config: dict):
        self.plugin_data_dir = data_dir
        self.plugin_dir = data_dir
        self._config = config

    def register_config(self, schema):
        return None

    def get_config(self, key, default=None):
        return self._config.get(key, default)

    def vfs_write(self, path, content):
        self._config.setdefault('_vfs', {})[path] = content


@pytest.fixture()
def plugin(tmp_path):
    p = impl.Plugin()
    ctx = _FakeCtx(tmp_path / 'plugin_data' / 'song-studio', {
        'REF_AUDIO_PATH': '',
        'DIFFUSION_STEPS': 50,
        'SEMI_TONE_SHIFT': 0,
        'AUTO_F0_ADJUST': True,
        'VOCAL_GAIN_DB': 0.0,
    })
    ctx.plugin_data_dir.mkdir(parents=True, exist_ok=True)
    p.startup(ctx)
    return p


def test_plugin_status_and_songs(plugin):
    resp = asyncio.run(plugin.communicate_handler({'action': 'status'}, plugin.ctx))
    assert resp['status'] == 'ok'
    assert resp['runtime_installed'] is False
    assert resp['singing'] is None

    _write_wav(plugin.lib.source_dir / 'demo.wav')
    resp = asyncio.run(plugin.communicate_handler({'action': 'list_songs'}, plugin.ctx))
    assert resp['status'] == 'ok'
    assert resp['items'][0]['name'] == 'demo'
    assert resp['items'][0]['cached'] is False


def test_plugin_unknown_action(plugin):
    resp = asyncio.run(plugin.communicate_handler({'action': 'nope'}, plugin.ctx))
    assert resp['status'] == 'error'


def test_plugin_convert_requires_ref(plugin, monkeypatch):
    import faust_backend.config_loader as conf
    monkeypatch.setattr(conf, 'TTS_REFER_WAV_PATH', '', raising=False)
    _write_wav(plugin.lib.source_dir / 'demo.wav')
    resp = asyncio.run(plugin.communicate_handler({'action': 'convert_song', 'name': 'demo'}, plugin.ctx))
    assert resp['status'] == 'error'
    assert '参考音色' in resp['detail']


def test_plugin_sse_job_stream(plugin):
    import time as _time
    job = {
        'id': 'manualjob1', 'type': 'convert', 'status': 'running', 'stage': 'convert',
        'percent': 10.0, 'message': 'working', 'log': ['working'], 'error': None,
        'created_at': _time.time(), 'cancel': False,
    }
    with plugin._jobs_lock:
        plugin._jobs[job['id']] = job

    async def collect():
        agen = plugin.sse_communicate_handler({'job_id': job['id']}, plugin.ctx)
        items = []
        async for item in agen:
            items.append(item)
            if len(items) >= 2:
                plugin._update_job(job, status='done')
        return items

    items = asyncio.run(asyncio.wait_for(collect(), timeout=10))
    assert items[0]['kind'] == 'job'
    assert items[0]['status'] == 'running'
    assert items[-1]['status'] == 'done'


def test_plugin_sse_unknown_job(plugin):
    async def collect():
        agen = plugin.sse_communicate_handler({'job_id': 'missing'}, plugin.ctx)
        return [item async for item in agen]

    items = asyncio.run(collect())
    assert items == [{'kind': 'error', 'detail': '任务不存在: missing'}]


def test_plugin_tools_registered(plugin):
    tools = plugin.register_tools(plugin.ctx)
    assert [t.name for t in tools] == ['singSong', 'stopSinging']


def test_stop_singing_without_song(plugin):
    resp = asyncio.run(plugin.communicate_handler({'action': 'stop_sing'}, plugin.ctx))
    assert resp == {'status': 'ok', 'was_singing': False}
