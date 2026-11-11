import { useCallback, useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import { exportAudit, getAuditStats, listAudit, verifyAudit } from '../api/client';
import type { AuditEvent, AuditStats } from '../types';
import type { JSX } from 'react';

const PAGE_SIZE = 50;

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function toIso(value: string): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toISOString();
}

function statusClass(status: string): string {
  switch (status) {
    case 'error': return 'text-red-400 bg-red-500/10 border-red-500/20';
    case 'denied': return 'text-amber-400 bg-amber-500/10 border-amber-500/20';
    case 'retry': return 'text-orange-400 bg-orange-500/10 border-orange-500/20';
    default: return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20';
  }
}

export function Audit(): JSX.Element {
  const { addToast } = useApp();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<AuditStats | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<string>('');
  const [selected, setSelected] = useState<AuditEvent | null>(null);

  const [search, setSearch] = useState('');
  const [action, setAction] = useState('');
  const [actor, setActor] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [status, setStatus] = useState('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');

  const filterParams = useCallback(() => ({
    search, action, actor, session_id: sessionId, status,
    since: toIso(since), until: toIso(until),
  }), [search, action, actor, sessionId, status, since, until]);

  const load = useCallback(async (nextOffset = 0) => {
    setLoading(true);
    try {
      const data = await listAudit({ ...filterParams(), limit: PAGE_SIZE, offset: nextOffset });
      setEvents(data.events);
      setTotal(data.total);
      setOffset(nextOffset);
    } catch (err) {
      addToast({ type: 'error', title: '加载审计日志失败', message: (err as Error).message });
    } finally {
      setLoading(false);
    }
  }, [addToast, filterParams]);

  useEffect(() => {
    load(0);
    getAuditStats()
      .then(setStats)
      .catch(() => { /* 统计失败不阻塞列表 */ });
    // 仅在首次进入页面时加载；筛选条件变化由“查询”按钮触发，
    // 避免搜索框每输入一个字符就打一次 API。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleVerify() {
    setVerifying(true);
    try {
      const result = await verifyAudit();
      if (result.ok) {
        setVerifyResult(`✓ 哈希链完整（${result.total} 条）`);
        addToast({ type: 'success', title: '审计完整性校验通过', message: `共校验 ${result.total} 条事件` });
      } else {
        setVerifyResult(`✗ 第 ${(result.index ?? 0) + 1} 条校验失败（${result.broken_at ?? '未知'}）`);
        addToast({ type: 'error', title: '审计完整性校验失败', message: result.broken_at ?? '哈希链断裂' });
      }
    } catch (err) {
      addToast({ type: 'error', title: '校验失败', message: (err as Error).message });
    } finally {
      setVerifying(false);
    }
  }

  async function handleExport(format: 'jsonl' | 'csv') {
    try {
      await exportAudit(format, filterParams());
      addToast({ type: 'success', title: `已导出 ${format.toUpperCase()}` });
    } catch (err) {
      addToast({ type: 'error', title: '导出失败', message: (err as Error).message });
    }
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-start justify-between gap-4 mb-5">
          <div>
            <h1 className="text-xl font-semibold">审计日志</h1>
            <p className="text-xs text-gray-500 mt-1">
              统一记录登录、配置变更、会话与工具调用；只追加、哈希链可校验。
              完整工具输出仍在会话日志中。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleVerify}
              disabled={verifying}
              className="px-3 py-2 text-xs rounded-lg border border-gray-700 text-gray-300 hover:bg-gray-800 disabled:opacity-50"
            >
              {verifying ? '校验中…' : '校验完整性'}
            </button>
            <button
              onClick={() => handleExport('jsonl')}
              className="px-3 py-2 text-xs rounded-lg border border-gray-700 text-gray-300 hover:bg-gray-800"
            >
              导出 JSONL
            </button>
            <button
              onClick={() => handleExport('csv')}
              className="px-3 py-2 text-xs rounded-lg border border-gray-700 text-gray-300 hover:bg-gray-800"
            >
              导出 CSV
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 mb-4 text-xs text-gray-500">
          <span>共 <span className="text-gray-200 font-medium">{total}</span> 条</span>
          {stats && (
            <span>
              动作 {stats.by_action.length} 类 · 状态{' '}
              {stats.by_status.map(s => `${s.status}:${s.count}`).join(' / ') || '—'}
            </span>
          )}
          {verifyResult && (
            <span className={verifyResult.startsWith('✓') ? 'text-emerald-400' : 'text-red-400'}>
              {verifyResult}
            </span>
          )}
        </div>

        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-4 mb-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="搜索动作/资源/主体/详情"
              className="bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs outline-none focus:border-blue-600/50"
            />
            <input
              value={action}
              onChange={e => setAction(e.target.value)}
              placeholder="动作，如 tool.* 或 session.delete"
              className="bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs outline-none focus:border-blue-600/50"
            />
            <input
              value={actor}
              onChange={e => setActor(e.target.value)}
              placeholder="主体（用户名 / session:xxx）"
              className="bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs outline-none focus:border-blue-600/50"
            />
            <input
              value={sessionId}
              onChange={e => setSessionId(e.target.value)}
              placeholder="会话 ID"
              className="bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs outline-none focus:border-blue-600/50"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={status}
              onChange={e => setStatus(e.target.value)}
              className="bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs outline-none"
            >
              <option value="">全部状态</option>
              <option value="ok">ok</option>
              <option value="error">error</option>
              <option value="denied">denied</option>
              <option value="retry">retry</option>
            </select>
            <label className="text-[11px] text-gray-500 flex items-center gap-1">
              从
              <input
                type="datetime-local"
                value={since}
                onChange={e => setSince(e.target.value)}
                className="bg-gray-950 border border-gray-800 rounded-lg px-2 py-1.5 text-xs"
              />
            </label>
            <label className="text-[11px] text-gray-500 flex items-center gap-1">
              到
              <input
                type="datetime-local"
                value={until}
                onChange={e => setUntil(e.target.value)}
                className="bg-gray-950 border border-gray-800 rounded-lg px-2 py-1.5 text-xs"
              />
            </label>
            <button
              onClick={() => load(0)}
              disabled={loading}
              className="ml-auto px-4 py-2 text-xs rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50"
            >
              {loading ? '查询中…' : '查询'}
            </button>
          </div>
        </div>

        <div className="bg-gray-900 border border-gray-800 rounded-2xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-950/60 text-gray-500">
                <tr>
                  <th className="text-left font-medium px-4 py-3 w-44">时间</th>
                  <th className="text-left font-medium px-4 py-3">动作</th>
                  <th className="text-left font-medium px-4 py-3">主体</th>
                  <th className="text-left font-medium px-4 py-3">资源</th>
                  <th className="text-left font-medium px-4 py-3 w-20">状态</th>
                  <th className="text-left font-medium px-4 py-3">会话</th>
                </tr>
              </thead>
              <tbody>
                {events.map(event => (
                  <tr
                    key={event.event_id}
                    onClick={() => setSelected(event)}
                    className="border-t border-gray-800/70 hover:bg-gray-800/40 cursor-pointer"
                  >
                    <td className="px-4 py-2.5 text-gray-500 whitespace-nowrap">{formatTime(event.ts)}</td>
                    <td className="px-4 py-2.5 font-mono text-gray-200">{event.action}</td>
                    <td className="px-4 py-2.5 text-gray-400">
                      {event.actor_id || event.actor_type}
                      <span className="ml-1 text-gray-600">({event.actor_type})</span>
                    </td>
                    <td className="px-4 py-2.5 text-gray-400 font-mono truncate max-w-[240px]">
                      {event.resource_type}
                      {event.resource_id ? `:${event.resource_id}` : ''}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`px-2 py-0.5 rounded border ${statusClass(event.status)}`}>
                        {event.status}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-gray-500 font-mono truncate max-w-[180px]">
                      {event.session_id || '—'}
                    </td>
                  </tr>
                ))}
                {!loading && events.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-12 text-center text-gray-600">
                      暂无审计事件
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-800 text-xs text-gray-500">
            <span>第 {page} / {pages} 页</span>
            <div className="flex gap-2">
              <button
                onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
                disabled={offset === 0 || loading}
                className="px-3 py-1.5 rounded-lg border border-gray-700 hover:bg-gray-800 disabled:opacity-40"
              >
                上一页
              </button>
              <button
                onClick={() => load(offset + PAGE_SIZE)}
                disabled={offset + PAGE_SIZE >= total || loading}
                className="px-3 py-1.5 rounded-lg border border-gray-700 hover:bg-gray-800 disabled:opacity-40"
              >
                下一页
              </button>
            </div>
          </div>
        </div>
      </div>

      {selected && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6" onClick={() => setSelected(null)}>
          <div
            className="w-full max-w-3xl max-h-[80vh] overflow-y-auto bg-gray-900 border border-gray-700 rounded-2xl p-5"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <h2 className="text-base font-semibold font-mono">{selected.action}</h2>
                <p className="text-xs text-gray-500 mt-1">
                  {formatTime(selected.ts)} · {selected.actor_id || selected.actor_type} · {selected.ip || '—'}
                </p>
              </div>
              <button
                onClick={() => setSelected(null)}
                className="text-gray-500 hover:text-gray-200 text-sm"
              >
                关闭
              </button>
            </div>
            <dl className="grid grid-cols-2 gap-2 text-xs mb-4">
              <div><dt className="text-gray-600">事件 ID</dt><dd className="font-mono text-gray-300 break-all">{selected.event_id}</dd></div>
              <div><dt className="text-gray-600">状态</dt><dd>{selected.status} ({selected.status_code || '—'})</dd></div>
              <div><dt className="text-gray-600">资源</dt><dd className="font-mono text-gray-300">{selected.resource_type}{selected.resource_id ? `:${selected.resource_id}` : ''}</dd></div>
              <div><dt className="text-gray-600">会话</dt><dd className="font-mono text-gray-300 break-all">{selected.session_id || '—'}</dd></div>
              <div className="col-span-2"><dt className="text-gray-600">User-Agent</dt><dd className="text-gray-400 break-all">{selected.ua || '—'}</dd></div>
            </dl>
            <div className="text-xs text-gray-600 mb-1">详情</div>
            <pre className="bg-gray-950 border border-gray-800 rounded-xl p-3 text-[11px] leading-relaxed overflow-x-auto whitespace-pre-wrap break-all">
              {JSON.stringify(selected.detail, null, 2)}
            </pre>
            <div className="mt-3 text-[10px] text-gray-600 font-mono break-all">
              prev: {selected.prev_hash}<br />hash: {selected.hash}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
