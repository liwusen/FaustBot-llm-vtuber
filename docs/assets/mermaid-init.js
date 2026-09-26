/* 渲染 ```mermaid 围栏：pymdownx.superfences 输出 <pre class="mermaid"> */
(function () {
  "use strict";

  function render() {
    if (typeof mermaid === "undefined") {
      console.error("mermaid.js 未加载，```mermaid 代码块保持为纯文本。");
      return;
    }
    mermaid.initialize({ startOnLoad: false });
    mermaid.run({ querySelector: ".mermaid" });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
