// Memory page visualizations (echarts): word cloud / timeline / treemap.
// Classic script: exposes window.MemoryVisuals. Requires window.echarts; the
// wordCloud series additionally needs the echarts-wordcloud plugin (both are
// loaded from CDN by config-window.html). When either is unavailable the view
// degrades instead of throwing: no echarts -> "图表组件未加载", no wordCloud
// plugin -> frequency-ranked chip list.
//
// Public API:
//   MemoryVisuals.mount(kind, host, data, opts)  kind: "cloud"|"timeline"|"treemap"
//   MemoryVisuals.dispose(host)
//   MemoryVisuals.disposeAll()

(function () {
  const BLUE = "#3f6be8";
  const BLUE_MID = "#6f90ee";
  const BLUE_SOFT = "#b9cbf7";
  const BLUE_PALE = "#e6edf7";
  const GRAY = "#647086";
  const WHITE = "#ffffff";
  const BLUE_RAMP = [BLUE_PALE, BLUE_SOFT, BLUE_MID, BLUE];
  const CLOUD_COLORS = [BLUE, BLUE_MID, BLUE_MID, GRAY];

  const EMPTY_ECHARTS = "图表组件未加载（echarts 不可达）";
  const EMPTY_DATA = "暂无数据";
  const EMPTY_DATED = "暂无更新时间数据";
  const EMPTY_TAGS = "暂无标签数据";

  // canvas element -> live echarts instance, so disposeAll() can enumerate.
  // dispose()/clearHost() always remove the entry, so nothing leaks for hosts
  // that are torn down through this module.
  const registry = new Map();

  const DAY_MS = 24 * 60 * 60 * 1000;
  const CALENDAR_DAYS = 365;
  const CLOUD_MAX_WORDS = 60;
  const TREEMAP_NODE_LIMIT = 2000;
  const TREEMAP_LEAF_DEPTH = 2;

  function pad2(n) {
    return n < 10 ? `0${n}` : String(n);
  }

  function utcDayKey(date) {
    return `${date.getUTCFullYear()}-${pad2(date.getUTCMonth() + 1)}-${pad2(date.getUTCDate())}`;
  }

  function utcKeyToMs(key) {
    const parts = String(key || "").split("-");
    if (parts.length !== 3) return NaN;
    const ms = Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    return Number.isNaN(ms) ? NaN : ms;
  }

  // "2026-09-10T03:00:00Z" / any Date-parsable value -> UTC day key, else "".
  function parseUtcDayKey(value) {
    const raw = String(value == null ? "" : value).trim();
    if (!raw) return "";
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return "";
    return utcDayKey(date);
  }

  function escapeText(value) {
    const node = document.createElement("div");
    node.textContent = String(value == null ? "" : value);
    return node.innerHTML;
  }

  function fileList(value) {
    return Array.isArray(value) ? value : [];
  }

  function chunkCountOf(file) {
    const n = Number((file || {}).chunk_count);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  function emptyBox(text) {
    return el("div", "mem-empty", text);
  }

  // host markup: host > div.mem-viz > div.mem-viz-canvas (echarts target).
  function makeVizShell(host, emptyText) {
    if (emptyText) {
      host.append(emptyBox(emptyText));
      return null;
    }
    const wrap = el("div", "mem-viz");
    const canvas = el("div", "mem-viz-canvas");
    wrap.append(canvas);
    host.append(wrap);
    return canvas;
  }

  function clearHost(host) {
    const canvases = host.querySelectorAll(".mem-viz-canvas");
    for (const canvas of canvases) disposeCanvas(canvas);
    for (const child of Array.from(host.children)) {
      if (child.classList.contains("mem-viz") || child.classList.contains("mem-empty")) {
        child.remove();
      }
    }
  }

  function disposeCanvas(canvas) {
    let chart = registry.get(canvas) || null;
    registry.delete(canvas);
    const echarts = window.echarts;
    if (!chart && echarts && typeof echarts.getInstanceByDom === "function") {
      chart = echarts.getInstanceByDom(canvas) || null;
    }
    if (!chart) return;
    if (typeof chart.isDisposed === "function" && chart.isDisposed()) return;
    chart.dispose();
  }

  function createChart(canvas) {
    const chart = window.echarts.init(canvas);
    registry.set(canvas, chart);
    return chart;
  }

  // True only when echarts actually built a wordCloud series model. Checking the
  // returned option is not enough: an unregistered series type is kept in the
  // raw option but never modelled, so the chart would stay blank.
  function hasSeriesModel(chart, type) {
    try {
      if (typeof chart.getModel !== "function") return false;
      const model = chart.getModel();
      if (!model || typeof model.getSeriesByType !== "function") return false;
      return model.getSeriesByType(type).length > 0;
    } catch (err) {
      return false;
    }
  }

  function renderChips(canvas, entries, onPick) {
    const wrap = el("div", "mem-chips");
    for (const entry of entries) {
      const chip = el("button", "mem-chip", `${entry.name} (${entry.value})`);
      chip.type = "button";
      if (onPick) chip.addEventListener("click", () => onPick(entry.name));
      wrap.append(chip);
    }
    canvas.append(wrap);
  }

  // ── cloud ──────────────────────────────────────────────────────────────

  function collectTagFreq(files) {
    const counts = new Map();
    for (const file of files) {
      const tags = Array.isArray((file || {}).tags) ? file.tags : [];
      const seen = new Set();
      for (const raw of tags) {
        const tag = String(raw == null ? "" : raw).trim();
        if (!tag || seen.has(tag)) continue;
        seen.add(tag);
        counts.set(tag, (counts.get(tag) || 0) + 1);
      }
    }
    const entries = [];
    for (const [name, value] of counts) entries.push({ name, value });
    entries.sort((a, b) => (b.value - a.value) || a.name.localeCompare(b.name, "zh-Hans-CN"));
    return entries;
  }

  function cloudOption(entries) {
    return {
      animation: false,
      tooltip: { show: false },
      series: [{
        type: "wordCloud",
        shape: "circle",
        left: "center",
        top: "center",
        width: "96%",
        height: "96%",
        sizeRange: [12, 44],
        rotationRange: [0, 0],
        gridSize: 8,
        drawOutOfBound: false,
        layoutAnimation: false,
        textStyle: { fontWeight: "normal" },
        data: entries.map((entry, index) => ({
          name: entry.name,
          value: entry.value,
          textStyle: { color: CLOUD_COLORS[index % CLOUD_COLORS.length] },
        })),
      }],
    };
  }

  function mountCloud(host, data, opts) {
    const files = fileList(((data || {}).cloud || {}).files);
    const entries = collectTagFreq(files);
    if (!files.length) {
      makeVizShell(host, EMPTY_DATA);
      return null;
    }
    if (!entries.length) {
      makeVizShell(host, EMPTY_TAGS);
      return null;
    }
    const onPick = opts && opts.cloud && typeof opts.cloud.onPickTag === "function"
      ? opts.cloud.onPickTag
      : null;
    const top = entries.slice(0, CLOUD_MAX_WORDS);
    const canvas = makeVizShell(host, null);
    const chart = createChart(canvas);
    let live = false;
    try {
      chart.setOption(cloudOption(top));
      live = hasSeriesModel(chart, "wordCloud");
    } catch (err) {
      live = false;
    }
    if (live) return chart;
    // echarts-wordcloud missing/unusable -> degrade to a ranked chip list.
    registry.delete(canvas);
    if (typeof chart.isDisposed !== "function" || !chart.isDisposed()) chart.dispose();
    canvas.textContent = "";
    renderChips(canvas, top, onPick);
    return null;
  }

  // ── timeline ───────────────────────────────────────────────────────────

  function timelineOption(startKey, endKey, heat, cumFiles, cumChunks, maxDaily) {
    return {
      animation: false,
      tooltip: {
        trigger: "item",
        borderColor: BLUE_SOFT,
        textStyle: { color: GRAY, fontSize: 12 },
        formatter(params) {
          const value = params.value;
          if (params.seriesType === "heatmap") {
            const day = Array.isArray(value) ? value[0] : "";
            const count = Array.isArray(value) ? value[1] : 0;
            return `${escapeText(day)} 更新 ${Number(count) || 0} 个文件`;
          }
          const day = new Date(Array.isArray(value) ? value[0] : value);
          const label = Number.isNaN(day.getTime()) ? "" : utcDayKey(day);
          return `${escapeText(label)} ${escapeText(params.seriesName)}：${Number(Array.isArray(value) ? value[1] : 0) || 0}`;
        },
      },
      calendar: {
        top: 28,
        left: 56,
        right: 28,
        bottom: "48%",
        range: [startKey, endKey],
        cellSize: ["auto", 13],
        splitLine: { show: false },
        itemStyle: { color: BLUE_PALE, borderColor: WHITE, borderWidth: 1 },
        dayLabel: { firstDay: 1, nameMap: "ZH", color: GRAY, fontSize: 10 },
        monthLabel: { nameMap: "ZH", color: GRAY, fontSize: 11 },
        yearLabel: { show: false },
      },
      visualMap: {
        type: "continuous",
        min: 0,
        max: maxDaily,
        calculable: false,
        orient: "horizontal",
        left: "center",
        bottom: "40%",
        itemWidth: 12,
        itemHeight: 150,
        borderColor: BLUE_SOFT,
        indicatorStyle: { color: BLUE, borderColor: WHITE },
        textStyle: { color: GRAY, fontSize: 11 },
        inRange: { color: BLUE_RAMP },
      },
      grid: { left: 62, right: 62, top: "66%", height: "24%" },
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: BLUE_SOFT } },
        axisLabel: { color: GRAY, fontSize: 11 },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: "value",
          name: "文件数",
          nameTextStyle: { color: GRAY, fontSize: 11 },
          axisLine: { show: false },
          axisLabel: { color: GRAY, fontSize: 11 },
          splitLine: { lineStyle: { color: BLUE_PALE } },
        },
        {
          type: "value",
          name: "chunks",
          nameTextStyle: { color: GRAY, fontSize: 11 },
          axisLine: { show: false },
          axisLabel: { color: GRAY, fontSize: 11 },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "当日更新",
          type: "heatmap",
          coordinateSystem: "calendar",
          animation: false,
          data: heat,
          itemStyle: { borderColor: WHITE, borderWidth: 1 },
        },
        {
          name: "累计文件数",
          type: "line",
          yAxisIndex: 0,
          animation: false,
          showSymbol: false,
          data: cumFiles,
          lineStyle: { color: BLUE, width: 2 },
          itemStyle: { color: BLUE },
          areaStyle: { color: BLUE, opacity: 0.08 },
        },
        {
          name: "累计 chunk",
          type: "line",
          yAxisIndex: 1,
          animation: false,
          showSymbol: false,
          data: cumChunks,
          lineStyle: { color: GRAY, width: 2 },
          itemStyle: { color: GRAY },
        },
      ],
    };
  }

  function mountTimeline(host, data, opts) {
    const files = fileList(((data || {}).timeline || {}).files);
    if (!files.length) {
      makeVizShell(host, EMPTY_DATA);
      return null;
    }
    const endMs = utcKeyToMs(utcDayKey(new Date()));
    const startMs = endMs - (CALENDAR_DAYS - 1) * DAY_MS;
    const startKey = utcDayKey(new Date(startMs));
    const endKey = utcDayKey(new Date(endMs));
    const daily = new Map();
    for (const file of files) {
      const key = parseUtcDayKey((file || {}).updated_at);
      if (!key) continue;
      const ms = utcKeyToMs(key);
      if (Number.isNaN(ms) || ms < startMs || ms > endMs) continue;
      const bucket = daily.get(key) || { count: 0, chunks: 0 };
      bucket.count += 1;
      bucket.chunks += chunkCountOf(file);
      daily.set(key, bucket);
    }
    if (!daily.size) {
      makeVizShell(host, EMPTY_DATED);
      return null;
    }
    const heat = [];
    const cumFiles = [];
    const cumChunks = [];
    let totalFiles = 0;
    let totalChunks = 0;
    let maxDaily = 1;
    for (let i = 0; i < CALENDAR_DAYS; i++) {
      const key = utcDayKey(new Date(startMs + i * DAY_MS));
      const bucket = daily.get(key);
      if (bucket) {
        totalFiles += bucket.count;
        totalChunks += bucket.chunks;
        if (bucket.count > maxDaily) maxDaily = bucket.count;
      }
      heat.push([key, bucket ? bucket.count : 0]);
      cumFiles.push([key, totalFiles]);
      cumChunks.push([key, totalChunks]);
    }
    const canvas = makeVizShell(host, null);
    const chart = createChart(canvas);
    chart.setOption(timelineOption(startKey, endKey, heat, cumFiles, cumChunks, maxDaily));
    const onPick = opts && opts.timeline && typeof opts.timeline.onPickRange === "function"
      ? opts.timeline.onPickRange
      : null;
    if (onPick) {
      chart.on("click", (params) => {
        const value = params.value;
        const ms = params.seriesType === "heatmap"
          ? utcKeyToMs(Array.isArray(value) ? value[0] : value)
          : Number(Array.isArray(value) ? value[0] : value);
        if (!Number.isFinite(ms)) return;
        const key = utcDayKey(new Date(ms));
        onPick(key, key);
      });
    }
    return chart;
  }

  // ── treemap ────────────────────────────────────────────────────────────

  function isDirNode(node) {
    return String((node || {}).type || "") === "dir";
  }

  function countTreeNodes(node, cap) {
    if (!node || typeof node !== "object") return 0;
    let total = 1;
    for (const child of Array.isArray(node.children) ? node.children : []) {
      if (!isDirNode(child) && String(child.type || "") !== "file") continue;
      total += countTreeNodes(child, cap);
      if (total > cap) return total;
    }
    return total;
  }

  function dirOf(path) {
    const text = String(path || "");
    const idx = text.lastIndexOf("/");
    if (idx <= 0) return "/";
    return text.slice(0, idx);
  }

  // Leaf tile fill ramps blue → light with depth; containers are drawn by
  // echarts with itemStyle.borderColor (the gap colour), so their labels must
  // stay readable on that light background.
  function depthColor(depth) {
    if (depth <= 1) return BLUE;
    if (depth === 2) return BLUE_MID;
    if (depth === 3) return BLUE_SOFT;
    return BLUE_PALE;
  }

  function depthTextColor(depth) {
    return depth <= 2 ? WHITE : GRAY;
  }

  // node -> new datum (never mutates the input tree).
  function buildTreemapNode(node, depth, maxDepth, counter) {
    if (counter.count > TREEMAP_NODE_LIMIT) return null;
    counter.count += 1;
    const dir = isDirNode(node);
    const path = String(node.path || "/");
    const datum = {
      name: String(node.name || path),
      _memPath: path,
      _memIsDir: dir,
    };
    if (!dir) {
      datum.value = Math.max(chunkCountOf(node), 1);
      datum.itemStyle = { color: depthColor(depth) };
      datum.label = { color: depthTextColor(depth) };
      return datum;
    }
    const children = [];
    let sum = 0;
    if (depth < maxDepth) {
      for (const child of Array.isArray(node.children) ? node.children : []) {
        const type = String((child || {}).type || "");
        if (type !== "dir" && type !== "file") continue;
        const built = buildTreemapNode(child, depth + 1, maxDepth, counter);
        if (!built) break;
        children.push(built);
        sum += built.value;
      }
    }
    datum.value = Math.max(sum, 1);
    if (children.length) datum.children = children;
    // Nodes shallower than leafDepth render as containers (filled with the gap
    // colour), so their title band must be dark; deeper nodes render as coloured
    // leaves.
    const container = children.length > 0 && depth < TREEMAP_LEAF_DEPTH;
    const textColor = container ? GRAY : depthTextColor(depth);
    datum.itemStyle = { color: depthColor(depth) };
    datum.label = { color: textColor };
    datum.upperLabel = { color: textColor };
    return datum;
  }

  function treemapOption(datums, degraded) {
    return {
      animation: false,
      tooltip: {
        trigger: "item",
        borderColor: BLUE_SOFT,
        textStyle: { color: GRAY, fontSize: 12 },
        formatter(params) {
          const datum = params.data || {};
          const lines = [escapeText(datum._memPath || params.name)];
          lines.push(`${datum._memIsDir ? "目录" : "文件"} · 权重 ${Number(params.value) || 0}`);
          return lines.join("<br/>");
        },
      },
      series: [{
        type: "treemap",
        data: datums,
        animation: false,
        maxDepth: degraded ? 2 : 3,
        leafDepth: TREEMAP_LEAF_DEPTH,
        nodeClick: false,
        roam: false,
        breadcrumb: { show: false },
        width: "100%",
        height: "100%",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        label: { show: true, formatter: "{b}", fontSize: 12, overflow: "truncate" },
        upperLabel: { show: true, height: 20, fontSize: 12, overflow: "truncate" },
        itemStyle: { borderColor: BLUE_PALE, borderWidth: 1, gapWidth: 2 },
      }],
    };
  }

  function mountTreemap(host, data, opts) {
    const tree = ((data || {}).treemap || {}).tree;
    if (!tree || typeof tree !== "object") {
      makeVizShell(host, EMPTY_DATA);
      return null;
    }
    const nodeTotal = countTreeNodes(tree, TREEMAP_NODE_LIMIT);
    const degraded = nodeTotal > TREEMAP_NODE_LIMIT;
    const rootDatum = buildTreemapNode(tree, 0, degraded ? 2 : 3, { count: 0 });
    const children = rootDatum && rootDatum.children ? rootDatum.children : [];
    // A dir root without renderable children (empty scope, or only entities) has
    // nothing to draw.
    if (!rootDatum || (isDirNode(tree) && !children.length)) {
      makeVizShell(host, EMPTY_DATA);
      return null;
    }
    // The scope root itself is not drawn as a tile: the top level of the treemap
    // is its children, so leafDepth 2 exposes real files (and their dirs) and
    // every clickable tile has a path that onPickDir can use.
    const datums = children.length ? children : [rootDatum];
    const canvas = makeVizShell(host, null);
    const chart = createChart(canvas);
    chart.setOption(treemapOption(datums, degraded));
    const onPick = opts && opts.treemap && typeof opts.treemap.onPickDir === "function"
      ? opts.treemap.onPickDir
      : null;
    if (onPick) {
      chart.on("click", (params) => {
        const datum = params.data || {};
        const path = String(datum._memPath || "");
        if (!path) return;
        onPick(datum._memIsDir ? path : dirOf(path));
      });
    }
    return chart;
  }

  // ── public API ─────────────────────────────────────────────────────────

  function mount(kind, host, data, opts) {
    if (!host || typeof host.querySelectorAll !== "function") return null;
    clearHost(host);
    if (!window.echarts) {
      host.append(emptyBox(EMPTY_ECHARTS));
      return null;
    }
    if (kind === "cloud") return mountCloud(host, data, opts);
    if (kind === "timeline") return mountTimeline(host, data, opts);
    if (kind === "treemap") return mountTreemap(host, data, opts);
    return null;
  }

  function dispose(host) {
    if (!host || typeof host.querySelectorAll !== "function") return;
    clearHost(host);
  }

  function disposeAll() {
    for (const canvas of Array.from(registry.keys())) disposeCanvas(canvas);
    registry.clear();
  }

  window.MemoryVisuals = { mount, dispose, disposeAll };
})();
