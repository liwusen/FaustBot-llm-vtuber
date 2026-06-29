// Handle deeplink-config-faustcloud from main process

async function handleDeeplinkConfigFaustCloud(payload) {
  if (!payload || typeof payload !== 'object') return;
  const host = String(payload.host || '').trim();
  const key = String(payload.key || '').trim();
  if (!host || !key) return;

  const hostInput = el('input', 'input');
  hostInput.readOnly = true;
  hostInput.value = host;

  const keyInput = el('input', 'input');
  keyInput.readOnly = true;
  keyInput.value = key;

  const info = el('div', 'card-help');
  info.textContent = '系统检测到来自 FaustBot Cloud 的配置请求。确认后将把云地址和服务密钥写入配置，并将 TTS/ASR 模式切换为 FaustBot-cloud。';

  const actionBar = el('div', 'toolbar');
  const doConfirm = async () => {
    try {
      setBusy(true);
      const payloadToSave = {
        public: {
          FAUSTBOT_CLOUD_BASE_URL: host,
          TTS_MODE: 'faustbot-cloud',
          ASR_MODE: 'faustbot-cloud',
        },
        private: {
          FAUSTBOT_CLOUD_SERVICE_KEY: key,
        }
      };
      await cfgApi('POST', '/faust/admin/config', payloadToSave);
      try { await cfgApi('POST', '/faust/admin/config/reload', { reset_dialog: false, no_initial_chat: true }); } catch (e) {}
      try { await reloadAll(); } catch (e) { console.warn('reloadAll failed after deeplink config', e); }
      showBanner('success', 'FaustBot Cloud 已配置。');
      closeModal();
    } catch (e) {
      console.error('Failed to apply FaustBot Cloud config', e);
      showBanner('error', '配置保存失败: ' + String(e));
    } finally {
      setBusy(false);
    }
  };

  actionBar.append(makeButton('确认并保存', doConfirm, 'btn btn-primary'), makeButton('取消', closeModal));

  const body = [info, el('label', '', 'FaustBot Cloud 地址'), hostInput, el('label', '', 'Service Key'), keyInput, actionBar];
  openModal('收到 FaustBot Cloud 配置', body);
}

if (window.deeplink && typeof window.deeplink.onConfigFaustCloud === 'function') {
  window.deeplink.onConfigFaustCloud((payload) => {
    try { handleDeeplinkConfigFaustCloud(payload); } catch (e) { console.error('deeplink handler failed', e); }
  });
}
