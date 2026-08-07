import { useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import { listSessions, deleteSession, getSession } from '../api/client';
import type { SessionDetail } from '../types';
import type { JSX } from 'react';

export function Sessions(): JSX.Element {
  const { sessions, setSessions, setActiveSessionId, setCurrentView, removeSession, addToast } = useApp();
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedSession, setSelectedSession] = useState<SessionDetail | null>(null);
  const [_loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => { loadSessions(); }, []);

  async function loadSessions() {
    setLoading(true);
    try {
      const r = await listSessions();
      setSessions(r.sessions);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载失败', message: err.message });
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteSession(id);
      removeSession(id);
      if (selectedSession?.id === id) setSelectedSession(null);
      addToast({ type: 'info', title: '会话已删除' });
    } catch (err: any) {
      addToast({ type: 'error', title: '删除失败', message: err.message });
    }
  }

  async function handleViewDetail(id: string) {
    setLoadingDetail(true);
    try {
      const detail = await getSession(id);
      setSelectedSession(detail);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载详情失败', message: err.message });
    } finally {
      setLoadingDetail(false);
    }
  }

  async function handleContinue(id: string) {
    setActiveSessionId(id);
    setCurrentView('chat');
  }

  const filtered = sessions.filter(s =>
    !search || s.agent.toLowerCase().includes(search.toLowerCase()) ||
    s.model.toLowerCase().includes(search.toLowerCase()) || s.id.includes(search)
  );

  function formatDate(ts: string): string {
    try {
      return new Date(ts).toLocaleString('zh-CN', {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      });
    } catch { return ts; }
  }

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">会话管理</h1>
          <p className="text-sm text-gray-500 mt-1">共 {sessions.length} 个会话</p>
        </div>
        <button onClick={loadSessions} className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl transition-colors">
          刷新
        </button>
      </div>

      {/* Search + Bulk */}
      <div className="flex gap-3 mb-6">
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="搜索会话名称、模型或 ID..."
          className="flex-1 max-w-md px-4 py-2.5 bg-gray-900 border border-gray-800 rounded-xl text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none"
        />
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20 text-gray-600">
          {search ? '未找到匹配的会话' : '暂无会话记录'}
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-gray-800">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-900/50">
                <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">智能体</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">模型</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">消息数</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">创建时间</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {filtered.map(s => (
                <tr key={s.id} className="hover:bg-gray-900/30 transition-colors group">
                  <td className="px-5 py-3.5">
                    <span className="text-sm font-medium">{s.agent}</span>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className="text-sm text-gray-400">{s.model}</span>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className="text-sm text-gray-400">{s.history_length}</span>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className="text-xs text-gray-600">{formatDate(s.created_at)}</span>
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleContinue(s.id)}
                        className="px-3 py-1.5 text-xs bg-blue-600/10 hover:bg-blue-600/20 text-blue-400 rounded-lg border border-blue-600/20 transition-colors"
                      >
                        继续对话
                      </button>
                      <button
                        onClick={() => handleViewDetail(s.id)}
                        className="px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 text-gray-400 rounded-lg border border-gray-700 transition-colors"
                      >
                        详情
                      </button>
                      <button
                        onClick={() => handleDelete(s.id)}
                        className="px-3 py-1.5 text-xs bg-red-600/10 hover:bg-red-600/20 text-red-400 rounded-lg border border-red-600/20 transition-colors"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Detail Drawer */}
      {selectedSession && (
        <div className="fixed inset-y-0 right-0 z-30 w-96 bg-gray-900 border-l border-gray-800 shadow-2xl animate-slide-right overflow-y-auto">
          <div className="p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">会话详情</h2>
              <button onClick={() => setSelectedSession(null)} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>

            <div className="space-y-3 mb-6">
              <InfoRow label="会话 ID" value={selectedSession.id} />
              <InfoRow label="智能体" value={selectedSession.agent} />
              <InfoRow label="模型" value={selectedSession.model} />
              <InfoRow label="状态" value={selectedSession.stateful ? '有状态' : '无状态'} />
              <InfoRow label="消息数" value={String(selectedSession.history_length)} />
              <InfoRow label="创建时间" value={formatDate(selectedSession.created_at)} />
              <InfoRow label="更新时间" value={formatDate(selectedSession.updated_at)} />
              {selectedSession.metadata && Object.keys(selectedSession.metadata).length > 0 && (
                <InfoRow label="元数据" value={JSON.stringify(selectedSession.metadata, null, 2)} />
              )}
            </div>

            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
              对话历史 ({selectedSession.history.length} 条)
            </h3>
            <div className="space-y-2">
              {selectedSession.history.map((h: any, i: number) => (
                <div key={i} className="bg-gray-800/40 rounded-lg p-3 text-xs">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${
                      h.role === 'user' ? 'bg-blue-600/10 text-blue-400' :
                      h.role === 'assistant' ? 'bg-emerald-600/10 text-emerald-400' :
                      'bg-gray-600/10 text-gray-400'
                    }`}>{h.role}</span>
                  </div>
                  <div className="text-gray-400 line-clamp-3 font-mono text-[11px]">
                    {typeof h.content === 'string' ? h.content : JSON.stringify(h.content, null, 2)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex justify-between text-sm">
      <span className="text-gray-500">{label}</span>
      <span className="text-gray-300 text-right max-w-[60%] truncate font-mono text-xs">{value}</span>
    </div>
  );
}
