// Canvas 图谱渲染器：完整 TS 化 frontend/libs/configer/graph-renderer.js 的 GraphCanvas。
// 保留拖拽、滚轮缩放、fit、hover tooltip、双击回调、暂停/恢复、销毁。

import { ForceSimulation } from '@/memory/forceSimulation';
import type { GraphEdge, GraphNode } from '@/memory/types';

// ── 图着色表 ──
export const GRAPH_COLORS: Record<string, string> = {
  person: '#4a90d9',
  place: '#5cb85c',
  event: '#d9534f',
  concept: '#9b59b6',
  object: '#f0ad4e',
  document: '#5bc0de',
  custom: '#95a5a6',
  chat_record: '#27ae60',
  diary: '#e91e63',
  file: '#c47f3c',
  dir: '#8B7355',
  _unknown: '#bbb',
};

/** 图例/详情里按实体类型取色，未知类型回退 `custom` 灰。 */
export function graphNodeColor(node: GraphNode | null | undefined): string {
  const type = (node && (node.entity_type || node.type)) || 'custom';
  return GRAPH_COLORS[type] || GRAPH_COLORS._unknown;
}

export function graphTypeColor(type: unknown): string {
  return GRAPH_COLORS[String(type || 'custom')] || '#95a5a6';
}

// ── 图着色（边类型） ──
const GRAPH_EDGE_COLORS: Record<string, string> = {
  has_child: '#aaa',
  references: '#666',
  next: '#1abc9c',
  relates_to: '#3498db',
  part_of: '#e67e22',
  located_at: '#2ecc71',
  created_by: '#9b59b6',
  mentions: '#e74c3c',
};

const GRAPH_DASH: Record<string, number[]> = {
  has_child: [],
  references: [4, 4],
  next: [8, 4],
};

function edgeColor(type: string | undefined): string {
  return GRAPH_EDGE_COLORS[String(type || '')] || '#888';
}

function edgeDash(type: string | undefined): number[] {
  return GRAPH_DASH[String(type || '')] || [6, 4];
}

function ctxRoundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
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

function nodeShape(
  node: GraphNode,
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
): void {
  const type = node.entity_type || node.type || 'custom';
  ctx.save();
  ctx.beginPath();
  if (type === 'file' || type === 'dir') {
    const rr = r * 0.6;
    ctxRoundRect(ctx, x - r, y - r, r * 2, r * 2, rr);
  } else if (type === 'chat_record' || type === 'diary') {
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

export interface GraphCanvasOptions {
  onNodeClick?: (node: GraphNode) => void;
  onNodeDblClick?: (node: GraphNode) => void;
}

/**
 * 图渲染器。选中/拖拽等内部字段沿用旧名下划线命名，方便组件与测试对照旧实现。
 */
export class GraphCanvas {
  readonly canvas: HTMLCanvasElement;
  readonly simulation: ForceSimulation;

  private readonly _container: HTMLElement;
  private readonly _ctx: CanvasRenderingContext2D;
  private readonly _dpr: number;

  _width = 0;
  _height = 0;
  _viewX = 0;
  _viewY = 0;
  _viewScale = 1;
  _nodeRadius = 18;
  _degrees: Record<string, number> = {};
  _hoveredNode: GraphNode | null = null;
  _selectedNode: GraphNode | null = null;
  _highlightIds: string[] = [];
  _expanded: Record<string, boolean> = {};

  private _destroyed = false;
  private _draggedNode: GraphNode | null = null;
  private _isPanning = false;
  private _panStart = { x: 0, y: 0 };
  private _dragOffset = { x: 0, y: 0 };

  private _onNodeClick: ((node: GraphNode) => void) | null = null;
  private _onExpand: ((node: GraphNode) => void) | null = null;
  private _onNodeDblClick: ((node: GraphNode) => void) | null = null;

  private readonly _onCanvasMouseDown: (event: MouseEvent) => void;
  private readonly _onWindowMouseMove: (event: MouseEvent) => void;
  private readonly _onWindowMouseUp: () => void;
  private readonly _onCanvasClick: (event: MouseEvent) => void;
  private readonly _onCanvasDblClick: (event: MouseEvent) => void;
  private readonly _onCanvasWheel: (event: WheelEvent) => void;
  private readonly _onWindowResize: () => void;

  constructor(container: HTMLElement, options?: GraphCanvasOptions) {
    this._container = container;
    const canvas = document.createElement('canvas');
    canvas.style.display = 'block';
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    canvas.style.background = '#f8faff';
    container.appendChild(canvas);
    this.canvas = canvas;

    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D 上下文不可用');
    this._ctx = ctx;
    this._dpr = window.devicePixelRatio || 1;

    this.simulation = new ForceSimulation({
      kRep: 6000,
      kAtt: 0.008,
      restLen: 100,
      damping: 0.85,
      maxSpeed: 6,
      centerStr: 0.005,
      energyThreshold: 0.1,
    });

    if (options && typeof options.onNodeClick === 'function') this._onNodeClick = options.onNodeClick;
    if (options && typeof options.onNodeDblClick === 'function') {
      this._onNodeDblClick = options.onNodeDblClick;
    }

    this._onCanvasMouseDown = (event: MouseEvent): void => {
      const rect = this.canvas.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const world = this._screenToWorld(mx, my);
      const node = this._hitTest(world.x, world.y);
      if (node) {
        this._draggedNode = node;
        node.fixed = true;
        this._dragOffset = { x: world.x - node.x, y: world.y - node.y };
        this._selectedNode = node;
      } else {
        this._isPanning = true;
        this._panStart = { x: mx, y: my };
      }
    };

    this._onWindowMouseMove = (event: MouseEvent): void => {
      const rect = this.canvas.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const world = this._screenToWorld(mx, my);

      if (this._draggedNode) {
        this._draggedNode.x = world.x - this._dragOffset.x;
        this._draggedNode.y = world.y - this._dragOffset.y;
        return;
      }
      if (this._isPanning) {
        this._viewX += mx - this._panStart.x;
        this._viewY += my - this._panStart.y;
        this._panStart = { x: mx, y: my };
        return;
      }

      const hit = this._hitTest(world.x, world.y);
      if (hit !== this._hoveredNode) {
        this._hoveredNode = hit;
        this.canvas.style.cursor = hit ? 'pointer' : 'default';
        this.render();
      }
    };

    this._onWindowMouseUp = (): void => {
      if (this._draggedNode) {
        this._draggedNode.fixed = false;
        this._draggedNode = null;
        this.simulation._onSettle = null;
      }
      this._isPanning = false;
    };

    this._onCanvasClick = (event: MouseEvent): void => {
      const rect = this.canvas.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const world = this._screenToWorld(mx, my);
      const node = this._hitTest(world.x, world.y);
      if (node && this._onNodeClick) this._onNodeClick(node);
    };

    this._onCanvasDblClick = (event: MouseEvent): void => {
      const rect = this.canvas.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const world = this._screenToWorld(mx, my);
      const node = this._hitTest(world.x, world.y);
      if (!node) return;
      if (this._onExpand) this._onExpand(node);
      if (this._onNodeDblClick) this._onNodeDblClick(node);
    };

    this._onCanvasWheel = (event: WheelEvent): void => {
      event.preventDefault();
      const rect = this.canvas.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const delta = event.deltaY > 0 ? 0.9 : 1.1;
      const nextScale = this._viewScale * delta;
      if (nextScale < 0.1 || nextScale > 8) return;
      this._viewX = mx - (mx - this._viewX) * delta;
      this._viewY = my - (my - this._viewY) * delta;
      this._viewScale = nextScale;
      this.render();
    };

    this._onWindowResize = (): void => {
      this._resize();
      this.render();
    };

    this.canvas.addEventListener('mousedown', this._onCanvasMouseDown);
    window.addEventListener('mousemove', this._onWindowMouseMove);
    window.addEventListener('mouseup', this._onWindowMouseUp);
    this.canvas.addEventListener('click', this._onCanvasClick);
    this.canvas.addEventListener('dblclick', this._onCanvasDblClick);
    this.canvas.addEventListener('wheel', this._onCanvasWheel, { passive: false });
    window.addEventListener('resize', this._onWindowResize);

    this._resize();
  }

  // ── 坐标/命中 ──

  _resize(): void {
    const width = this._container.clientWidth;
    const height = this._container.clientHeight;
    this.canvas.width = width * this._dpr;
    this.canvas.height = height * this._dpr;
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    this._width = width;
    this._height = height;
    this._ctx.setTransform(1, 0, 0, 1, 0, 0);
    this._ctx.scale(this._dpr, this._dpr);
  }

  _worldToScreen(wx: number, wy: number): { x: number; y: number } {
    return { x: wx * this._viewScale + this._viewX, y: wy * this._viewScale + this._viewY };
  }

  _screenToWorld(sx: number, sy: number): { x: number; y: number } {
    return { x: (sx - this._viewX) / this._viewScale, y: (sy - this._viewY) / this._viewScale };
  }

  /** 度数 ≤1 → 13，2–4 → 17，≥5 → 22；无度数记录时回退统一的 _nodeRadius。 */
  _nodeRadiusFor(node: GraphNode | null | undefined): number {
    const degree = node ? this._degrees[node.id] : undefined;
    if (typeof degree !== 'number') return this._nodeRadius;
    if (degree <= 1) return 13;
    if (degree <= 4) return 17;
    return 22;
  }

  /**
   * 按当前边统计无向度数：(source,target,type) 三元组去重，A→B 与 B→A 视为同一条边。
   * 无邻接边的节点记为 0 度，未出现在图中的 id 才落回 _nodeRadius 缺省。
   */
  _recomputeDegrees(): void {
    const nodes = this.simulation.nodes;
    const degrees: Record<string, number> = {};
    for (const node of nodes) degrees[node.id] = 0;
    const seenEdge: Record<string, boolean> = {};
    for (const edge of this.simulation.edges) {
      const key = `${edge.source < edge.target ? `${edge.source}\u0000${edge.target}` : `${edge.target}\u0000${edge.source}`}\u0000${edge.type || ''}`;
      if (seenEdge[key]) continue;
      seenEdge[key] = true;
      if (typeof degrees[edge.source] === 'number') degrees[edge.source] += 1;
      if (typeof degrees[edge.target] === 'number') degrees[edge.target] += 1;
    }
    this._degrees = degrees;
  }

  _hitTest(wx: number, wy: number): GraphNode | null {
    const nodes = this.simulation.nodes;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const node = nodes[i];
      const r = this._nodeRadiusFor(node);
      const dx = wx - node.x;
      const dy = wy - node.y;
      if (dx * dx + dy * dy <= r * r * 2.5) return node;
    }
    return null;
  }

  // ── 数据 ──

  setData(nodes: GraphNode[], edges: GraphEdge[]): void {
    if (this._destroyed) return;
    // 最多渲染 500 个实体，超限则丢弃多余实体及其相关边，避免卡顿
    const MAX_RENDER_NODES = 500;
    let keptNodes = nodes;
    let keptEdges = edges;
    if (keptNodes && keptNodes.length > MAX_RENDER_NODES) {
      keptNodes = keptNodes.slice(0, MAX_RENDER_NODES);
      const keptIds: Record<string, boolean> = {};
      for (const node of keptNodes) keptIds[node.id] = true;
      keptEdges = (keptEdges || []).filter((edge) => keptIds[edge.source] && keptIds[edge.target]);
    }
    // 将单向边转为双向（正反向各一条）
    const bidirEdges = keptEdges.slice();
    for (const edge of keptEdges) {
      bidirEdges.push({
        source: edge.target,
        target: edge.source,
        type: edge.type,
        key: `${edge.key || ''}_r`,
      });
    }
    this.simulation.stop();
    this.simulation.setData(keptNodes, bidirEdges);
    this._recomputeDegrees();
    this._expanded = {};
    const cx = this._width / 2;
    const cy = this._height / 2;
    for (const node of keptNodes) {
      node.x = cx + (Math.random() - 0.5) * 200;
      node.y = cy + (Math.random() - 0.5) * 200;
      node.vx = 0;
      node.vy = 0;
    }
    this.render();
    this.simulation.start(
      () => this.render(),
      () => this.render(),
    );
  }

  addNodes(newNodes: GraphNode[], newEdges: GraphEdge[]): void {
    if (this._destroyed) return;
    // Find an existing parent node from the edges
    let parentId: string | null = null;
    if (newEdges && newEdges.length) {
      for (const edge of newEdges) {
        for (const node of this.simulation.nodes) {
          if (node.id === edge.source || node.id === edge.target) {
            parentId = node.id;
            break;
          }
        }
        if (parentId) break;
      }
    }
    let parentNode: GraphNode | null = null;
    if (parentId) {
      for (const node of this.simulation.nodes) {
        if (node.id === parentId) {
          parentNode = node;
          break;
        }
      }
    }
    this.simulation.addNodes(newNodes, parentNode);
    // Convert single-direction edges to bidirectional (same pattern as setData)
    const bidirEdges: GraphEdge[] = [];
    if (newEdges && newEdges.length) {
      for (const edge of newEdges) {
        bidirEdges.push(edge);
        bidirEdges.push({
          source: edge.target,
          target: edge.source,
          type: edge.type,
          key: `${edge.key || ''}_r`,
        });
      }
    }
    if (bidirEdges.length) this.simulation.addEdges(bidirEdges);
    this._recomputeDegrees();
    if (parentNode) this._expanded[parentNode.id] = true;
    this.render();
    this.simulation.stop();
    this.simulation.start(
      () => this.render(),
      () => this.render(),
    );
  }

  focusNode(id: string): void {
    const nodes = this.simulation.nodes;
    for (const node of nodes) {
      if (node.id === id) {
        this._viewX = this._width / 2 - node.x * this._viewScale;
        this._viewY = this._height / 2 - node.y * this._viewScale;
        this._selectedNode = node;
        this.render();
        return;
      }
    }
  }

  clearExpanded(): void {
    this._expanded = {};
  }

  highlightIds(ids: string[] | null | undefined): void {
    this._highlightIds = ids || [];
    this.render();
  }

  fitToScreen(): void {
    if (this._destroyed) return;
    const nodes = this.simulation.nodes;
    if (!nodes.length) return;
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const node of nodes) {
      if (node.x < minX) minX = node.x;
      if (node.x > maxX) maxX = node.x;
      if (node.y < minY) minY = node.y;
      if (node.y > maxY) maxY = node.y;
    }
    const gw = maxX - minX || 100;
    const gh = maxY - minY || 100;
    const pad = 80;
    const sx = (this._width - pad * 2) / gw;
    const sy = (this._height - pad * 2) / gh;
    this._viewScale = Math.min(sx, sy, 3);
    this._viewX = (this._width - (minX + maxX) * this._viewScale) / 2;
    this._viewY = (this._height - (minY + maxY) * this._viewScale) / 2;
    this.render();
  }

  /** 工具栏放大/缩小：与旧实现一致，直接乘系数（不做 0.1–8 钳制）。 */
  zoomBy(factor: number): void {
    if (this._destroyed) return;
    this._viewScale *= factor;
    this.render();
  }

  render(): void {
    if (this._destroyed) return;
    const ctx = this._ctx;
    const w = this._width;
    const h = this._height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w * this._dpr, h * this._dpr);
    ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);
    ctx.translate(this._viewX, this._viewY);
    ctx.scale(this._viewScale, this._viewScale);

    this._drawEdges(ctx);
    this._drawNodes(ctx);

    if (this._hoveredNode) this._drawTooltip(ctx, this._hoveredNode);
  }

  private _drawEdges(ctx: CanvasRenderingContext2D): void {
    const edges = this.simulation.edges;
    const nodeMap: Record<string, GraphNode> = {};
    for (const node of this.simulation.nodes) nodeMap[node.id] = node;

    for (const edge of edges) {
      const src = nodeMap[edge.source];
      const tgt = nodeMap[edge.target];
      if (!src || !tgt) continue;
      ctx.save();
      ctx.strokeStyle = edgeColor(edge.type);
      ctx.lineWidth = 1.5;
      ctx.globalAlpha = 0.5;
      ctx.setLineDash(edgeDash(edge.type));
      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);
      ctx.stroke();
      ctx.restore();
    }
  }

  private _drawNodes(ctx: CanvasRenderingContext2D): void {
    for (const node of this.simulation.nodes) {
      const r = this._nodeRadiusFor(node);
      const color = graphNodeColor(node);
      const isHover = node === this._hoveredNode;
      const isSelected = node === this._selectedNode;
      const isHighlighted = this._highlightIds.indexOf(node.id) >= 0;

      ctx.save();
      if (isSelected) {
        ctx.shadowColor = color;
        ctx.shadowBlur = 15;
      }
      if (isHighlighted) {
        ctx.shadowColor = '#f1c40f';
        ctx.shadowBlur = 20;
      }

      nodeShape(node, ctx, node.x, node.y, r);
      ctx.fillStyle = color;
      ctx.fill();

      if (isSelected || isHover) {
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2.5;
        ctx.stroke();
      }
      ctx.restore();

      const label = node.name || node.id;
      ctx.save();
      ctx.fillStyle = '#2c3e50';
      ctx.font = "11px Consolas, 'Courier New', monospace";
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      const labelWidth = ctx.measureText(label).width;
      if (labelWidth < r * 2 + 10) {
        ctx.fillText(label, node.x, node.y + r + 3);
      } else {
        ctx.fillText(
          `${label.substring(0, Math.floor((r * 2 + 10) / 7))}..`,
          node.x,
          node.y + r + 3,
        );
      }
      ctx.restore();
    }
  }

  private _drawTooltip(ctx: CanvasRenderingContext2D, node: GraphNode): void {
    const lines = [`ID: ${node.id}`, `Type: ${node.entity_type || node.type || '?'}`];
    if (node.name) lines[0] = `Name: ${node.name}`;
    const degree = this._degrees[node.id];
    lines.push(`度数: ${typeof degree === 'number' ? degree : 0}`);
    const desc = node.description ? String(node.description).replace(/\s+/g, ' ').trim() : '';
    if (desc) lines.push(`描述: ${desc.substring(0, 40)}`);
    const fontSize = 11;
    const pad = 6;
    let maxWidth = 0;
    ctx.save();
    ctx.font = `${fontSize}px Consolas, monospace`;
    for (const line of lines) {
      const measured = ctx.measureText(line).width;
      if (measured > maxWidth) maxWidth = measured;
    }
    const boxWidth = maxWidth + pad * 2;
    const boxHeight = lines.length * (fontSize + 4) + pad * 2;
    let tx = node.x + 20;
    const ty = node.y - boxHeight / 2;
    if (tx + boxWidth > this._width / this._viewScale - 10) tx = node.x - boxWidth - 10;
    ctx.fillStyle = 'rgba(44,62,80,0.9)';
    ctx.beginPath();
    ctxRoundRect(ctx, tx, ty, boxWidth, boxHeight, 4);
    ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    for (let i = 0; i < lines.length; i++) {
      ctx.fillText(lines[i], tx + pad, ty + pad + i * (fontSize + 4));
    }
    ctx.restore();
  }

  // ── 生命周期 ──

  pause(): void {
    this.simulation.stop();
  }

  resume(): void {
    if (this._destroyed) return;
    if (!this.simulation.nodes.length) return;
    this.simulation.stop();
    this.simulation.start(
      () => this.render(),
      () => this.render(),
    );
  }

  destroy(): void {
    if (this._destroyed) return;
    this._destroyed = true;
    this.simulation.stop();
    this.canvas.removeEventListener('mousedown', this._onCanvasMouseDown);
    this.canvas.removeEventListener('click', this._onCanvasClick);
    this.canvas.removeEventListener('dblclick', this._onCanvasDblClick);
    this.canvas.removeEventListener('wheel', this._onCanvasWheel);
    window.removeEventListener('mousemove', this._onWindowMouseMove);
    window.removeEventListener('mouseup', this._onWindowMouseUp);
    window.removeEventListener('resize', this._onWindowResize);
    if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
  }

  onNodeClick(callback: (node: GraphNode) => void): void {
    this._onNodeClick = callback;
  }

  onExpand(callback: (node: GraphNode) => void): void {
    this._onExpand = callback;
  }

  onNodeDblClick(callback: (node: GraphNode) => void): void {
    this._onNodeDblClick = callback;
  }
}
