import { describe, expect, it, vi } from 'vitest';
import { ForceSimulation } from '@/memory/forceSimulation';
import type { GraphNode } from '@/memory/types';

function node(id: string, x: number, y: number): GraphNode {
  return { id, x, y, vx: 0, vy: 0 };
}

describe('ForceSimulation', () => {
  it('斥力让相邻节点分离，中心力把它们拉回原点', () => {
    const sim = new ForceSimulation();
    const a = node('a', -10, 0);
    const b = node('b', 10, 0);
    sim.setData([a, b], []);
    sim.tick();
    expect(a.vx).toBeLessThan(0);
    expect(b.vx).toBeGreaterThan(0);

    const single = new ForceSimulation();
    const solo = node('solo', 100, 0);
    single.setData([solo], []);
    single.tick();
    expect(solo.vx).toBeLessThan(0);
    expect(Math.abs(solo.vx)).toBeLessThanOrEqual(single.maxSpeed);
  });

  it('fixed 节点不参与受力', () => {
    const sim = new ForceSimulation();
    const pinned = node('pinned', 50, 50);
    pinned.fixed = true;
    const moving = node('moving', 0, 0);
    sim.setData([pinned, moving], []);
    sim.tick();
    expect(pinned.x).toBe(50);
    expect(pinned.y).toBe(50);
    expect(pinned.vx).toBe(0);
    expect(pinned.vy).toBe(0);
    expect(moving.vx).not.toBe(0);
  });

  it('连线产生朝向 restLen 的吸引力', () => {
    const sim = new ForceSimulation({ kRep: 0, centerStr: 0, restLen: 100, kAtt: 0.01 });
    const a = node('a', 0, 0);
    const b = node('b', 200, 0);
    sim.setData([a, b], [{ source: 'a', target: 'b', type: 'references' }]);
    sim.tick();
    expect(a.vx).toBeGreaterThan(0);
    expect(b.vx).toBeLessThan(0);
  });

  it('addNodes 按 id 去重，新节点围绕父节点撒点', () => {
    const sim = new ForceSimulation();
    const parent = node('p', 0, 0);
    sim.setData([parent], []);
    sim.addNodes([node('c1', 0, 0), node('c1', 0, 0)], parent);
    expect(sim.nodes.map((item) => item.id)).toEqual(['p', 'c1']);
    expect(Math.abs(sim.nodes[1].x)).toBeLessThanOrEqual(20);
    expect(Math.abs(sim.nodes[1].y)).toBeLessThanOrEqual(20);
  });

  it('addEdges 按 source/target/type 三元组去重', () => {
    const sim = new ForceSimulation();
    sim.setData([], []);
    sim.addEdges([{ source: 'a', target: 'b', type: 'references' }]);
    sim.addEdges([
      { source: 'a', target: 'b', type: 'references' },
      { source: 'a', target: 'b', type: 'mentions' },
      { source: 'b', target: 'a', type: 'references' },
    ]);
    expect(sim.edges).toHaveLength(3);
  });

  it('能量低于阈值时 onSettle 只触发一次', () => {
    const sim = new ForceSimulation({ energyThreshold: 1e9 });
    sim.setData([node('a', 0, 0)], []);
    const settle = vi.fn();
    sim._onSettle = settle;
    sim.tick();
    expect(settle).toHaveBeenCalledTimes(1);
    sim.tick();
    expect(settle).toHaveBeenCalledTimes(1);
  });
});
