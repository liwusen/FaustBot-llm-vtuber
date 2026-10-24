// Edge TTS 语音管理Modal

async function openEdgeTTSVoiceModal() {
  const modalBody = el("div", "edge-tts-voice-modal");

  // 搜索栏
  const searchBar = el("div", "search-bar");
  const searchInput = el("input", "search-input");
  searchInput.type = "text";
  searchInput.placeholder = "搜索语音名称、ID或特征...";
  const searchBtn = makeButton("搜索", () => loadEdgeTTSVoices(searchInput.value), "btn btn-primary");
  const refreshBtn = makeButton("刷新", () => loadEdgeTTSVoices("", true), "btn btn-secondary");
  searchBar.append(searchInput, searchBtn, refreshBtn);

  // 筛选栏
  const filterBar = el("div", "filter-bar");
  const languageSelect = el("select", "filter-select");
  languageSelect.innerHTML = '<option value="">所有语言</option>';
  const genderSelect = el("select", "filter-select");
  genderSelect.innerHTML = '<option value="">所有性别</option>';

  // 语音列表容器
  const voiceList = el("div", "voice-list");

  // 选中信息
  const selectedInfo = el("div", "selected-info");

  // 加载语言和性别选项
  async function loadFilters() {
    try {
      const [languages, genders] = await Promise.all([
        cfgApi("GET", "/faust/edge-tts/languages"),
        cfgApi("GET", "/faust/edge-tts/genders")
      ]);

      languages.languages.forEach(lang => {
        const option = el("option");
        option.value = lang;
        option.textContent = lang;
        languageSelect.appendChild(option);
      });

      genders.genders.forEach(gender => {
        const option = el("option");
        option.value = gender;
        option.textContent = gender;
        genderSelect.appendChild(option);
      });
    } catch (error) {
      console.error('加载筛选器失败:', error);
    }
  }

  // 加载语音列表
  async function loadEdgeTTSVoices(searchQuery = "", forceRefresh = false) {
    try {
      voiceList.innerHTML = '<div class="voice-empty">加载中...</div>';

      const language = languageSelect.value;
      const gender = genderSelect.value;

      if (forceRefresh) {
        await cfgApi("POST", "/faust/edge-tts/cache/refresh", {});
      }

      const data = await cfgApi("GET", "/faust/edge-tts/voices/search", null, {
        q: searchQuery,
        language: language || null,
        gender: gender || null,
      });

      voiceList.innerHTML = '';

      if (data.voices.length === 0) {
        voiceList.innerHTML = '<div class="voice-empty">未找到匹配的语音</div>';
        return;
      }

      data.voices.forEach(voice => {
        const voiceItem = el("div", "voice-item");

        voiceItem.innerHTML = `
          <div class="voice-name">${voice.name}</div>
          <div class="voice-meta">标识：${voice.voice_id}</div>
          <div class="voice-meta">语言：${voice.language} | 性别：${voice.gender}</div>
          <div class="voice-meta dim">特征: ${voice.voice_personalities}</div>
        `;

        voiceItem.addEventListener('click', () => selectVoice(voice, voiceItem));
        voiceList.appendChild(voiceItem);
      });

    } catch (error) {
      console.error('加载语音列表失败:', error);
      voiceList.innerHTML = '<div class="voice-empty is-error">加载失败</div>';
    }
  }

  // 选择语音
  function selectVoice(voice, voiceItem) {
    voiceList.querySelectorAll('.voice-item').forEach(item => {
      item.classList.remove('is-selected');
    });

    voiceItem.classList.add('is-selected');

    selectedInfo.innerHTML = `
      <div><strong>已选择: ${voice.name}</strong></div>
      <div>语音标识：${voice.voice_id}</div>
      <div>语言：${voice.language} | 性别：${voice.gender}</div>
      <div>特征: ${voice.voice_personalities}</div>
      <button onclick="confirmEdgeTTSVoice('${voice.voice_id}', '${voice.name.replace(/'/g, "\\'")}')">
        确认选择
      </button>
    `;
  }

  // 确认选择
  window.confirmEdgeTTSVoice = function(voiceId, voiceName) {
    updateValue("public", "EDGE_TTS_VOICE", voiceId);
    const voiceField = document.querySelector('input[name="EDGE_TTS_VOICE"]');
    if (voiceField) {
      voiceField.value = voiceId;
      voiceField.dispatchEvent(new Event('input', { bubbles: true }));
      voiceField.dispatchEvent(new Event('change', { bubbles: true }));
    }
    closeModal();
    showBanner('success', `已选择语音: ${voiceName}`);
  };

  // 初始化
  modalBody.append(searchBar, filterBar, voiceList, selectedInfo);
  await loadFilters();
  await loadEdgeTTSVoices();
  openModal("Edge TTS 语音管理", [modalBody]);
}
