// 共享线性图标 — HUD 与配置中心共用，避免 Emoji 与重复的 SVG 片段。
// 用法:
//   FaustIcons.svg('mic', { size: 13 })        → 返回可插入 innerHTML 的 <svg>
//   静态标记直接 <svg class="ui-icon"><use href="#i-mic"/></svg>（由 mount() 注入 sprite）
(function () {
  const ICONS = {
    mic: '<path d="M12 3.2a2.8 2.8 0 0 1 2.8 2.8v5.2a2.8 2.8 0 1 1-5.6 0V6A2.8 2.8 0 0 1 12 3.2Z"/>' +
      '<path d="M5.8 11.2a6.2 6.2 0 0 0 12.4 0"/><path d="M12 17.4V21"/><path d="M9.2 21h5.6"/>',
    chat: '<path d="M20 6.5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v13l4.5-3.5H18a2 2 0 0 0 2-2Z"/>',
    speaker: '<path d="M4 9.5h3L11.5 6v12L7 14.5H4z"/>' +
      '<path d="M15.5 9.2a4.2 4.2 0 0 1 0 5.6M18.2 6.6a7.6 7.6 0 0 1 0 10.8"/>',
    wave: '<path d="M4 10v4M8 7.5v9M12 5v14M16 8.5v7M20 10.5v3"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2.2"/>',
    spark: '<path d="M12 3.5l1.8 4.9 4.9 1.8-4.9 1.8L12 16.9l-1.8-4.9L5.3 10.2l4.9-1.8L12 3.5Z"/>' +
      '<path d="M18.5 15.5l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7.7-1.9Z"/>',
    tray: '<path d="M4 13.5v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>' +
      '<path d="M12 4v8.5"/><path d="M8.5 9.5 12 13l3.5-3.5"/>',
    gear: '<circle cx="12" cy="12" r="3.2"/>' +
      '<path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.64 5.64l1.56 1.56M16.8 16.8l1.56 1.56M18.36 5.64 16.8 7.2M7.2 16.8l-1.56 1.56"/>',
    broadcast: '<circle cx="12" cy="12" r="1.7"/>' +
      '<path d="M8.6 8.6a4.8 4.8 0 0 0 0 6.8M15.4 8.6a4.8 4.8 0 0 1 0 6.8"/>' +
      '<path d="M5.9 5.9a8.6 8.6 0 0 0 0 12.2M18.1 5.9a8.6 8.6 0 0 1 0 12.2"/>',
    gamepad: '<rect x="3.2" y="8" width="17.6" height="9.2" rx="4.2"/>' +
      '<path d="M8 11v3M6.5 12.5h3"/><circle cx="15.6" cy="11.8" r=".9"/><circle cx="17.6" cy="13.4" r=".9"/>',
    layout: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/>' +
      '<path d="M3.5 9.5h17M9.5 9.5v10"/>',
    doc: '<path d="M6.5 3.5h8l3 3v14h-11z"/>' +
      '<path d="M9.5 9.5h5M9.5 12.5h5M9.5 15.5h3"/>',
    close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    trash: '<path d="M4.5 7h15"/><path d="M9.5 7V4.8h5V7"/>' +
      '<path d="M6.5 7l.8 12.2h9.4L17.5 7"/><path d="M10 10.5v6M14 10.5v6"/>',
    play: '<path d="M8 5.6v12.8L19 12 8 5.6Z"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/>',
    grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>' +
      '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    server: '<rect x="4" y="4.5" width="16" height="6" rx="1.5"/><rect x="4" y="13.5" width="16" height="6" rx="1.5"/>' +
      '<path d="M7.5 7.5h.01M7.5 16.5h.01"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="9" r="1.5"/>' +
      '<path d="M4.5 17l4.8-4.5 3.2 3 2.4-2.2 4.6 4.2"/>',
    user: '<circle cx="12" cy="8" r="3.2"/><path d="M5 20c.8-3.6 3.5-5.5 7-5.5s6.2 1.9 7 5.5"/>',
    vr: '<rect x="3.2" y="8" width="17.6" height="9" rx="3"/>' +
      '<path d="M9 8v2.2M15 8v2.2"/><circle cx="8" cy="12.4" r="1.1"/><circle cx="16" cy="12.4" r="1.1"/>',
    package: '<path d="M12 3.2l8 4.4v8.8L12 20.8l-8-4.4V7.6l8-4.4Z"/>' +
      '<path d="M4 7.6l8 4.4 8-4.4M12 12v8.8"/>',
    check: '<circle cx="12" cy="12" r="8.4"/><path d="M8.5 12.2l2.3 2.3 4.8-5"/>',
    lock: '<rect x="5.5" y="10" width="13" height="9.5" rx="2"/>' +
      '<path d="M8.5 10V7.5a3.5 3.5 0 0 1 7 0V10"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    send: '<path d="M12 19V5M6.5 10.5 12 5l5.5 5.5"/>'
  };

  const SPRITE_ID = 'faust-icon-sprite';

  function svg(name, opts) {
    const options = opts || {};
    const size = Number(options.size) > 0 ? Number(options.size) : 16;
    const className = 'ui-icon' + (options.className ? ' ' + options.className : '');
    return `<svg class="${className}" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ICONS[name] || ''}</svg>`;
  }

  function mount() {
    if (!document.body || document.getElementById(SPRITE_ID)) return;
    const host = document.createElement('div');
    host.id = SPRITE_ID;
    host.setAttribute('aria-hidden', 'true');
    host.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden';
    host.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg">' +
      Object.entries(ICONS).map(([name, body]) =>
        `<symbol id="i-${name}" viewBox="0 0 24 24">${body}</symbol>`).join('') +
      '</svg>';
    document.body.prepend(host);
  }

  window.FaustIcons = { svg, mount, names: Object.keys(ICONS) };
  if (document.body) mount();
  else document.addEventListener('DOMContentLoaded', mount, { once: true });
})();
