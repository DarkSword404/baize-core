import { useState, useEffect, useCallback } from 'react';
import { getBlackboard, addBlackboardHint } from '../api/client';
import type { BlackboardSnapshot, BlackboardNode as BBNode } from '../types';
import type { JSX } from 'react';

interface Props {
  sessionId: string | null;
  open: boolean;
  onClose: () => void;
}

// 节点类型 → 颜色/图标
const KIND_STYLE: Record<string, { color: string; bg: string; icon: string; label: string }> = {
  origin: { color: 'text-gray-300', bg: 'bg-gray-700/60 border-gray-500/40', icon: '⊙', label: '起点' },
  fact:   { color: 'text-blue-300',  bg: 'bg-blue-600/10 border-blue-500/40', icon: '✓', label: '事实' },
  intent:{ color: 'text-teal-300',  bg: 'bg-teal-600/10 border-teal-500/40 border-dashed', icon: '?', label: '意图' },
  goal:   { color: 'text-purple-300', bg: 'bg-purple-600/20 border-purple-500/50', icon: '◎', label: '目标' },
  hint:   { color: 'text-amber-300', bg: 'bg-amber-600/10 border-amber-500/40', icon: '!', label: '提示' },
};

// Intent 状态 → 徽章
const INTENT_STATUS_BADGE: Record<string, { text: string; cls: string }> = {
  pending:   { text: '待认领', cls: 'bg-gray-700/60 text-gray-400' },
  claimed:   { text: '已认领', cls: 'bg-blue-600/20 text-blue-300' },
  exploring: { text: '探索中', cls: 'bg-teal-600/20 text-teal-300' },
  done:      { text: '已完成', cls: 'bg-green-600/20 text-green-300' },
  failed:    { text: '失败',   cls: 'bg-red-600/20 text-red-300' },
};

export function AttackMap({ sessionId, open, onClose }: Props): JSX.Element | null {
  const [snapshot, setSnapshot] = useState<BlackboardSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showHintForm, setShowHintForm] = useState(false);
  const [hintLabel, setHintLabel] = useState('');
  const [hintDetail, setHintDetail] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchBoard = useCallback(async () => {
    if (!sessionId) return;
    try {
      const bb = await getBlackboard(sessionId);
      setSnapshot(bb);
      setError('');
    } catch (e: any) {
      // 协作模式才有黑板，非协作会话返回 400
      setError(e.message || '无法获取黑板');
      setSnapshot(null);
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

  // 自动刷新（每 3 秒拉一次，捕捉 agent 实时写入的 Fact/Intent）
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

  if (!open) return null;

  const stats = snapshot?.stats;
  const nodes = snapshot?.nodes || [];
  const edges = snapshot?.edges || [];

  // 简单分层布局：origin 在左，goal 在右，中间按 kind 分层
  // 实际渲染用 flex 分层，避免复杂 SVG 计算
  const layers: Record<string, BBNode[]> = {
    origin:  nodes.filter(n => n.kind === 'origin'),
    fact:    nodes.filter(n => n.kind === 'fact'),
    intent:  nodes.filter(n => n.kind === 'intent'),
    hint:    nodes.filter(n => n.kind === 'hint'),
    goal:    nodes.filter(n => n.kind === 'goal'),
  };

  return (
    <div className="fixed inset-0 z-30 flex">
      {/* 攻击地图侧栏（右侧滑出，类似 BrowserPanel）*/}
      <div className="ml-auto w-[440px] h-full bg-gray-900 border-l border-gray-800 flex flex-col shadow-2xl">
        {/* 顶部 */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
          <div>
            <h3 className="text-sm font-semibold text-gray-200">攻击地图</h3>
            <p className="text-[10px] text-gray-500 mt-0.5">
              Fact-Intent 黑板 · {snapshot ? `v${snapshot.version}` : '加载中'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setAutoRefresh(a => !a)}
              className={`text-[10px] px-2 py-1 rounded border transition-all ${
                autoRefresh
                  ? 'bg-green-600/20 border-green-600/40 text-green-300'
                  : 'bg-gray-800 border-gray-700 text-gray-400'
              }`}
              title="自动刷新开关"
            >
              {autoRefresh ? '● 实时' : '○ 暂停'}
            </button>
            <button
              onClick={() => setShowHintForm(s => !s)}
              className="text-[10px] px-2 py-1 rounded border border-amber-600/40 bg-amber-600/10 text-amber-300 hover:bg-amber-600/20 transition-all"
            >
              + Hint
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
          <div className="flex gap-1.5 px-4 py-2 border-b border-gray-800/50 text-[10px]">
            <span className="px-1.5 py-0.5 rounded bg-blue-600/15 text-blue-300">{stats.facts} 事实</span>
            <span className="px-1.5 py-0.5 rounded bg-teal-600/15 text-teal-300">{stats.intents_pending} 待探</span>
            <span className="px-1.5 py-0.5 rounded bg-green-600/15 text-green-300">{stats.intents_done} 完成</span>
            <span className="px-1.5 py-0.5 rounded bg-amber-600/15 text-amber-300">{stats.hints} 提示</span>
          </div>
        )}

        {/* Hint 注入表单 */}
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
                className="flex-1 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:bg-gray-700 disabled:text-gray-500 rounded text-xs font-medium transition-all"
              >
                注入
              </button>
              <button
                onClick={() => setShowHintForm(false)}
                className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-400 rounded text-xs transition-all"
              >
                取消
              </button>
            </div>
          </div>
        )}

        {/* 黑板内容 */}
        <div className="flex-1 overflow-y-auto p-3">
          {loading ? (
            <div className="text-center text-gray-600 py-8 text-xs">加载黑板中...</div>
          ) : error ? (
            <div className="text-center py-8">
              <p className="text-xs text-gray-500 mb-2">该会话无黑板</p>
              <p className="text-[10px] text-gray-600">{error}</p>
              <p className="text-[10px] text-gray-600 mt-2">协作模式会话才有攻击图</p>
            </div>
          ) : nodes.length === 0 ? (
            <div className="text-center text-gray-600 py-8 text-xs">黑板为空</div>
          ) : (
            <div className="space-y-3">
              {/* 目标信息 */}
              {snapshot && (
                <div className="px-2 py-1.5 bg-purple-600/10 border border-purple-600/30 rounded text-[10px]">
                  <span className="text-purple-300">目标:</span>{' '}
                  <span className="text-gray-300">{snapshot.goal || '(未设)'}</span>
                  {' · '}
                  <span className="text-purple-300">范围:</span>{' '}
                  <span className="text-gray-300">{snapshot.scope || '(未设)'}</span>
                </div>
              )}

              {/* 分层渲染节点 */}
              {(Object.keys(layers) as Array<keyof typeof layers>).map(kind => {
                const layerNodes = layers[kind];
                if (layerNodes.length === 0) return null;
                const style = KIND_STYLE[kind];
                return (
                  <div key={kind}>
                    <div className="flex items-center gap-1.5 mb-1 px-1">
                      <span className={`text-[10px] ${style.color}`}>{style.icon}</span>
                      <span className="text-[10px] font-medium text-gray-500">{style.label}</span>
                      <span className="text-[10px] text-gray-700">({layerNodes.length})</span>
                    </div>
                    <div className="space-y-1">
                      {layerNodes.map(n => {
                        const badge = n.kind === 'intent' ? INTENT_STATUS_BADGE[n.status] : null;
                        return (
                          <div
                            key={n.id}
                            className={`px-2 py-1.5 rounded border ${style.bg} ${style.color}`}
                          >
                            <div className="flex items-start justify-between gap-2">
                              <div className="flex-1 min-w-0">
                                <div className="text-xs font-medium truncate">{n.label}</div>
                                {n.detail && (
                                  <div className="text-[10px] text-gray-500 mt-0.5 line-clamp-2">{n.detail}</div>
                                )}
                                {n.discovered_by && n.discovered_by !== 'user' && (
                                  <div className="text-[9px] text-gray-600 mt-0.5">by {n.discovered_by}</div>
                                )}
                              </div>
                              {badge && (
                                <span className={`text-[9px] px-1.5 py-0.5 rounded ${badge.cls} flex-shrink-0`}>
                                  {badge.text}
                                </span>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}

              {/* 边计数（信息性）*/}
              {edges.length > 0 && (
                <div className="text-[9px] text-gray-700 text-center pt-2">
                  {edges.length} 条关系边
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
