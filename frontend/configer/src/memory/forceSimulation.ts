// 质点弹簧物理引擎：完整移植 frontend/libs/configer/graph-renderer.js 的 ForceSimulation。

import type { GraphEdge, GraphNode } from '@/memory/types';

export interface ForceSimulationOptions {
  kRep?: number;
  kAtt?: number;
  restLen?: number;
  damping?: number;
  maxSpeed?: number;
  centerStr?: number;
  energyThreshold?: number;
}

export class ForceSimulation {
  nodes: GraphNode[] = [];
  edges: GraphEdge[] = [];

  kRep: number;
  kAtt: number;
  restLen: number;
  damping: number;
  maxSpeed: number;
  centerStr: number;
  energyThreshold: number;

  private _running = false;
  private _frameId: number | null = null;
  private _onTick: (() => void) | null = null;
  /** 停稳回调只触发一次；拖动时外部可清空。 */
  _onSettle: (() => void) | null = null;

  constructor(options?: ForceSimulationOptions) {
    const opts = options || {};
    this.kRep = opts.kRep || 6000;
    this.kAtt = opts.kAtt || 0.008;
    this.restLen = opts.restLen || 100;
    this.damping = opts.damping || 0.85;
    this.maxSpeed = opts.maxSpeed || 6;
    this.centerStr = opts.centerStr || 0.005;
    this.energyThreshold = opts.energyThreshold || 0.1;
  }

  setData(nodes: GraphNode[], edges: GraphEdge[]): void {
    this.nodes = nodes || [];
    this.edges = edges || [];
  }

  addNodes(newNodes: GraphNode[] | null | undefined, parentNode?: GraphNode | null): void {
    for (const node of newNodes || []) {
      if (this.nodes.some((existing) => existing.id === node.id)) continue;
      if (parentNode) {
        node.x = parentNode.x + (Math.random() - 0.5) * 40;
        node.y = parentNode.y + (Math.random() - 0.5) * 40;
      } else {
        node.x = (Math.random() - 0.5) * 100;
        node.y = (Math.random() - 0.5) * 100;
      }
      node.vx = 0;
      node.vy = 0;
      this.nodes.push(node);
    }
  }

  addEdges(newEdges: GraphEdge[] | null | undefined): void {
    for (const edge of newEdges || []) {
      const exists = this.edges.some(
        (item) =>
          item.source === edge.source &&
          item.target === edge.target &&
          item.type === edge.type,
      );
      if (!exists) this.edges.push(edge);
    }
  }

  start(onTick?: () => void, onSettle?: () => void): void {
    this._onTick = onTick || null;
    this._onSettle = onSettle || null;
    this._running = true;
    const loop = (): void => {
      if (!this._running) return;
      this.tick();
      if (this._onTick) this._onTick();
      this._frameId = requestAnimationFrame(loop);
    };
    loop();
  }

  stop(): void {
    this._running = false;
    if (this._frameId) {
      cancelAnimationFrame(this._frameId);
      this._frameId = null;
    }
  }

  tick(): void {
    const nodes = this.nodes;
    const edges = this.edges;
    const n = nodes.length;
    if (n === 0) return;

    const kRep = this.kRep;
    const kAtt = this.kAtt;
    const restLen = this.restLen;
    const damping = this.damping;
    const maxSpeed = this.maxSpeed;
    const centerStr = this.centerStr;
    let totalEnergy = 0;

    for (let i = 0; i < n; i++) {
      const ni = nodes[i];
      if (ni.fixed) continue;
      let fx = 0;
      let fy = 0;

      for (let j = 0; j < n; j++) {
        if (i === j) continue;
        const nj = nodes[j];
        const dx = ni.x - nj.x;
        const dy = ni.y - nj.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const rep = kRep / (dist * dist);
        fx += (dx / dist) * rep;
        fy += (dy / dist) * rep;
      }

      for (let k = 0; k < edges.length; k++) {
        const edge = edges[k];
        let other: string | null = null;
        if (edge.source === ni.id) other = edge.target;
        else if (edge.target === ni.id) other = edge.source;
        if (!other) continue;
        let oj: GraphNode | null = null;
        for (let m = 0; m < n; m++) {
          if (nodes[m].id === other) {
            oj = nodes[m];
            break;
          }
        }
        if (!oj || oj.id === ni.id) continue;
        const edx = oj.x - ni.x;
        const edy = oj.y - ni.y;
        const edist = Math.sqrt(edx * edx + edy * edy) || 1;
        const att = kAtt * (edist - restLen);
        fx += (edx / edist) * att;
        fy += (edy / edist) * att;
      }

      fx += -ni.x * centerStr;
      fy += -ni.y * centerStr;

      ni.vx = (ni.vx + fx) * damping;
      ni.vy = (ni.vy + fy) * damping;
      const speed = Math.sqrt(ni.vx * ni.vx + ni.vy * ni.vy);
      if (speed > maxSpeed) {
        ni.vx = (ni.vx / speed) * maxSpeed;
        ni.vy = (ni.vy / speed) * maxSpeed;
      }
      ni.x += ni.vx;
      ni.y += ni.vy;
      totalEnergy += ni.vx * ni.vx + ni.vy * ni.vy;
    }

    if (totalEnergy < this.energyThreshold && this._onSettle) {
      this._onSettle();
      this._onSettle = null;
    }
  }
}
