// ── 图着色表 ──
var GRAPH_COLORS = {
  person: "#4a90d9", place: "#5cb85c", event: "#d9534f",
  concept: "#9b59b6", object: "#f0ad4e", document: "#5bc0de",
  custom: "#95a5a6",
  chat_record: "#27ae60", diary: "#e91e63",
  file: "#c47f3c", dir: "#8B7355",
  _unknown: "#bbb",
};

function ctxRoundRect(ctx, x, y, w, h, r) {
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

// ── 图着色（边类型） ──
var GRAPH_EDGE_COLORS = {
  has_child: "#aaa",
  references: "#666",
  next: "#1abc9c",
  relates_to: "#3498db",
  part_of: "#e67e22",
  located_at: "#2ecc71",
  created_by: "#9b59b6",
  mentions: "#e74c3c",
};

var GRAPH_DASH = {
  has_child: [], references: [4, 4], next: [8, 4],
};

function _nodeColor(nd) {
  var t = (nd && nd.entity_type) || (nd && nd.type) || "custom";
  return GRAPH_COLORS[t] || GRAPH_COLORS._unknown;
}

function _edgeColor(ed) {
  return GRAPH_EDGE_COLORS[ed] || "#888";
}

function _edgeDash(ed) {
  return GRAPH_DASH[ed] || [6, 4];
}

function _nodeShape(nd, ctx, x, y, r) {
  var t = (nd && nd.entity_type) || (nd && nd.type) || "custom";
  ctx.save();
  ctx.beginPath();
  if (t === "file" || t === "dir") {
    var rr = r * 0.6;
    ctxRoundRect(ctx, x - r, y - r, r * 2, r * 2, rr);
  } else if (t === "chat_record" || t === "diary") {
    ctx.moveTo(x, y - r);
    ctx.lineTo(x + r * 0.7, y);
    ctx.lineTo(x, y + r);
    ctx.lineTo(x - r * 0.7, y);
    ctx.closePath();
  } else {
    ctx.arc(x, y, r, 0, Math.PI * 2);
  }
  ctx.restore();
}

// ── 质点弹簧物理引擎 ──
function ForceSimulation(options) {
  this.nodes = [];
  this.edges = [];
  this.kRep = (options && options.kRep) || 6000;
  this.kAtt = (options && options.kAtt) || 0.008;
  this.restLen = (options && options.restLen) || 100;
  this.damping = (options && options.damping) || 0.85;
  this.maxSpeed = (options && options.maxSpeed) || 6;
  this.centerStr = (options && options.centerStr) || 0.005;
  this.energyThreshold = (options && options.energyThreshold) || 0.1;
  this._running = false;
  this._frameId = null;
  this._onTick = null;
  this._onSettle = null;
}

ForceSimulation.prototype.setData = function (nodes, edges) {
  this.nodes = nodes || [];
  this.edges = edges || [];
};

ForceSimulation.prototype.addNodes = function (newNodes, parentNode) {
  var self = this;
  (newNodes || []).forEach(function (n) {
    if (self.nodes.some(function (e) { return e.id === n.id; })) return;
    if (parentNode) {
      n.x = parentNode.x + (Math.random() - 0.5) * 40;
      n.y = parentNode.y + (Math.random() - 0.5) * 40;
    } else {
      n.x = (Math.random() - 0.5) * 100;
      n.y = (Math.random() - 0.5) * 100;
    }
    n.vx = 0; n.vy = 0;
    self.nodes.push(n);
  });
};

ForceSimulation.prototype.addEdges = function (newEdges) {
  var self = this;
  (newEdges || []).forEach(function (e) {
    var exists = self.edges.some(function (x) { return x.source === e.source && x.target === e.target && x.type === e.type; });
    if (!exists) self.edges.push(e);
  });
};

ForceSimulation.prototype.start = function (onTick, onSettle) {
  var self = this;
  this._onTick = onTick;
  this._onSettle = onSettle;
  this._running = true;
  function loop() {
    if (!self._running) return;
    self.tick();
    if (self._onTick) self._onTick();
    self._frameId = requestAnimationFrame(loop);
  }
  loop();
};

ForceSimulation.prototype.stop = function () {
  this._running = false;
  if (this._frameId) { cancelAnimationFrame(this._frameId); this._frameId = null; }
};

ForceSimulation.prototype.tick = function () {
  var nodes = this.nodes, edges = this.edges;
  var n = nodes.length;
  if (n === 0) return;

  var kRep = this.kRep;
  var kAtt = this.kAtt;
  var restLen = this.restLen;
  var damp = this.damping;
  var maxSpd = this.maxSpeed;
  var centerStr = this.centerStr;
  var totalEnergy = 0;

  for (var i = 0; i < n; i++) {
    var ni = nodes[i];
    if (ni.fixed) continue;
    var fx = 0, fy = 0;

    for (var j = 0; j < n; j++) {
      if (i === j) continue;
      var nj = nodes[j];
      var dx = ni.x - nj.x;
      var dy = ni.y - nj.y;
      var dist = Math.sqrt(dx * dx + dy * dy) || 1;
      var rep = kRep / (dist * dist);
      fx += (dx / dist) * rep;
      fy += (dy / dist) * rep;
    }

    for (var k = 0; k < edges.length; k++) {
      var e = edges[k];
      var other = null;
      if (e.source === ni.id) other = e.target;
      else if (e.target === ni.id) other = e.source;
      if (!other) continue;
      var oj = null;
      for (var m = 0; m < n; m++) {
        if (nodes[m].id === other) { oj = nodes[m]; break; }
      }
      if (!oj || oj.id === ni.id) continue;
      var edx = oj.x - ni.x;
      var edy = oj.y - ni.y;
      var edist = Math.sqrt(edx * edx + edy * edy) || 1;
      var att = kAtt * (edist - restLen);
      fx += (edx / edist) * att;
      fy += (edy / edist) * att;
    }

    fx += -ni.x * centerStr;
    fy += -ni.y * centerStr;

    ni.vx = (ni.vx + fx) * damp;
    ni.vy = (ni.vy + fy) * damp;
    var spd = Math.sqrt(ni.vx * ni.vx + ni.vy * ni.vy);
    if (spd > maxSpd) { ni.vx = (ni.vx / spd) * maxSpd; ni.vy = (ni.vy / spd) * maxSpd; }
    ni.x += ni.vx;
    ni.y += ni.vy;
    totalEnergy += ni.vx * ni.vx + ni.vy * ni.vy;
  }

  if (totalEnergy < this.energyThreshold && this._onSettle) {
    this._onSettle();
    this._onSettle = null;
  }
};

// ── Canvas 图谱渲染器 ──
function GraphCanvas(container, options) {
  this._container = container;
  this._options = options || {};

  this.canvas = document.createElement("canvas");
  this.canvas.style.display = "block";
  this.canvas.style.width = "100%";
  this.canvas.style.height = "100%";
  this.canvas.style.background = "#f8faff";
  container.appendChild(this.canvas);

  this._ctx = this.canvas.getContext("2d");
  this._dpr = window.devicePixelRatio || 1;

  this.simulation = new ForceSimulation({
    kRep: 6000, kAtt: 0.008, restLen: 100,
    damping: 0.85, maxSpeed: 6, centerStr: 0.005,
    energyThreshold: 0.1,
  });

  this._viewX = 0;
  this._viewY = 0;
  this._viewScale = 1;
  this._nodeRadius = 18;

  this._hoveredNode = null;
  this._selectedNode = null;
  this._draggedNode = null;
  this._isPanning = false;
  this._panStart = { x: 0, y: 0 };
  this._dragOffset = { x: 0, y: 0 };
  this._highlightIds = [];
  this._expanded = {};
  this._onNodeClick = null;

  this._resize();
  this._bindEvents();
}

GraphCanvas.prototype._resize = function () {
  var w = this._container.clientWidth;
  var h = this._container.clientHeight;
  this.canvas.width = w * this._dpr;
  this.canvas.height = h * this._dpr;
  this.canvas.style.width = w + "px";
  this.canvas.style.height = h + "px";
  this._width = w;
  this._height = h;
  this._ctx.setTransform(1, 0, 0, 1, 0, 0);
  this._ctx.scale(this._dpr, this._dpr);
};

GraphCanvas.prototype._worldToScreen = function (wx, wy) {
  return { x: wx * this._viewScale + this._viewX, y: wy * this._viewScale + this._viewY };
};

GraphCanvas.prototype._screenToWorld = function (sx, sy) {
  return { x: (sx - this._viewX) / this._viewScale, y: (sy - this._viewY) / this._viewScale };
};

GraphCanvas.prototype._bindEvents = function () {
  var self = this;

  this.canvas.addEventListener("mousedown", function (e) {
    var r = self.canvas.getBoundingClientRect();
    var mx = e.clientX - r.left, my = e.clientY - r.top;
    var w = self._screenToWorld(mx, my);
    var node = self._hitTest(w.x, w.y);
    if (node) {
      self._draggedNode = node;
      node.fixed = true;
      self._dragOffset = { x: w.x - node.x, y: w.y - node.y };
      self._selectedNode = node;
    } else {
      self._isPanning = true;
      self._panStart = { x: mx, y: my };
    }
  });

  window.addEventListener("mousemove", function (e) {
    var r = self.canvas.getBoundingClientRect();
    var mx = e.clientX - r.left, my = e.clientY - r.top;
    var w = self._screenToWorld(mx, my);

    if (self._draggedNode) {
      self._draggedNode.x = w.x - self._dragOffset.x;
      self._draggedNode.y = w.y - self._dragOffset.y;
      return;
    }
    if (self._isPanning) {
      self._viewX += mx - self._panStart.x;
      self._viewY += my - self._panStart.y;
      self._panStart = { x: mx, y: my };
      return;
    }

    var hit = self._hitTest(w.x, w.y);
    if (hit !== self._hoveredNode) {
      self._hoveredNode = hit;
      self.canvas.style.cursor = hit ? "pointer" : "default";
      self.render();
    }
  });

  window.addEventListener("mouseup", function () {
    if (self._draggedNode) {
      self._draggedNode.fixed = false;
      self._draggedNode = null;
      self.simulation._onSettle = null;
    }
    self._isPanning = false;
  });

  this.canvas.addEventListener("click", function (e) {
    var r = self.canvas.getBoundingClientRect();
    var mx = e.clientX - r.left, my = e.clientY - r.top;
    var w = self._screenToWorld(mx, my);
    var node = self._hitTest(w.x, w.y);
    if (node && self._onNodeClick) self._onNodeClick(node);
  });

  this.canvas.addEventListener("dblclick", function (e) {
    var r = self.canvas.getBoundingClientRect();
    var mx = e.clientX - r.left, my = e.clientY - r.top;
    var w = self._screenToWorld(mx, my);
    var node = self._hitTest(w.x, w.y);
    if (node && self._onExpand) self._onExpand(node);
  });

  this.canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    var r = self.canvas.getBoundingClientRect();
    var mx = e.clientX - r.left, my = e.clientY - r.top;
    var delta = e.deltaY > 0 ? 0.9 : 1.1;
    var ns = self._viewScale * delta;
    if (ns < 0.1 || ns > 8) return;
    self._viewX = mx - (mx - self._viewX) * delta;
    self._viewY = my - (my - self._viewY) * delta;
    self._viewScale = ns;
    self.render();
  });

  window.addEventListener("resize", function () { self._resize(); self.render(); });
};

GraphCanvas.prototype._hitTest = function (wx, wy) {
  var r = this._nodeRadius;
  var nodes = this.simulation.nodes;
  for (var i = nodes.length - 1; i >= 0; i--) {
    var n = nodes[i];
    var dx = wx - n.x, dy = wy - n.y;
    if (dx * dx + dy * dy <= r * r * 2.5) return n;
  }
  return null;
};

GraphCanvas.prototype.setData = function (nodes, edges) {
  var self = this;
  this.simulation.stop();
  this.simulation.setData(nodes, edges);
  this._expanded = {};
  var cx = this._width / 2, cy = this._height / 2;
  nodes.forEach(function (n) {
    n.x = cx + (Math.random() - 0.5) * 200;
    n.y = cy + (Math.random() - 0.5) * 200;
    n.vx = 0; n.vy = 0;
  });
  this.render();
  this.simulation.start(function () { self.render(); }, function () { self.render(); });
};

GraphCanvas.prototype.addNodes = function (newNodes, newEdges) {
  var self = this;
  var parentId = null;
  if (newEdges && newEdges.length) {
    for (var i = 0; i < newEdges.length; i++) {
      var e = newEdges[i];
      var found = null;
      for (var j = 0; j < this.simulation.nodes.length; j++) {
        if (this.simulation.nodes[j].id === e.source || this.simulation.nodes[j].id === e.target) {
          found = e; break;
        }
      }
      if (found) { parentId = found.source; break; }
    }
  }
  var parentNode = null;
  if (parentId) {
    for (var k = 0; k < this.simulation.nodes.length; k++) {
      if (this.simulation.nodes[k].id === parentId) { parentNode = this.simulation.nodes[k]; break; }
    }
  }
  this.simulation.addNodes(newNodes, parentNode);
  this.simulation.addEdges(newEdges);
  if (parentNode) this._expanded[parentNode.id] = true;
  this.render();
  this.simulation.stop();
  this.simulation.start(function () { self.render(); }, function () { self.render(); });
};

GraphCanvas.prototype.focusNode = function (id) {
  var nodes = this.simulation.nodes;
  for (var i = 0; i < nodes.length; i++) {
    if (nodes[i].id === id) {
      this._viewX = this._width / 2 - nodes[i].x * this._viewScale;
      this._viewY = this._height / 2 - nodes[i].y * this._viewScale;
      this._selectedNode = nodes[i];
      this.render();
      return;
    }
  }
};

GraphCanvas.prototype.highlightIds = function (ids) {
  this._highlightIds = ids || [];
  this.render();
};

GraphCanvas.prototype.fitToScreen = function () {
  var nodes = this.simulation.nodes;
  if (!nodes.length) return;
  var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  nodes.forEach(function (n) {
    if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
    if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
  });
  var gw = maxX - minX || 100, gh = maxY - minY || 100;
  var pad = 80;
  var sx = (this._width - pad * 2) / gw;
  var sy = (this._height - pad * 2) / gh;
  this._viewScale = Math.min(sx, sy, 3);
  this._viewX = (this._width - (minX + maxX) * this._viewScale) / 2;
  this._viewY = (this._height - (minY + maxY) * this._viewScale) / 2;
  this.render();
};

GraphCanvas.prototype.render = function () {
  var ctx = this._ctx;
  var w = this._width, h = this._height;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, w * this._dpr, h * this._dpr);
  ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);
  ctx.translate(this._viewX, this._viewY);
  ctx.scale(this._viewScale, this._viewScale);

  this._drawEdges(ctx);
  this._drawNodes(ctx);

  if (this._hoveredNode) this._drawTooltip(ctx, this._hoveredNode);
};

GraphCanvas.prototype._drawEdges = function (ctx) {
  var edges = this.simulation.edges;
  var nodes = this.simulation.nodes;
  var nodeMap = {};
  nodes.forEach(function (n) { nodeMap[n.id] = n; });

  for (var i = 0; i < edges.length; i++) {
    var e = edges[i];
    var src = nodeMap[e.source], tgt = nodeMap[e.target];
    if (!src || !tgt) continue;
    var color = _edgeColor(e.type);
    var dash = _edgeDash(e.type);

    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.globalAlpha = 0.5;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(src.x, src.y);
    ctx.lineTo(tgt.x, tgt.y);
    ctx.stroke();
    ctx.restore();
  }
};

GraphCanvas.prototype._drawNodes = function (ctx) {
  var nodes = this.simulation.nodes;
  var nodeRadius = this._nodeRadius;
  var self = this;

  for (var i = 0; i < nodes.length; i++) {
    var n = nodes[i];
    var r = nodeRadius;
    var color = _nodeColor(n);
    var isHover = n === this._hoveredNode;
    var isSel = n === this._selectedNode;
    var isHL = this._highlightIds.indexOf(n.id) >= 0;

    ctx.save();

    if (isSel) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 15;
    }
    if (isHL) {
      ctx.shadowColor = "#f1c40f";
      ctx.shadowBlur = 20;
    }

    _nodeShape(n, ctx, n.x, n.y, r);
    ctx.fillStyle = color;
    ctx.fill();

    if (isSel || isHover) {
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2.5;
      ctx.stroke();
    }

    ctx.restore();

    var label = n.name || n.id;
    ctx.save();
    ctx.fillStyle = "#2c3e50";
    ctx.font = "11px Consolas, 'Courier New', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    var lw = ctx.measureText(label).width;
    if (lw < r * 2 + 10) {
      ctx.fillText(label, n.x, n.y + r + 3);
    } else {
      ctx.fillText(label.substring(0, Math.floor((r * 2 + 10) / 7)) + "..", n.x, n.y + r + 3);
    }
    ctx.restore();
  }
};

GraphCanvas.prototype._drawTooltip = function (ctx, n) {
  var lines = ["ID: " + n.id, "Type: " + ((n.entity_type || n.type || "?"))];
  if (n.name) lines[0] = "Name: " + n.name;
  var fSize = 11;
  var pad = 6;
  var maxW = 0;
  ctx.save();
  ctx.font = fSize + "px Consolas, monospace";
  for (var i = 0; i < lines.length; i++) {
    var m = ctx.measureText(lines[i]).width;
    if (m > maxW) maxW = m;
  }
  var bw = maxW + pad * 2, bh = lines.length * (fSize + 4) + pad * 2;
  var tx = n.x + 20, ty = n.y - bh / 2;
  if (tx + bw > this._width / this._viewScale - 10) tx = n.x - bw - 10;
  ctx.fillStyle = "rgba(44,62,80,0.9)";
  ctx.beginPath();
  ctxRoundRect(ctx, tx, ty, bw, bh, 4);
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  for (var j = 0; j < lines.length; j++) {
    ctx.fillText(lines[j], tx + pad, ty + pad + j * (fSize + 4));
  }
  ctx.restore();
};

GraphCanvas.prototype.destroy = function () {
  this.simulation.stop();
  this._container.removeChild(this.canvas);
};

GraphCanvas.prototype.onNodeClick = function (cb) { this._onNodeClick = cb; };
GraphCanvas.prototype.onExpand = function (cb) { this._onExpand = cb; };
