import { useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import { listAgents, listCustomAgents, deleteCustomAgent } from '../api/client';
import type { AgentMetadata } from '../types';
import { agentDescCN, toolDescCN } from '../i18n/translations';
import type { JSX } from 'react';

export function Agents(): JSX.Element {
  const { agents, setAgents, addToast } = useApp();
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedAgent, setSelectedAgent] = useState<AgentMetadata | null>(null);

  useEffect(() => {
    if (agents.length === 0) loadAgents();
  }, []);

  async function loadAgents() {
    setLoading(true);
    try {
      const [builtin, custom] = await Promise.all([
        listAgents(),
        listCustomAgents().catch(() => ({ agents: [] })),
      ]);
      // Merge custom agents into the list with is_custom flag
      const customMapped: AgentMetadata[] = custom.agents
        .filter(a => a.name)  // skip broken entries
        .map(a => ({
          id: a.id,
          name: a.name,
          description: a.description || null,
          type: 'agent' as const,
          pattern_type: null,
          tools: a.tools.map(t => ({ name: t, description: null })),
          is_custom: true,
        }));
      setAgents([...builtin.agents, ...customMapped]);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载失败', message: err.message });
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(`确定要删除自定义智能体「${name}」吗？此操作不可撤销。`)) return;
    try {
      await deleteCustomAgent(id);
      addToast({ type: 'success', title: '删除成功', message: `已删除智能体「${name}」` });
      setSelectedAgent(null);
      loadAgents();
    } catch (err: any) {
      addToast({ type: 'error', title: '删除失败', message: err.message });
    }
  }

  const filtered = agents.filter(a => {
    if (!search) return true;
    const s = search.toLowerCase();
    if (a.name.toLowerCase().includes(s)) return true;
    if (a.description && a.description.toLowerCase().includes(s)) return true;
    if (agentDescCN[a.name] && agentDescCN[a.name].toLowerCase().includes(s)) return true;
    return false;
  });

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">安全智能体</h1>
          <p className="text-sm text-gray-500 mt-1">共 {agents.length} 个可用智能体</p>
        </div>
        <button onClick={loadAgents} className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl transition-colors">
          刷新
        </button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="搜索智能体名称或描述..."
          className="w-full max-w-md px-4 py-2.5 bg-gray-900 border border-gray-800 rounded-xl text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none"
        />
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20 text-gray-600">
          {search ? '未找到匹配的智能体' : '暂无可用智能体'}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map(a => (
            <div
              key={a.name}
              className={`relative text-left p-5 rounded-2xl border transition-all hover:shadow-xl ${
                selectedAgent?.name === a.name
                  ? 'border-blue-500/40 bg-blue-600/5 shadow-lg shadow-blue-600/5'
                  : 'border-gray-800 bg-gray-900 hover:border-gray-700'
              }`}
            >
              <button
                onClick={() => setSelectedAgent(selectedAgent?.name === a.name ? null : a)}
                className="w-full text-left"
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="w-10 h-10 bg-gray-800 rounded-xl flex items-center justify-center text-lg">
                    {a.is_custom ? '🧪' : (a.type === 'pattern' ? '🔍' : '🤖')}
                  </div>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                    a.is_custom ? 'bg-amber-600/10 text-amber-400' :
                    a.type === 'pattern' ? 'bg-purple-600/10 text-purple-400' : 'bg-emerald-600/10 text-emerald-400'
                  }`}>
                    {a.is_custom ? '自定义' : (a.type === 'pattern' ? '模式' : '智能体')}
                  </span>
                </div>
                <h3 className="text-sm font-semibold mb-1">{a.name}</h3>
                <p className="text-xs text-gray-500 line-clamp-2 mb-3">{agentDescCN[a.name] || a.description || '无描述'}</p>
                {a.tools.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {a.tools.map(t => (
                      <span key={t.name} className="text-[10px] px-2 py-0.5 bg-gray-800 rounded-md text-gray-500">{t.name}</span>
                    ))}
                  </div>
                )}
                {a.pattern_type && (
                  <div className="mt-2 text-[10px] text-gray-600">模式类型: {a.pattern_type}</div>
                )}
              </button>
              {a.is_custom && (
                <button
                  onClick={(e) => { e.stopPropagation(); handleDelete(a.id!, a.name); }}
                  className="absolute top-3 right-3 p-1.5 rounded-lg bg-red-600/10 hover:bg-red-600/20 text-red-400 hover:text-red-300 transition-colors"
                  title="删除"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Detail Drawer */}
      {selectedAgent && (
        <div className="fixed inset-y-0 right-0 z-30 w-80 bg-gray-900 border-l border-gray-800 shadow-2xl animate-slide-right overflow-y-auto">
          <div className="p-5">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold">{selectedAgent.name}</h2>
                {selectedAgent.is_custom && (
                  <span className="text-[10px] px-2 py-0.5 rounded-full font-medium bg-amber-600/10 text-amber-400">自定义</span>
                )}
              </div>
              <button onClick={() => setSelectedAgent(null)} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">基本信息</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-500">类型</span>
                    <span className="font-medium">{selectedAgent.type === 'pattern' ? '模式' : '智能体'}</span>
                  </div>
                  {selectedAgent.pattern_type && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">模式类型</span>
                      <span className="font-medium">{selectedAgent.pattern_type}</span>
                    </div>
                  )}
                </div>
              </div>

              <div>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">描述</h3>
                <p className="text-sm text-gray-400">{agentDescCN[selectedAgent.name] || selectedAgent.description || '无描述'}</p>
              </div>

              <div>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
                  工具列表 ({selectedAgent.tools.length})
                </h3>
                {selectedAgent.tools.length === 0 ? (
                  <p className="text-sm text-gray-600">无可用工具</p>
                ) : (
                  <div className="space-y-1.5">
                    {selectedAgent.tools.map(t => (
                      <div key={t.name} className="px-3 py-2 bg-gray-800/60 rounded-lg">
                        <div className="text-sm font-medium font-mono">{t.name}</div>
                        {t.description && <div className="text-xs text-gray-500 mt-0.5">{toolDescCN[t.name] || t.description}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {selectedAgent.is_custom && (
              <div className="mt-6 pt-4 border-t border-gray-800">
                <button
                  onClick={() => handleDelete(selectedAgent.id!, selectedAgent.name)}
                  className="w-full px-4 py-2.5 bg-red-600/10 hover:bg-red-600/20 border border-red-600/20 rounded-xl text-sm text-red-400 hover:text-red-300 transition-colors flex items-center justify-center gap-2 cursor-pointer"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                  删除此智能体
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
