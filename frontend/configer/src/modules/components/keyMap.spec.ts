import { describe, expect, it } from 'vitest';
import {
  adminServiceKey,
  buildInstallBody,
  componentTaskEventsPath,
  componentTaskEventsUrl,
  FUNASR_VARIANTS,
  funasrInstallText,
  funasrTorchText,
  gpuRows,
  installErrorText,
  installProgressText,
  servicePortText,
  serviceStatusText,
  serviceSummary,
  serviceTone,
  TTS_VARIANTS,
  ttsInstallText,
  ttsVariantText,
} from './keyMap';

describe('组件服务的 admin key 映射', () => {
  it('minecraft 显示 key 映射到 mc_operator，其它原样', () => {
    expect(adminServiceKey('minecraft')).toBe('mc_operator');
    expect(adminServiceKey('asr')).toBe('asr');
    expect(adminServiceKey('tts')).toBe('tts');
    expect(adminServiceKey('')).toBe('');
  });
});

describe('服务状态文案与配色', () => {
  it('starting/stopping 优先于 is_running', () => {
    expect(serviceStatusText({ status: 'starting', is_running: false })).toBe('启动中');
    expect(serviceStatusText({ status: 'stopping', is_running: true })).toBe('关闭中');
    expect(serviceStatusText({ is_running: true })).toBe('运行中');
    expect(serviceStatusText({ is_running: false })).toBe('已停止');
    expect(serviceStatusText(null)).toBe('已停止');
  });

  it('配色 class 与旧 _serviceTone 一致', () => {
    expect(serviceTone({ status: 'starting' })).toBe('is-warn');
    expect(serviceTone({ status: 'stopping' })).toBe('is-busy');
    expect(serviceTone({ is_running: true })).toBe('is-ok');
    expect(serviceTone({})).toBe('is-stop');
    expect(serviceTone(undefined)).toBe('is-stop');
  });

  it('端口文案与状态拼接保留旧实现的空格', () => {
    expect(servicePortText({ port: 10095 })).toBe('端口 10095');
    expect(servicePortText({})).toBe('');
    expect(serviceSummary({ is_running: true, port: 10095 })).toBe('运行中 端口 10095');
    expect(serviceSummary({ is_running: false })).toBe('已停止 ');
  });
});

describe('FunASR / TTS 状态文案', () => {
  it('FunASR 安装状态与 PyTorch 环境', () => {
    expect(funasrInstallText({ installed: true, version: '1.2.0' })).toBe('已安装 (funasr 1.2.0)');
    expect(funasrInstallText({ installed: true, version: null })).toBe('已安装 (funasr )');
    expect(funasrInstallText({ installed: false })).toBe('未安装');
    expect(funasrTorchText({ torch_version: '2.4.0', torch_variant: 'cu128' })).toBe('2.4.0 (cu128)');
    expect(funasrTorchText({ torch_version: '2.4.0', torch_variant: null })).toBe('2.4.0 (?)');
    expect(funasrTorchText({})).toBe('未检测');
  });

  it('TTS 安装状态与 GPU 变体', () => {
    expect(ttsInstallText({ installed: true, path: 'tts-hub/GPT-SoVITS-v2pro' }))
      .toBe('已安装 (tts-hub/GPT-SoVITS-v2pro)');
    expect(ttsInstallText({ installed: true, path: null })).toBe('已安装 ()');
    expect(ttsInstallText({ installed: false })).toBe('未安装');
    expect(ttsVariantText({ variant: 'nvidia50' })).toBe('nvidia50');
    expect(ttsVariantText({})).toBe('standard');
  });

  it('下拉选项与旧实现一致', () => {
    expect(FUNASR_VARIANTS.map((item) => item.value)).toEqual(['cu128', 'cu121', 'cu130', 'cpu']);
    expect(TTS_VARIANTS).toEqual([
      { value: 'standard', label: 'Standard' },
      { value: 'nvidia50', label: 'NVIDIA 50 系列' },
    ]);
  });
});

describe('安装请求体', () => {
  it('funasr 带 torch 变体与镜像开关', () => {
    expect(buildInstallBody('funasr', { torchVariant: 'cu128', useAliyunMirror: true })).toEqual({
      component: 'funasr',
      torch_variant: 'cu128',
      use_aliyun_mirror: true,
    });
    expect(buildInstallBody('funasr')).toEqual({
      component: 'funasr',
      torch_variant: undefined,
      use_aliyun_mirror: false,
    });
  });

  it('tts 只带 tts 变体', () => {
    expect(buildInstallBody('tts', { ttsVariant: 'nvidia50' })).toEqual({
      component: 'tts',
      tts_variant: 'nvidia50',
    });
  });
});

describe('安装 SSE 路径与事件文案', () => {
  it('任务事件路径做 URL 编码并兼容尾斜杠 base', () => {
    expect(componentTaskEventsPath('task-1')).toBe('/faust/components/tasks/task-1/events');
    expect(componentTaskEventsPath('a/b')).toBe('/faust/components/tasks/a%2Fb/events');
    expect(componentTaskEventsUrl('http://127.0.0.1:13900/', 't1'))
      .toBe('http://127.0.0.1:13900/faust/components/tasks/t1/events');
    expect(componentTaskEventsUrl('http://127.0.0.1:13900', 't1'))
      .toBe('http://127.0.0.1:13900/faust/components/tasks/t1/events');
  });

  it('进度文案取最后一行日志，否则取 stage', () => {
    expect(installProgressText({ stage: 'pip_output', log_lines: ['a', 'b'] })).toBe('b');
    expect(installProgressText({ stage: 'downloading', log_lines: [] })).toBe('downloading');
    expect(installProgressText({})).toBe('');
  });

  it('错误文案与旧实现一致', () => {
    expect(installErrorText('boom')).toBe('错误: boom');
    expect(installErrorText(undefined)).toBe('错误: 未知');
  });
});

describe('GPU 信息行', () => {
  it('无 NVIDIA 时只显示一行状态', () => {
    expect(gpuRows({ has_nvidia: false, gpus: [] })).toEqual([
      { label: '状态', value: '未检测到 NVIDIA GPU' },
    ]);
    expect(gpuRows(null)).toEqual([{ label: '状态', value: '未检测到 NVIDIA GPU' }]);
  });

  it('有 GPU 时按名称/CUDA/Driver 展开', () => {
    expect(gpuRows({
      has_nvidia: true,
      gpus: [
        { name: 'RTX 4090', cuda_version: '12.8', driver_version: '570.1' },
        { name: 'RTX 3060' },
      ],
    })).toEqual([
      { label: 'GPU 1', value: 'RTX 4090' },
      { label: 'CUDA 版本', value: '12.8' },
      { label: 'Driver 版本', value: '570.1' },
      { label: 'GPU 2', value: 'RTX 3060' },
    ]);
  });
});
