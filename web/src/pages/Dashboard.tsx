import { useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import { healthCheck, listAgents, getModelConfig, listSessions } from '../api/client';
import type { AgentMetadata, SessionSummary } from '../types';
import type { JSX } from 'react';

interface DashboardStats {
  agentCount: number;
  modelCount: string;
  sessionCount: number;
  activeSessions: number;
}

export function Dashboard(): JSX.Element {
  const { setCurrentView, addToast } = useApp();
  const [stats, setStats] = useState<DashboardStats>({ agentCount: 0, modelCount: '—', sessionCount: 0, activeSessions: 0 });
  const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
  const [topAgents, setTopAgents] = useState<AgentMetadata[]>([]);
  const [health, setHealth] = useState<{ status: string; version: string } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboard();
  }, []);

  async function loadDashboard() {
    setLoading(true);
    try {
      const [h, a, s] = await Promise.all([
        healthCheck(),
        listAgents(),
        listSessions(),
      ]);
      let modelName = '—';
      try {
        const mcfg = await getModelConfig();
        if (mcfg.configured) modelName = mcfg.model;
      } catch { /* ignore */ }
      setHealth(h);
      setStats({
        agentCount: a.agents.length,
        modelCount: modelName,
        sessionCount: s.sessions.length,
        activeSessions: s.sessions.length,
      });
      setRecentSessions(s.sessions.slice(0, 5));
      setTopAgents(a.agents.slice(0, 6));
    } catch (err: any) {
      addToast({ type: 'error', title: '无法加载控制台数据', message: err.message });
    } finally {
      setLoading(false);
    }
  }

  const statCards = [
    { label: '安全智能体', value: stats.agentCount, icon: 'M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z', color: 'blue', onClick: () => setCurrentView('agents') },
    { label: '当前模型', value: stats.modelCount, icon: 'M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z', color: 'amber', onClick: () => setCurrentView('settings') },
    { label: '渗透会话', value: stats.sessionCount, icon: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z', color: 'emerald', onClick: () => setCurrentView('sessions') },
    { label: '服务器状态', value: health?.status || '—', icon: 'M5 12h14M12 5l7 7-7 7', color: health?.status === 'ok' ? 'emerald' : 'red', sub: health?.version },
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-gray-500">加载控制台数据...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">白泽控制台</h1>
          <p className="text-sm text-gray-500 mt-1">AI 驱动的安全渗透测试平台</p>
        </div>
        <button
          onClick={loadDashboard}
          className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl transition-colors flex items-center gap-2"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          刷新
        </button>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {statCards.map((card) => (
          <button
            key={card.label}
            onClick={card.onClick}
            className="bg-gray-900 border border-gray-800 hover:border-gray-700 rounded-2xl p-5 text-left transition-all hover:shadow-xl hover:shadow-black/20 group"
          >
            <div className="flex items-start justify-between mb-3">
              <svg className={`w-6 h-6 text-${card.color}-400`} fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d={card.icon} />
              </svg>
            </div>
            <div className={`text-3xl font-bold tracking-tight text-${card.color}-400`}>
              {card.value}
            </div>
            <div className="text-sm text-gray-500 mt-1">
              {card.label}
              {card.sub && <span className="ml-2 text-gray-600">· v{card.sub}</span>}
            </div>
          </button>
        ))}
      </div>

      {/* Quick Actions */}
      <div className="mb-8">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">快捷操作</h2>
        <div className="flex gap-3 flex-wrap">
          <QuickAction label="新建渗透会话" onClick={() => setCurrentView('chat')} />
          <QuickAction label="浏览智能体库" onClick={() => setCurrentView('agents')} />
          <QuickAction label="编排平台" onClick={() => setCurrentView('orchestration')} />
          <QuickAction label="系统设置" onClick={() => setCurrentView('settings')} />
        </div>
      </div>

      {/* Two Column: Recent Sessions + Top Agents */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Sessions */}
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold">最近会话</h3>
            <button onClick={() => setCurrentView('sessions')} className="text-xs text-blue-400 hover:text-blue-300 transition-colors">
              查看全部 →
            </button>
          </div>
          {recentSessions.length === 0 ? (
            <p className="text-sm text-gray-600 py-6 text-center">暂无会话，点击上方快捷操作创建</p>
          ) : (
            <div className="space-y-2">
              {recentSessions.map(s => (
                <div key={s.id} className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-gray-800/50 hover:bg-gray-800 transition-colors">
                  <div className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{s.agent}</div>
                    <div className="text-[11px] text-gray-600">{s.model}</div>
                  </div>
                  <div className="text-[11px] text-gray-600 flex-shrink-0">
                    {s.history_length} 条消息
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Top Agents */}
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold">安全智能体</h3>
            <button onClick={() => setCurrentView('agents')} className="text-xs text-blue-400 hover:text-blue-300 transition-colors">
              查看全部 →
            </button>
          </div>
          {topAgents.length === 0 ? (
            <p className="text-sm text-gray-600 py-6 text-center">暂无智能体</p>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {topAgents.map(a => (
                <div key={a.name} className="px-3 py-2.5 rounded-xl bg-gray-800/50 border border-gray-800 hover:border-gray-700 transition-all">
                  <div className="text-sm font-medium truncate">{a.name}</div>
                  <div className="text-[11px] text-gray-600 mt-0.5 line-clamp-2">{a.description || '无描述'}</div>
                  {a.tools.length > 0 && (
                    <div className="text-[10px] text-gray-500 mt-1.5">{a.tools.slice(0, 3).map(t => t.name).join(', ')}{a.tools.length > 3 ? ` +${a.tools.length - 3}` : ''}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function QuickAction({ label, onClick }: { label: string; onClick: () => void }): JSX.Element {
  return (
    <button
      onClick={onClick}
      className="px-4 py-2.5 bg-gray-900 border border-gray-800 hover:border-blue-600/30 hover:bg-blue-600/5 rounded-xl text-sm transition-all hover:shadow-lg"
    >
      {label}
    </button>
  );
}
