import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { getBlackboard, addBlackboardHint } from '../api/client';
import type { BlackboardSnapshot, BlackboardNode as BBNode } from '../types';
import type { JSX } from 'react';
import cytoscape, { type Core, type ElementDefinition, type StylesheetJson } from 'cytoscape';
import elk from 'cytoscape-elk';

cytoscape.use(elk);

interface Props {
  sessionId: string | null;
  open: boolean;
  onClose: () => void;
}

// ── 节点视觉规格 ──
interface Vis {
  color: string;
  fill: string;
  stroke: string;
  icon: string;
  label: string;
}

const KIND_VIS: Record<string, Vis> = {
  origin:     { color: '#e5e7eb', fill: '#374151',  stroke: '#6b7280', icon: '⊙', label: '起点' },
  goal:       { color: '#e9d5ff', fill: '#4c1d95',  stroke: '#a855f7', icon: '◎', label: '目标' },
  intent:     { color: '#a5f3fc', fill: '#164e63',  stroke: '#0891b2', icon: '◇', label: '探索' },
  fact:       { color: '#dbeafe', fill: '#1e40af',  stroke: '#3b82f6', icon: '✓', label: '结论' },
  hint:       { color: '#fde68a', fill: '#78350f',  stroke: '#d97706', icon: '!', label: '提示' },
};

// Fact role 子类型视觉覆盖
const FACT_ROLE_VIS: Record<string, { icon: string; stroke: string; label: string }> = {
  handoff:   { icon: '📋', stroke: '#d97706', label: '交接' },
  target:    { icon: '⌖', stroke: '#f97316', label: '端点' },
  evidence:  { icon: '≡', stroke: '#60a5fa', label: '证据' },
};

// ── Fact 按威胁分着色（与后端 scoring.py 的 severity 对齐）──
// 高分（已确认 RCE/凭据）→ 红系；中分（普通发现）→ 蓝系
// 低分（侦察指纹）→ 青系；排除性结论 → 灰系
const SEVERITY_VIS: Record<string, { fill: string; stroke: string; label: string }> = {
  critical: { fill: '#7f1d1d', stroke: '#ef4444', label: '高危' },
  normal:   { fill: '#1e3a8a', stroke: '#3b82f6', label: '发现' },
  info:     { fill: '#164e63', stroke: '#0891b2', label: '侦察' },
  excluded: { fill: '#374151', stroke: '#6b7280', label: '排除' },
};

// ── Intent 按 phase 着色（与后端 _VALID_PHASES 对齐）──
const PHASE_VIS: Record<string, { fill: string; stroke: string; icon: string; label: string }> = {
  recon:    { fill: '#155e75', stroke: '#06b6d4', icon: '🔍', label: '侦察' },
  vuln:     { fill: '#92400e', stroke: '#f59e0b', icon: '⚠', label: '漏洞' },
  exploit:  { fill: '#7f1d1d', stroke: '#ef4444', icon: '⚔', label: '利用' },
  post:     { fill: '#581c87', stroke: '#a855f7', icon: '⬆', label: '后利用' },
  general:  { fill: '#164e63', stroke: '#0891b2', icon: '◇', label: '通用' },
};

const EDGE_COLORS: Record<string, string> = {
  precondition: '#5eead4',
  derived: '#60a5fa',
};

// 截断标签
function truncateLabel(text: string, max = 20): string {
  const t = (text || '').replace(/\s+/g, ' ').trim();
  if (!t) return '';
  const chars = [...t];
  let w = 0;
  let out = '';
  for (const ch of chars) {
    w += ch.charCodeAt(0) > 255 ? 1.6 : 1;
    if (w > max) { out += '…'; break; }
    out += ch;
  }
  return out;
}

// 构建 Cytoscape 元素
function buildElements(snap: BlackboardSnapshot): ElementDefinition[] {
  const visibleNodes = snap.nodes;
  const elements: ElementDefinition[] = [];

  // ── BFS 计算每个节点到 origin 的深度（树状层级）──
  // origin depth=0；其余节点按派生边算 depth = max(前驱 depth) + 1
  // goal 强制为 max(fact depth) + 1，确保始终在最底层
  // 孤立 fact（无入边且 BFS 未到达）按 created_at 时间分层，避免同级
  const depthMap = new Map<string, number>();
  const inEdges = new Map<string, string[]>(); // 节点 → 入边 source 列表
  const outEdges = new Map<string, string[]>(); // 节点 → 出边 target 列表
  for (const n of visibleNodes) {
    inEdges.set(n.id, []);
    outEdges.set(n.id, []);
  }
  for (const e of snap.edges) {
    if (!e.source || !e.target || e.source === e.target) continue;
    inEdges.get(e.target)?.push(e.source);
    outEdges.get(e.source)?.push(e.target);
  }
  // 1. origin 设 depth 0，作为 BFS 起点
  const roots: string[] = [];
  for (const n of visibleNodes) {
    if (n.kind === 'origin') {
      depthMap.set(n.id, 0);
      roots.push(n.id);
    }
  }
  // 无 origin 时，无入边且非 goal 的节点作根
  if (roots.length === 0) {
    for (const n of visibleNodes) {
      if (n.kind !== 'goal' && (inEdges.get(n.id) || []).length === 0) {
        depthMap.set(n.id, 0);
        roots.push(n.id);
      }
    }
  }
  // 2. BFS 拓扑：每个节点 depth = max(前驱 depth) + 1
  const queue = [...roots];
  while (queue.length) {
    const cur = queue.shift()!;
    const d = depthMap.get(cur) ?? 0;
    for (const next of (outEdges.get(cur) || [])) {
      if (next === cur) continue;
      const nd = depthMap.get(next);
      if (nd === undefined || nd < d + 1) {
        depthMap.set(next, d + 1);
        queue.push(next);
      }
    }
  }
  // 3. 孤立 fact（无入边且非 origin 且 BFS 未到达）按 created_at 分层
  //    避免多个无入边 fact 全部 fallback 到 depth=1 同级
  const orphans = visibleNodes
    .filter(n =>
      n.kind !== 'origin' && n.kind !== 'goal' &&
      !depthMap.has(n.id) &&
      (inEdges.get(n.id) || []).length === 0
    )
    .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
  let orphanDepth = 1;
  for (const o of orphans) {
    depthMap.set(o.id, orphanDepth);
    orphanDepth++;
  }
  // 4. 仍有节点无 depth（有入边但 BFS 未到达，如有环）→ 给个兜底 depth
  let maxFactDepth = 0;
  for (const n of visibleNodes) {
    if (n.kind === 'fact' && depthMap.has(n.id)) {
      const d = depthMap.get(n.id)!;
      if (d > maxFactDepth) maxFactDepth = d;
    }
  }
  for (const n of visibleNodes) {
    if (!depthMap.has(n.id) && n.kind !== 'goal') {
      depthMap.set(n.id, maxFactDepth + 1);
    }
  }
  // 5. goal depth = max(fact depth) + 1（强制在最底层，确保 goal 在所有结论之下）
  for (const n of visibleNodes) {
    if (n.kind === 'goal') {
      depthMap.set(n.id, maxFactDepth + 1);
    }
  }

  // depth → 颜色渐变（深 → 浅，Cairn 蓝紫渐变风格）
  const DEPTH_FILLS = [
    '#4c1d95', // L0 深紫（origin）
    '#1e3a8a', // L1 深蓝
    '#1e40af', // L2 中深蓝
    '#1d4ed8', // L3 中蓝
    '#2563eb', // L4 浅蓝
    '#3b82f6', // L5 更浅
    '#60a5fa', // L6+
  ];
  function depthFill(d: number): string {
    if (d < 0) return KIND_VIS.fact.fill;
    return DEPTH_FILLS[Math.min(d, DEPTH_FILLS.length - 1)];
  }
  function depthTextColor(d: number): string {
    // 深底浅字，浅底深字
    return d <= 4 ? '#e5e7eb' : '#0f172a';
  }

  // 节点
  for (const n of visibleNodes) {
    const vis = KIND_VIS[n.kind] || KIND_VIS.fact;
    const role = n.properties?.role as string | undefined;
    const roleVis = role ? FACT_ROLE_VIS[role] : undefined;
    const polarity = n.properties?.polarity as string | undefined;
    let stroke = roleVis?.stroke || vis.stroke;
    if (role === 'evidence' && polarity === 'refutes') stroke = '#f87171';
    else if (role === 'evidence' && polarity === 'supports') stroke = '#4ade80';
    const icon = roleVis?.icon || vis.icon;
    const dimmed = n.status === 'superseded' || n.status === 'refuted' || n.status === 'failed';

    // depth：从 BFS depthMap 读取（origin=0, goal=maxFactDepth+1, 其余按 BFS）
    const depth = depthMap.get(n.id) ?? 0;

    // ── 背景着色策略 ──
    // origin/goal：保留 KIND_VIS 原色（首尾节点视觉权重）
    // fact：按 severity 着色（高分红/中分蓝/低分青/排除灰），覆盖 depth 渐变
    // intent：按 phase 着色（recon 青/vuln 橙/exploit 红/post 紫）
    // 其他：按 depth 渐变（保留原 Cairn 蓝紫风格）
    let bg: string;
    let textColor: string;
    let phaseTag = '';
    let scoreTag = '';

    if (n.kind === 'origin' || n.kind === 'goal') {
      bg = vis.fill;
      textColor = vis.color;
    } else if (n.kind === 'fact') {
      const severity = (n.properties?.severity as string) || '';
      const score = n.properties?.score as number | undefined;
      const sevVis = severity ? SEVERITY_VIS[severity] : undefined;
      bg = sevVis?.fill || depthFill(depth);
      textColor = sevVis ? '#e5e7eb' : depthTextColor(depth);
      if (score !== undefined) scoreTag = `[${score}] `;
    } else if (n.kind === 'intent') {
      const phase = (n.properties?.phase as string) || '';
      const phVis = phase ? PHASE_VIS[phase] : undefined;
      bg = phVis?.fill || depthFill(depth);
      textColor = phVis ? '#e5e7eb' : depthTextColor(depth);
      if (phase && PHASE_VIS[phase]) phaseTag = `${PHASE_VIS[phase].icon} `;
    } else {
      bg = depthFill(depth);
      textColor = depthTextColor(depth);
    }

    const labelPrefix = phaseTag || (icon ? `${icon} ` : '');
    const depthTag = depth >= 0 ? `L${depth} · ` : '';

    elements.push({
      group: 'nodes',
      data: {
        id: n.id,
        label: labelPrefix + scoreTag + depthTag + truncateLabel(n.label, 22),
        fullLabel: n.label,
        kind: n.kind,
        role: role || '',
        status: n.status || '',
        color: textColor,
        background: bg,
        borderColor: stroke,
        dimmed: dimmed ? 0.35 : 1,
        active: n.status === 'active',
        depth,
      },
    });
  }

  // 边
  for (const e of snap.edges) {
    const src = e.source;
    const tgt = e.target;
    if (!src || !tgt || src === tgt) continue;
    if (!visibleNodes.find(n => n.id === src) || !visibleNodes.find(n => n.id === tgt)) continue;

    const color = EDGE_COLORS[e.relation] || '#64748b';

    elements.push({
      group: 'edges',
      data: {
        id: e.id,
        source: src,
        target: tgt,
        relation: e.relation,
        color,
        lineStyle: 'solid',
        opacity: 0.7,
      },
    });
  }

  return elements;
}

// ELK 布局配置：垂直分层树（Cairn 风格，从上到下生长）
function elkLayout(): any {
  return {
    name: 'elk',
    elk: {
      algorithm: 'layered',
      'elk.direction': 'DOWN',
      // 层间距加大：让树状层级感清晰可见
      'elk.layered.spacing.nodeNodeBetweenLayers': 110,
      'elk.spacing.nodeNode': 55,
      'elk.layered.spacing.edgeNodeBetweenLayers': 40,
      // 网络单纯形法放置节点，减少边交叉
      'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
      'elk.layered.nodePlacement.freeNodeModel.strategy': 'NETWORK_SIMPLEX',
      // 交叉最小化：层扫 + 中点偏移
      'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
      'elk.layered.crossingMinimization.semiInteractive': 'true',
      // 同层节点按 BFS 深度排序，让派生链（A→B→C）尽量在同侧
      'elk.layered.layering.layerConstraint': 'NONE',
      // 节点间不要重叠，离散分层
      'elk.layered.nodePlacement.freeNodeModel.overhang': '0',
      'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
      'elk.padding': '[top=30,left=30,bottom=30,right=30]',
      // 边路由：正交（直角折线）使层间更清晰
      'elk.layered.edgeRouting.shape': 'ORTHOGONAL',
      'elk.layered.edgeRouting.style': 'ORTHOGONAL',
      'elk.layered.edgeRouting.selfSpacing': '6',
      'elk.layered.edgeSpacing': '40',
    },
    fit: false,
    padding: 30,
    animate: false,
  };
}

// Cytoscape 样式：垂直分层树（Cairn 风格）
function graphStyles(): StylesheetJson {
  return [
    {
      selector: 'node',
      style: {
        shape: 'round-rectangle',
        width: 220,
        height: 72,
        'background-color': 'data(background)',
        'border-color': 'data(borderColor)',
        'border-width': 2,
        label: 'data(label)',
        color: 'data(color)',
        'font-size': 13,
        'font-weight': 600,
        'text-wrap': 'wrap',
        'text-max-width': '200px',
        'text-valign': 'center',
        'text-halign': 'center',
        opacity: 'data(dimmed)' as any,
        'overlay-opacity': 0,
      },
    },
    // origin/goal：首尾节点尺寸加大，视觉权重更高
    {
      selector: 'node[kind = "origin"], node[kind = "goal"]',
      style: {
        width: 240,
        height: 84,
        'border-width': 3,
        'font-size': 14,
        'font-weight': 700,
      },
    },
    // 同层节点（depth 相同）：浅色边框做"层带"视觉提示
    {
      selector: 'node[depth <= 1]',
      style: { 'border-width': 3 },
    },
    {
      selector: 'node[?active]',
      style: {
        'border-width': 3,
      },
    },
    {
      selector: 'node.is-selected',
      style: {
        'border-width': 3,
        'border-color': '#fbbf24',
        'overlay-color': '#fbbf24',
        'overlay-opacity': 0.25,
        'overlay-padding': 8,
      },
    },
    {
      selector: '.is-dimmed-node',
      style: { opacity: 0.3 },
    },
    // 边：bezier 曲线（避免 taxi 乱穿）+ 加粗箭头 + 圆角端点
    {
      selector: 'edge',
      style: {
        width: 2.0,
        'line-color': 'data(color)',
        'target-arrow-color': 'data(color)',
        'source-arrow-color': 'data(color)',
        'line-style': 'data(lineStyle)' as any,
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        'control-point-distances': [-40, 40],
        'control-point-weights': [0.25, 0.75],
        'arrow-scale': 1.15,
        'line-cap': 'round',
        'source-endpoint': 'outside-to-node',
        'target-endpoint': 'outside-to-node',
        opacity: 'data(opacity)' as any,
        'overlay-opacity': 0,
      },
    },
    // precondition 边虚线：区分"前置条件"和"派生"两种语义
    {
      selector: 'edge[relation = "precondition"]',
      style: { 'line-style': 'dashed' as any },
    },
    {
      selector: 'edge.is-active-edge',
      style: {
        width: 3.0,
        label: 'data(relation)',
        'font-size': 9,
        'font-weight': 600,
        color: '#94a3b8',
        'text-background-color': '#0f172a',
        'text-background-opacity': 0.9,
        'text-background-padding': '2px',
        'text-rotation': 'autorotate',
      },
    },
  ];
}

export function AttackMap({ sessionId, open, onClose }: Props): JSX.Element | null {
  const [snapshot, setSnapshot] = useState<BlackboardSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showHintForm, setShowHintForm] = useState(false);
  const [hintLabel, setHintLabel] = useState('');
  const [hintDetail, setHintDetail] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const cyRef = useRef<Core | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);
  const onSelectRef = useRef(setSelectedId);

  const fetchBoard = useCallback(async () => {
    if (!sessionId) return;
    try {
      const bb = await getBlackboard(sessionId);
      setSnapshot(bb);
      setError('');
    } catch (e: any) {
      // 401 时不覆盖已有 snapshot，保留上次数据
      if (!String(e.message || '').includes('401')) {
        setError(e.message || '无法获取黑板');
        setSnapshot(null);
      }
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (open && sessionId) {
      setLoading(true);
      fetchBoard();
    }
  }, [open, sessionId, fetchBoard]);

  useEffect(() => {
    if (!open || !autoRefresh || !sessionId) return;
    const t = setInterval(fetchBoard, 3000);
    return () => clearInterval(t);
  }, [open, autoRefresh, sessionId, fetchBoard]);

  async function handleAddHint() {
    if (!sessionId || !hintLabel.trim()) return;
    try {
      await addBlackboardHint(sessionId, { label: hintLabel.trim(), detail: hintDetail.trim() });
      setHintLabel('');
      setHintDetail('');
      setShowHintForm(false);
      await fetchBoard();
    } catch (e: any) {
      setError(e.message || '注入 Hint 失败');
    }
  }

  const handoffs = useMemo(
    () => (snapshot?.nodes || [])
      .filter(n => n.kind === 'fact' && n.properties?.role === 'handoff')
      .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || '')),
    [snapshot],
  );
  const selected = selectedId && snapshot
    ? snapshot.nodes.find(n => n.id === selectedId) || null
    : null;
  const stats = snapshot?.stats;

  // 用 ref 跟踪上次的版本号
  const lastVersionRef = useRef<number>(-1);

  // 单一 effect：创建 + 数据更新（合并，避免 container 未挂载的时序问题）
  useEffect(() => {
    if (!open || !snapshot) return;

    // 延迟到下一帧，确保 containerRef 已挂载
    const raf = requestAnimationFrame(() => {
      if (!containerRef.current) return;
      // 容器尺寸为 0 时跳过（flex 布局尚未计算完成），下个轮询周期会重试
      const rect = containerRef.current.getBoundingClientRect();
      if (rect.width < 2 || rect.height < 2) return;

      const elements = buildElements(snapshot);
      if (!elements.length) return;

      // 保存当前视图（如果已有 cy 实例）
      const oldCy = cyRef.current;
      let curZoom = 1;
      let curPan: { x: number; y: number } | null = null;
      let isFirst = false;

      if (oldCy && !oldCy.destroyed()) {
        // 版本号没变就不做任何操作
        if (snapshot.version === lastVersionRef.current) return;
        curZoom = oldCy.zoom();
        curPan = { x: oldCy.pan().x, y: oldCy.pan().y };
        // 增量更新：移除旧元素、添加新元素
        oldCy.elements().remove();
        oldCy.add(elements);
        oldCy.resize();
      } else {
        // 首次创建
        isFirst = true;
        const cy = cytoscape({
          container: containerRef.current,
          elements,
          style: graphStyles(),
          minZoom: 0.2,
          maxZoom: 6.0,
          wheelSensitivity: 0.2,
          boxSelectionEnabled: false,
          selectionType: 'single',
        });
        cyRef.current = cy;

        cy.on('tap', 'node', (evt) => onSelectRef.current(evt.target.id()));
        cy.on('tap', (evt) => {
          if (evt.target === cy) onSelectRef.current(null);
        });
      }

      lastVersionRef.current = snapshot.version;
      const cy = cyRef.current!;

      // 布局超时兜底：ELK 偶尔会挂起不返回，5 秒后强制回退 cose
      let layoutDone = false;
      const layoutTimeout = setTimeout(() => {
        if (layoutDone) return;
        console.warn('ELK layout timeout, falling back to cose');
        try {
          cy.layout({
            name: 'cose',
            animate: false,
            nodeRepulsion: 8000,
            idealEdgeLength: 120,
            nodeOverlap: 20,
            padding: 30,
            fit: false,
          }).run();
          cy.resize();
          if (isFirst) { cy.fit(undefined, 60); cy.center(); }
        } catch (_) { /* ignore */ }
      }, 5000);

      try {
        const layout = cy.layout(elkLayout());
        layout.on('layoutstop', () => {
          layoutDone = true;
          clearTimeout(layoutTimeout);
          cy.resize();
          if (isFirst) {
            cy.fit(undefined, 60);
            if (cy.zoom() < 0.6) cy.zoom(0.8);
            cy.center();
          } else if (curPan) {
            cy.zoom(curZoom);
            cy.pan(curPan);
          }
        });
        layout.on('layouterror', () => {
          layoutDone = true;
          clearTimeout(layoutTimeout);
          const fb = cy.layout({
            name: 'cose',
            animate: false,
            nodeRepulsion: 8000,
            idealEdgeLength: 120,
            nodeOverlap: 20,
            padding: 30,
            fit: false,
          });
          fb.run();
        });
        layout.run();
      } catch (e) {
        layoutDone = true;
        clearTimeout(layoutTimeout);
        console.warn('ELK exception, fallback cose:', e);
        cy.layout({
          name: 'cose',
          animate: false,
          nodeRepulsion: 8000,
          idealEdgeLength: 120,
          nodeOverlap: 20,
          padding: 30,
          fit: false,
        }).run();
      }
    });

    return () => cancelAnimationFrame(raf);
  }, [open, snapshot]);

  // ResizeObserver：容器尺寸变化时同步 cy（解决 flex 布局延迟导致黑屏）
  useEffect(() => {
    if (!open) return;
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const cy = cyRef.current;
      if (cy && !cy.destroyed()) {
        cy.resize();
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [open]);

  // 关闭时清理
  useEffect(() => {
    if (open) return;
    if (cyRef.current) {
      cyRef.current.destroy();
      cyRef.current = undefined;
      lastVersionRef.current = -1;
    }
  }, [open]);

  // 选中高亮
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass('is-selected is-active-edge');
    if (!selectedId) return;
    const node = cy.getElementById(selectedId);
    if (!node.length) return;
    node.addClass('is-selected');
    node.connectedEdges().addClass('is-active-edge');
  }, [selectedId, snapshot]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-30 flex">
      <div className="ml-auto h-full w-[min(960px,95vw)] bg-gradient-to-b from-gray-900 to-gray-950 border-l border-gray-700/50 flex flex-col shadow-2xl">
        {/* 顶部 */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700/50 bg-gray-800/30">
          <div>
            <h3 className="text-sm font-bold text-gray-100">证据攻击地图</h3>
            <p className="text-[10px] text-gray-500 mt-0.5">
              垂直分层树 · origin(顶)→goal(底) · 每层向下派生 ·{' '}
              {snapshot ? `v${snapshot.version}` : '加载中'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                const cy = cyRef.current;
                if (!cy) return;
                cy.zoom(cy.zoom() * 1.25);
                cy.center();
              }}
              className="text-[10px] px-2 py-1 rounded border border-gray-700 bg-gray-800 text-gray-300 hover:bg-gray-700"
              title="放大"
            >
              ⊕
            </button>
            <button
              onClick={() => {
                const cy = cyRef.current;
                if (!cy) return;
                cy.zoom(cy.zoom() / 1.25);
                cy.center();
              }}
              className="text-[10px] px-2 py-1 rounded border border-gray-700 bg-gray-800 text-gray-300 hover:bg-gray-700"
              title="缩小"
            >
              ⊖
            </button>
            <button
              onClick={() => {
                const cy = cyRef.current;
                if (!cy) return;
                cy.fit(undefined, 60);
                if (cy.zoom() < 0.5) cy.zoom(0.8);
                cy.center();
              }}
              className="text-[10px] px-2 py-1 rounded border border-gray-700 bg-gray-800 text-gray-300 hover:bg-gray-700"
              title="居中"
            >
              ◎
            </button>
            <button
              onClick={() => setAutoRefresh(a => !a)}
              className={`text-[10px] px-2 py-1 rounded border transition-all ${
                autoRefresh
                  ? 'bg-green-600/20 border-green-600/40 text-green-300'
                  : 'bg-gray-800 border-gray-700 text-gray-400'
              }`}
            >
              {autoRefresh ? '● 实时' : '○ 暂停'}
            </button>
            <button
              onClick={() => setShowHintForm(s => !s)}
              className="text-[10px] px-2 py-1 rounded border border-amber-600/40 bg-amber-600/10 text-amber-300 hover:bg-amber-600/20"
            >
              + Hint
            </button>
            <button
              onClick={() => { cyRef.current?.fit(undefined, 40); }}
              className="text-[10px] px-2 py-1 rounded border border-gray-600/40 bg-gray-800 text-gray-300 hover:bg-gray-700"
              title="居中"
            >
              ⊕
            </button>
            <button onClick={onClose} className="p-1 rounded hover:bg-gray-800 text-gray-500">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>
        </div>

        {/* 统计条 */}
        {stats && (
          <div className="flex flex-wrap gap-1.5 px-4 py-2 border-b border-gray-700/40 text-[10px] font-medium">
            <span className="px-2 py-0.5 rounded-full bg-blue-600/15 text-blue-200 border border-blue-600/20">✓{stats.facts}</span>
            <span className="px-2 py-0.5 rounded-full bg-cyan-600/15 text-cyan-300 border border-cyan-600/20">◇{stats.intents_pending}</span>
            <span className="px-2 py-0.5 rounded-full bg-green-600/15 text-green-300 border border-green-600/20">✅{stats.intents_done}</span>
            <span className="px-2 py-0.5 rounded-full bg-red-600/15 text-red-300 border border-red-600/20">❌{stats.intents_failed ?? 0}</span>
            <span className="px-2 py-0.5 rounded-full bg-amber-600/15 text-amber-300 border border-amber-600/20">!{stats.hints}</span>
          </div>
        )}

        {showHintForm && (
          <div className="px-4 py-3 border-b border-gray-800 bg-amber-600/5 space-y-2">
            <input
              type="text"
              value={hintLabel}
              onChange={e => setHintLabel(e.target.value)}
              placeholder="提示标题（如：重点关注 AJP 协议）"
              className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-amber-500/40"
            />
            <input
              type="text"
              value={hintDetail}
              onChange={e => setHintDetail(e.target.value)}
              placeholder="详细说明（可选）"
              className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-amber-500/40"
            />
            <div className="flex gap-2">
              <button
                onClick={handleAddHint}
                disabled={!hintLabel.trim()}
                className="flex-1 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:bg-gray-700 disabled:text-gray-500 rounded text-xs font-medium"
              >
                注入
              </button>
              <button
                onClick={() => setShowHintForm(false)}
                className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-400 rounded text-xs"
              >
                取消
              </button>
            </div>
          </div>
        )}

        {/* 图例：节点类型 + phase + severity + 深度色带 */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-1.5 border-b border-gray-700/40 text-[9px] text-gray-500">
          {Object.entries(KIND_VIS).map(([k, v]) => (
            <span key={k} className="flex items-center gap-1">
              <span style={{ color: v.stroke, fontWeight: 700 }}>{v.icon}</span>
              {v.label}
            </span>
          ))}
          <span className="text-gray-700 mx-1">|</span>
          <span className="flex items-center gap-1 font-medium text-gray-400">
            阶段
            {Object.entries(PHASE_VIS).map(([k, v]) => (
              <span key={k} className="flex items-center gap-0.5" title={k}>
                <span style={{ color: v.stroke, fontWeight: 700 }}>{v.icon}</span>
                {v.label}
              </span>
            ))}
          </span>
          <span className="text-gray-700 mx-1">|</span>
          <span className="flex items-center gap-1 font-medium text-gray-400">
            威胁
            {Object.entries(SEVERITY_VIS).map(([k, v]) => (
              <span key={k} className="flex items-center gap-0.5" title={k}>
                <span style={{
                  display: 'inline-block', width: 9, height: 9,
                  background: v.fill, border: `1px solid ${v.stroke}`, borderRadius: 2,
                }} />
                {v.label}
              </span>
            ))}
          </span>
          <span className="text-gray-700 mx-1">|</span>
          <span className="flex items-center gap-1">
            <span style={{ borderTop: '2px solid #60a5fa', width: 14, display: 'inline-block' }} /> 派生
          </span>
          <span className="flex items-center gap-1">
            <span style={{ borderTop: '2px dashed #5eead4', width: 14, display: 'inline-block' }} /> 前置
          </span>
        </div>

        {/* 图 */}
        <div className="flex-1 overflow-hidden bg-gray-950/60 relative">
          {loading ? (
            <div className="text-center text-gray-600 py-8 text-xs">加载攻击地图中...</div>
          ) : error ? (
            <div className="text-center py-8">
              <p className="text-xs text-gray-500 mb-2">该会话无黑板</p>
              <p className="text-[10px] text-gray-600">{error}</p>
            </div>
          ) : !snapshot ? (
            <div className="text-center text-gray-600 py-8 text-xs">黑板为空</div>
          ) : (
            <div ref={containerRef} className="w-full h-full" />
          )}
        </div>

        {/* 交接卡 */}
        {handoffs.length > 0 && (
          <div className="px-3 pb-2 max-h-[30%] overflow-y-auto border-t border-gray-700/40">
            <div className="text-[10px] text-amber-300/80 font-bold mb-1.5 mt-2 px-1">
              📋 轮末交接卡 · {handoffs.length} 张 · 跨轮工作记忆
            </div>
            <div className="space-y-1.5">
              {handoffs.map(h => <HandoffCard key={h.id} node={h}
                selected={selectedId === h.id}
                onClick={() => setSelectedId(h.id)} />)}
            </div>
          </div>
        )}

        {/* 节点详情底栏 */}
        {selected && (
          <NodeDetail node={selected} onClose={() => setSelectedId(null)} />
        )}
      </div>
    </div>
  );
}

function HandoffCard({ node, selected, onClick }: {
  node: BBNode; selected: boolean; onClick: () => void;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const card = (node.properties?.card || {}) as Record<string, any>;
  const rnd = node.properties?.round;
  const vp: string[] = card.verified_paths || [];
  const de: string[] = card.dead_ends || [];
  const ns: string[] = card.next_steps || [];
  const arts: { name?: string }[] = card.artifacts || [];
  const expanded = open || selected;
  return (
    <div
      onClick={onClick}
      className={`rounded-lg border px-3 py-2 text-[10px] cursor-pointer transition-all ${
        selected
          ? 'border-amber-400/70 bg-amber-600/15 shadow-lg'
          : 'border-amber-600/25 bg-amber-600/5 hover:bg-amber-600/10 hover:border-amber-600/40'
      }`}
    >
      <div className="flex items-center justify-between" onClick={e => { e.stopPropagation(); setOpen(o => !o); }}>
        <span className="font-medium text-amber-200">
          📋 第 {rnd ?? '?'} 轮 · {card.summary || node.label}
          {card.target_changed ? <span className="ml-1 text-red-300">🔴目标变更</span> : null}
        </span>
        <span className="text-gray-500">{expanded ? '▾' : '▸'}</span>
      </div>
      {expanded && (
        <div className="mt-1 space-y-1 text-gray-300">
          {card.current_target && (
            <div><span className="text-orange-300">目标：</span>{card.current_target}</div>
          )}
          {vp.length > 0 && (
            <div>
              <span className="text-green-300">已走通：</span>
              {vp.map((p, i) => <div key={i} className="pl-3 text-gray-400">· {p}</div>)}
            </div>
          )}
          {de.length > 0 && (
            <div>
              <span className="text-red-300/90">死路：</span>
              {de.map((p, i) => <div key={i} className="pl-3 text-gray-500 opacity-60 line-through">· {p}</div>)}
            </div>
          )}
          {ns.length > 0 && (
            <div>
              <span className="text-teal-300">下一步：</span>
              {ns.map((p, i) => <div key={i} className="pl-3 text-gray-400">→ {p}</div>)}
            </div>
          )}
          {arts.length > 0 && (
            <div className="space-y-1">
              {arts.map((a: any, i: number) => (
                <div key={i}>
                  <div className="text-indigo-300/90">🧩 工件 {a.name}：</div>
                  {a.content ? (
                    <pre className="mt-0.5 text-[9px] text-gray-400 whitespace-pre-wrap break-all font-mono bg-gray-950/60 border border-indigo-900/40 rounded p-1.5 max-h-32 overflow-y-auto">
                      {String(a.content).slice(0, 2000)}
                    </pre>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function NodeDetail({ node, onClose }: { node: BBNode; onClose: () => void }): JSX.Element {
  const vis = KIND_VIS[node.kind] || KIND_VIS.fact;
  const card = node.kind === 'fact' && node.properties?.role === 'handoff' ? (node.properties?.card || {}) as Record<string, any> : null;
  return (
    <div className="border-t border-gray-700/50 bg-gray-900/95 max-h-[42%] overflow-y-auto px-4 py-3 backdrop-blur-sm">
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold" style={{ color: vis.stroke }}>
            {vis.icon} {vis.label}
          </span>
          {node.status && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">
              {node.status}
            </span>
          )}
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-xs">✕</button>
      </div>
      <div className="text-xs text-gray-200 font-medium mb-1">{node.label}</div>
      {node.detail && (
        <pre className="text-[10px] text-gray-400 whitespace-pre-wrap break-all font-sans">
          {node.detail}
        </pre>
      )}
      {card && (
        <div className="mt-1.5 text-[10px] text-gray-400">
          {card.current_target && <div>目标：{card.current_target}</div>}
          {(card.artifacts || []).map((a: any, i: number) => (
            <div key={i} className="mt-1">
              <div className="text-indigo-300">工件 {a.name}：</div>
              <pre className="text-[9px] text-gray-500 whitespace-pre-wrap break-all font-mono bg-gray-800/50 rounded p-1">
                {String(a.content || '').slice(0, 800)}
              </pre>
            </div>
          ))}
        </div>
      )}
      {node.discovered_by && node.discovered_by !== 'user' && (
        <div className="text-[9px] text-gray-600 mt-1.5">by {node.discovered_by}</div>
      )}
    </div>
  );
}
