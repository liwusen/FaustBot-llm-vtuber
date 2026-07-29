// 歌台 Song Studio — 配置中心面板：运行环境安装、曲库管理、转换进度（SSE）
(function () {
  const api = window.pluginUI;
  if (!api) return;
  const PLUGIN_ID = 'song-studio';

  function communicate(payload) {
    return api.communicate(PLUGIN_ID, payload || {});
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function watchJob(jobId, { onUpdate, onDone }) {
    if (typeof api.communicateSSE !== 'function') {
      console.warn('[song-studio] communicateSSE 不可用');
      return null;
    }
    const es = api.communicateSSE(PLUGIN_ID, { job_id: jobId });
    es.onmessage = (event) => {
      let data = null;
      try { data = JSON.parse(event.data); } catch (e) { return; }
      if (data.kind === 'error') {
        es.close();
        if (onDone) onDone({ status: 'error', message: data.detail });
        return;
      }
      if (onUpdate) onUpdate(data);
      if (['done', 'error', 'cancelled'].includes(data.status)) {
        es.close();
        if (onDone) onDone(data);
      }
    };
    es.onerror = () => { es.close(); if (onDone) onDone(null); };
    return es;
  }

  function render(container) {
    container.innerHTML = '';

    const legalNotice = el('div', 'card full-span',
      '⚠ 法律提示：本功能本质是变声器，产出内容属于对原曲的二次创作。根据法律规定，在公开场合使用需取得原作者授权。');
    legalNotice.style.cssText = 'border-left:4px solid #e6a23c;color:#e6a23c;padding:10px 14px;font-size:13px;';
    container.appendChild(legalNotice);

    const runtimeCard = el('article', 'card full-span');
    runtimeCard.appendChild(el('h3', 'card-title', '独立推理环境 (sva-runtime)'));
    const runtimeStatus = el('p', 'card-help', '加载中...');
    runtimeCard.appendChild(runtimeStatus);
    const runtimeBar = el('div', 'song-studio-progress');
    const runtimeBarInner = el('div', 'song-studio-progress-inner');
    runtimeBar.appendChild(runtimeBarInner);
    runtimeBar.style.display = 'none';
    runtimeCard.appendChild(runtimeBar);
    const runtimeLog = el('pre', 'song-studio-log');
    runtimeLog.style.display = 'none';
    runtimeCard.appendChild(runtimeLog);
    const variantToolbar = el('div', 'toolbar');
    variantToolbar.appendChild(el('span', 'card-help', 'torch CUDA 版本:'));
    const variantSelect = el('select');
    variantToolbar.appendChild(variantSelect);
    variantToolbar.appendChild(el('span', 'card-help', 'RTX 50xx 显卡需选择 cu128 或更高'));
    runtimeCard.appendChild(variantToolbar);
    const installBtn = el('button', 'btn btn-primary', '一键安装推理环境');
    runtimeCard.appendChild(installBtn);
    container.appendChild(runtimeCard);

    const refCard = el('article', 'card full-span');
    refCard.appendChild(el('h3', 'card-title', '参考音色'));
    const refStatus = el('p', 'card-help', '加载中...');
    refCard.appendChild(refStatus);
    const refToolbar = el('div', 'toolbar');
    const refInput = el('input');
    refInput.type = 'text';
    refInput.placeholder = '参考音频绝对路径（留空使用 TTS 参考音频）';
    refInput.style.minWidth = '360px';
    const refSaveBtn = el('button', 'btn btn-primary', '保存');
    refToolbar.append(refInput, refSaveBtn);
    refCard.appendChild(refToolbar);
    refCard.appendChild(el('p', 'card-help', '更换参考音色后，已转换歌曲需要重新转换才会使用新音色。'));
    container.appendChild(refCard);

    refSaveBtn.onclick = async () => {
      refSaveBtn.disabled = true;
      try {
        const res = await fetch(api.backendBaseUrl + '/faust/admin/plugins/song-studio/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            values: { REF_AUDIO_PATH: refInput.value.trim() },
            apply_runtime: true,
            no_initial_chat: true,
            reset_dialog: false,
          }),
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        refStatus.textContent = '已保存';
      } catch (e) {
        refStatus.textContent = '保存失败: ' + e.message;
      } finally {
        refSaveBtn.disabled = false;
        refresh();
      }
    };

    const singingCard = el('article', 'card full-span');
    singingCard.appendChild(el('h3', 'card-title', '演唱状态'));
    const singingStatus = el('p', 'card-help', '未在演唱');
    singingCard.appendChild(singingStatus);
    const stopBtn = el('button', 'btn btn-secondary', '停止演唱');
    stopBtn.onclick = async () => { await communicate({ action: 'stop_sing' }); refresh(); };
    singingCard.appendChild(stopBtn);
    container.appendChild(singingCard);

    const libraryCard = el('article', 'card full-span');
    libraryCard.appendChild(el('h3', 'card-title', '曲库'));
    const sourceHint = el('p', 'card-help', '');
    libraryCard.appendChild(sourceHint);
    const songTableWrap = el('div');
    libraryCard.appendChild(songTableWrap);
    const rescanBtn = el('button', 'btn btn-secondary', '重新扫描曲库');
    rescanBtn.onclick = async () => { await communicate({ action: 'rescan' }); refresh(); };
    libraryCard.appendChild(rescanBtn);
    container.appendChild(libraryCard);

    const logsCard = el('article', 'card full-span');
    logsCard.appendChild(el('h3', 'card-title', '转换 Worker 日志'));
    const logsHint = el('p', 'card-help', '每次歌曲转换的完整 worker 输出。');
    logsCard.appendChild(logsHint);
    const logsListWrap = el('div');
    logsCard.appendChild(logsListWrap);
    const logView = el('pre', 'song-studio-log');
    logView.style.display = 'none';
    logView.style.maxHeight = '320px';
    logsCard.appendChild(logView);
    const logsRefreshBtn = el('button', 'btn btn-secondary', '刷新日志列表');
    logsRefreshBtn.onclick = () => refreshLogs();
    logsCard.appendChild(logsRefreshBtn);
    container.appendChild(logsCard);

    function formatSize(bytes) {
      if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + 'MB';
      if (bytes >= 1024) return (bytes / 1024).toFixed(1) + 'KB';
      return bytes + 'B';
    }

    async function refreshLogs() {
      let res = null;
      try { res = await communicate({ action: 'list_logs' }); } catch (e) { return; }
      if (!res || res.status !== 'ok') return;
      logsListWrap.innerHTML = '';
      const items = res.items || [];
      if (!items.length) {
        logsListWrap.appendChild(el('p', 'card-help', '暂无日志。'));
        return;
      }
      const table = el('table', 'simple-table');
      const thead = el('thead');
      const headRow = el('tr');
      for (const label of ['文件', '大小', '时间', '操作']) headRow.appendChild(el('th', null, label));
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = el('tbody');
      for (const item of items) {
        const tr = el('tr');
        tr.appendChild(el('td', null, item.name));
        tr.appendChild(el('td', null, formatSize(item.size || 0)));
        tr.appendChild(el('td', null, new Date((item.mtime || 0) * 1000).toLocaleString()));
        const opTd = el('td');
        const viewBtn = el('button', 'btn btn-secondary', '查看');
        viewBtn.onclick = async () => {
          const detail = await communicate({ action: 'get_log', name: item.name });
          if (detail && detail.status === 'ok') {
            logView.style.display = '';
            logView.textContent = (detail.truncated ? '（日志过长，仅显示末尾 512KB）\n' : '') + detail.content;
            logView.scrollTop = logView.scrollHeight;
          } else {
            logView.style.display = '';
            logView.textContent = '读取失败: ' + ((detail && detail.detail) || '未知错误');
          }
        };
        opTd.appendChild(viewBtn);
        tr.appendChild(opTd);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      logsListWrap.appendChild(table);
    }

    function trackJobInto(barInner, bar, logEl, statusEl, jobId, doneText) {
      bar.style.display = '';
      logEl.style.display = '';
      watchJob(jobId, {
        onUpdate(snap) {
          const pct = Math.max(0, Math.min(100, Number(snap.percent) || 0));
          barInner.style.width = pct + '%';
          statusEl.textContent = '[' + (snap.stage || '') + '] ' + (snap.message || '');
          logEl.textContent = (snap.log_tail || []).join('\n');
          logEl.scrollTop = logEl.scrollHeight;
        },
        onDone(snap) {
          if (snap && snap.status === 'done') {
            statusEl.textContent = doneText;
          } else if (snap) {
            statusEl.textContent = '失败: ' + (snap.error || snap.message || '未知错误');
          } else {
            statusEl.textContent = '进度连接中断';
          }
          refresh();
        },
      });
    }

    installBtn.onclick = async () => {
      installBtn.disabled = true;
      const resp = await communicate({ action: 'install_runtime', torch_variant: variantSelect.value });
      if (resp && resp.job_id) {
        trackJobInto(runtimeBarInner, runtimeBar, runtimeLog, runtimeStatus, resp.job_id, '安装完成');
      } else {
        runtimeStatus.textContent = (resp && resp.detail) || '安装请求失败';
        refresh();
      }
    };

    function renderSongs(items) {
      songTableWrap.innerHTML = '';
      const table = el('table', 'simple-table');
      const thead = el('thead');
      const headRow = el('tr');
      for (const label of ['歌曲', '已转换', '歌词', '操作', '进度']) headRow.appendChild(el('th', null, label));
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = el('tbody');
      if (!items.length) {
        const tr = el('tr');
        const td = el('td', null, '曲库为空，请将歌曲文件（mp3/wav/flac 等，可附同名 .lrc）放入 source 目录');
        td.colSpan = 5;
        tr.appendChild(td);
        tbody.appendChild(tr);
      }
      for (const song of items) {
        const tr = el('tr');
        tr.appendChild(el('td', null, song.name));
        tr.appendChild(el('td', null, song.cached ? '是' : '否'));
        tr.appendChild(el('td', null, song.lrc ? '有' : '无'));
        const opTd = el('td');
        const progressTd = el('td', 'song-studio-song-progress', '');
        const convertBtn = el('button', 'btn btn-secondary', song.cached ? '重新转换' : '转换');
        convertBtn.onclick = async () => {
          convertBtn.disabled = true;
          const resp = await communicate({ action: 'convert_song', name: song.name });
          if (resp && resp.job_id) {
            watchJob(resp.job_id, {
              onUpdate(snap) {
                const pct = Math.max(0, Math.min(100, Number(snap.percent) || 0));
                progressTd.textContent = '[' + (snap.stage || '') + '] ' + pct + '% ' + (snap.message || '').slice(0, 60);
              },
              onDone(snap) {
                progressTd.textContent = snap && snap.status === 'done' ? '转换完成' : '失败: ' + ((snap && (snap.error || snap.message)) || '连接中断');
                refresh();
              },
            });
          } else {
            progressTd.textContent = (resp && resp.detail) || '转换请求失败';
            convertBtn.disabled = false;
          }
        };
        opTd.appendChild(convertBtn);
        const singBtn = el('button', 'btn btn-primary', '演唱');
        singBtn.onclick = async () => {
          const resp = await communicate({ action: 'sing', name: song.name });
          progressTd.textContent = (resp && resp.detail) || '';
          refresh();
        };
        opTd.appendChild(singBtn);
        if (song.cached && song.cache_key) {
          const delBtn = el('button', 'btn btn-secondary', '删缓存');
          delBtn.onclick = async () => {
            await communicate({ action: 'delete_cache', key: song.cache_key });
            refresh();
          };
          opTd.appendChild(delBtn);
        }
        tr.appendChild(opTd);
        tr.appendChild(progressTd);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      songTableWrap.appendChild(table);
    }

    async function refresh() {
      try {
        const [status, songs] = await Promise.all([
          communicate({ action: 'status' }),
          communicate({ action: 'list_songs' }),
        ]);
        if (status && status.status === 'ok') {
          runtimeStatus.textContent = status.runtime_installed
            ? '已安装 (' + (status.torch_variant || '未知 torch 变体') + '): ' + status.runtime_dir
            : '未安装（首次约需下载数 GB 依赖）: ' + status.runtime_dir;
          const variants = status.torch_variants || [];
          if (variantSelect.options.length !== variants.length) {
            variantSelect.innerHTML = '';
            for (const v of variants) {
              const opt = document.createElement('option');
              opt.value = v;
              opt.textContent = v;
              variantSelect.appendChild(opt);
            }
            variantSelect.value = status.torch_variant || status.default_torch_variant || variants[0] || '';
          }
          installBtn.disabled = false;
          installBtn.textContent = status.runtime_installed ? '重新安装推理环境' : '一键安装推理环境';
          singingStatus.textContent = status.singing
            ? '正在演唱: ' + status.singing.song
            : '未在演唱';
          stopBtn.disabled = !status.singing;
          sourceHint.textContent = '曲库目录: ' + status.source_dir;
          if (document.activeElement !== refInput) refInput.value = status.ref_audio_config || '';
          refStatus.textContent = status.ref_audio
            ? '当前生效: ' + status.ref_audio + (status.ref_audio_config ? '' : '（来自 TTS 参考音频）')
            : '不可用: ' + (status.ref_audio_error || '未配置');
          const running = (status.jobs || []).find((j) => j.status === 'running' || j.status === 'queued');
          if (running && running.type === 'install') {
            trackJobInto(runtimeBarInner, runtimeBar, runtimeLog, runtimeStatus, running.id, '安装完成');
          }
        }
        renderSongs((songs && songs.items) || []);
        refreshLogs();
      } catch (e) {
        runtimeStatus.textContent = '插件状态读取失败（插件可能未启用）';
        console.warn('[song-studio] refresh failed', e);
      }
    }

    refresh();
  }

  api.addPage({ id: 'song-studio', label: '歌台', desc: 'AI 歌声转换与演唱（Seed-VC）', plugin: 'song-studio', render: render });
  api.addCard('plugins', {
    title: '歌台 Song Studio',
    priority: 18,
    plugin: 'song-studio',
    render(container) {
      communicate({ action: 'status' }).then((status) => {
        const singing = status && status.singing ? '正在演唱: ' + status.singing.song : '未在演唱';
        const runtime = status && status.runtime_installed ? '推理环境已安装' : '推理环境未安装';
        container.innerHTML = '<div class="plugin-mini-card"><p></p><p class="plugin-mini-muted"></p></div>';
        container.querySelector('p').textContent = singing;
        container.querySelector('.plugin-mini-muted').textContent = runtime;
      }).catch(() => {
        container.innerHTML = '<div class="plugin-mini-card">歌台状态不可用</div>';
      });
    },
  });
})();
