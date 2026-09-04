import { useEffect, useMemo, useRef, useState } from 'react';
import type { MemoryGraphNode, MemoryGraphSnapshot } from '../types';
import type { JSX } from 'react';

const KIND_COLORS: Record<string, string> = {
  ip: '#34d399',
  domain: '#60a5fa',
  port: '#fbbf24',
  protocol: '#a78bfa',
  os: '#fb7185',
  product: '#22d3ee',
  version: '#e879f9',
  cve: '#f87171',
  file: '#fde047',
  user: '#4ade80',
  path: '#c084fc',
  command: '#94a3b8',
  process: '#38bdf8',
  url: '#2dd4bf',
  email: '#f472b6',
  service: '#818cf8',
  package: '#facc15',
};
const PALETTE = ['#60a5fa', '#34d399', '#fbbf24', '#a78bfa', '#fb7185', '#22d3ee', '#f472b6', '#fde047', '#4ade80', '#c084fc'];

function groupColor(group?: string): string {
  if (group && KIND_COLORS[group]) return KIND_COLORS[group];
  if (!group) return '#94a3b8';
  let h = 0;
  for (let i = 0; i < group.length; i++) h = (h * 31 + group.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function kindLabel(kind: string): string {
  const map: Record<string, string> = {
    entity: '实体', experience: '经验', episode: '任务轨迹', fact: '事实',
  };
  return map[kind] || kind;
}

interface Pos { x: number; y: number }

function makePositions(snap: MemoryGraphSnapshot, W: number, H: number): Map<string, Pos> {
  const pos = new Map<string, Pos>();
  const exps = snap.nodes.filter(n => n.kind === 'experience');
  const ents = snap.nodes.filter(n => n.kind === 'entity');
  const others = snap.nodes.filter(n => n.kind !== 'experience' && n.kind !== 'entity');

  const expY = H / 2 - ((exps.length - 1) * 34) / 2;
  exps.forEach((n, i) => pos.set(n.id, { x: 118, y: expY + i * 34 }));

  const leftCount = Math.max(1, Math.min(others.length, 24));
  const leftH = (leftCount - 1) * 30;
  others.slice(0, leftCount).forEach((n, i) =>
    pos.set(n.id, { x: W - 118, y: H / 2 - leftH / 2 + i * 30 }));

  const visEnts = ents.slice(0, 80);
  if (visEnts.length > 0) {
    const R = Math.max(150, Math.min(310, 130 + visEnts.length * 7));
    const cx = W / 2;
    const cy = H / 2;
    visEnts.forEach((n, i) => {
      const a = (i / visEnts.length) * Math.PI * 2 - Math.PI / 2;
      pos.set(n.id, { x: cx + Math.cos(a) * R, y: cy + Math.sin(a) * R });
    });
  }
  return pos;
}

export function MemoryGraphView({ snapshot }: { snapshot: MemoryGraphSnapshot }): JSX.Element {
  const W = 960;
  const H = 700;
  const [selected, setSelected] = useState<MemoryGraphNode | null>(null);
  const [zoom, setZoom] = useState(1);
  const wrapRef = useRef<HTMLDivElement>(null);

  const { pos, index, resolvedEdges, skipped } = useMemo(() => {
    const index2 = new Map<string, MemoryGraphNode>();
    for (const n of snapshot.nodes) index2.set(n.id, n);
    const pos2 = makePositions(snapshot, W, H);
    const edgeIndex: Map<string, MemoryGraphNode> = new Map();
    for (const n of snapshot.nodes) {
      const bare = n.id.replace(/^(entity|experience|episode|fact):/, '');
      edgeIndex.set(bare, n);
    }
    const edges = snapshot.edges
      .map(e => {
        const a = pos2.has(e.source) ? e.source : null;
        const b = pos2.has(e.target) ? e.target : null;
        const sa = a ? a : (edgeIndex.has(e.source) && edgeIndex.get(e.source)!.id);
        const sb = b ? b : (edgeIndex.has(e.target) && edgeIndex.get(e.target)!.id);
        if (sa && sb && pos2.has(sa) && pos2.has(sb)) return { ...e, source: sa, target: sb };
        return null;
      })
      .filter((e): e is NonNullable<typeof e> => !!e);
    return { pos: pos2, index: index2, resolvedEdges: edges, skipped: snapshot.edges.length - edges.length };
  }, [snapshot]);

  useEffect(() => {
    if (selected && index.has(selected.id)) setSelected(index.get(selected.id)!);
  }, [snapshot, index, selected]);

  function nodeRadius(n: MemoryGraphNode): number {
    return n.kind === 'experience' ? 15 : n.kind === 'fact' ? 12 : 8;
  }

  function nodeFill(n: MemoryGraphNode): string {
    if (n.kind === 'experience') return n.status === 'invalidated' ? '#6b7280' : '#f59e0b';
    if (n.kind === 'fact') return n.status === 'invalidated' ? '#6b7280' : '#a78bfa';
    if (n.kind === 'episode') return n.status === 'failed' ? '#ef4444' : '#38bdf8';
    return groupColor(n.group || 'entity');
  }

  function isSelected(id: string): boolean {
    return !!selected && (selected.id === id);
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2 text-xs text-gray-500">
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5"><i className="w-3 h-3 rounded-full bg-amber-500 inline-block" />经验</span>
          <span className="flex items-center gap-1.5"><i className="w-3 h-3 rounded-full bg-violet-500 inline-block" />语义事实</span>
          <span className="flex items-center gap-1.5"><i className="w-3 h-3 rounded-full bg-sky-500 inline-block" />任务轨迹</span>
          <span className="flex items-center gap-1.5"><i className="w-3 h-3 rounded-full bg-emerald-500 inline-block" />实体（按类别着色）</span>
        </div>
        <div className="flex items-center gap-2">
          {skipped > 0 && <span>({skipped} 条跨库外链未绘制)</span>}
          <button onClick={() => setZoom(z => Math.max(0.5, z - 0.1))} className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 border border-gray-700">−</button>
          <span className="tabular-nums">{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom(z => Math.min(2, z + 0.1))} className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 border border-gray-700">+</button>
        </div>
      </div>
      <div className="relative border border-gray-800 rounded-xl overflow-hidden bg-gray-950/60 select-none">
        <div ref={wrapRef} className="overflow-auto max-h-[68vh]">
          <svg width={W * zoom} height={H * zoom} viewBox={`0 0 ${W} ${H}`} className="block">
            <g transform={`scale(${zoom})`}>
              {resolvedEdges.map((e, i) => {
                const a = pos.get(e.source)!;
                const b = pos.get(e.target)!;
                return (
                  <line key={i}
                    x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                    stroke={e.type === 'MENTIONS' ? '#475569' : '#7c3aed'}
                    strokeOpacity={isSelected(e.source) || isSelected(e.target) ? 0.9 : 0.25}
                    strokeWidth={isSelected(e.source) || isSelected(e.target) ? 1.6 : 0.8}
                  />
                );
              })}
              {snapshot.nodes.map(n => {
                const p = pos.get(n.id);
                if (!p) return null;
                const r = nodeRadius(n);
                const sel = isSelected(n.id);
                return (
                  <g key={n.id} transform={`translate(${p.x},${p.y})`} style={{ cursor: 'pointer' }}
                    onClick={() => setSelected(index.get(n.id) || n)}>
                    <circle r={r + 4} fill={sel ? '#ffffff' : 'transparent'} opacity={sel ? 0.35 : 0} />
                    <circle r={r} fill={nodeFill(n)} stroke="#0b1220" strokeWidth={1.2} />
                    <text textAnchor="middle" dy={r + 10}
                      fontSize={n.kind === 'experience' ? 10 : 8}
                      fill={sel ? '#fbbf24' : '#94a3b8'}
                      className="pointer-events-none">
                      {n.label.length > (n.kind === 'experience' ? 14 : 10) ? n.label.slice(0, (n.kind === 'experience' ? 13 : 9)) + '…' : n.label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>
        {selected && (
          <div className="absolute right-2 top-2 w-72 max-h-56 overflow-y-auto rounded-xl bg-gray-900/95 border border-gray-700/60 p-3 text-xs backdrop-blur">
            <div className="flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-gray-500">{kindLabel(selected.kind)}{selected.group ? ` · ${selected.group}` : ''}</span>
              <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-gray-300">✕</button>
            </div>
            <p className="mt-1 font-medium text-gray-200 break-words leading-snug">{selected.label}</p>
            {selected.status && <p className="mt-1 text-gray-400">状态：{selected.status}</p>}
            {selected.confidence !== undefined && (
              <p className="text-gray-400">置信度：{Math.round(selected.confidence * 100)}%</p>
            )}
            {typeof selected.mentions === 'number' && <p className="text-gray-400">提及：{selected.mentions}</p>}
            {Array.isArray(selected.tags) && selected.tags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {(selected.tags as string[]).slice(0, 6).map((t, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">{t}</span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
