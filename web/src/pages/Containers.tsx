import { useEffect, useState, useCallback } from 'react';
import { useApp } from '../context/AppContext';
import {
  listContainers,
  deleteContainer,
  containerStats,
  createContainer,
  unbindContainerKeep,
  startContainer,
} from '../api/client';
import type { ContainerInfo, ContainerStats as Stats, CreateContainerRequest } from '../types';
import type { JSX } from 'react';

/** 容器管理：容器池模式 —— 用户在此创建独立容器，任务创建时从池中选一个绑定。 */
export function Containers(): JSX.Element {
  const { addToast } = useApp();
  const [containers, setContainers] = useState<ContainerInfo[]>([]);
  const [stats, setStats] = useState<Stats>({ active_count: 0, max: 10, available: 10 });
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  // 创建容器对话框
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [r, s] = await Promise.all([listContainers(), containerStats()]);
      setContainers(r.containers);
      setStats(s);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载容器失败', message: err.message });
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => { load(); }, [load]);

  async function handleCreate() {
    setCreating(true);
    try {
      const req: CreateContainerRequest = newName.trim()
        ? { name: newName.trim() }
        : {};
      const rec = await createContainer(req);
      addToast({ type: 'success', title: '容器已创建', message: rec.container_name });
      setShowCreateModal(false);
      setNewName('');
      await load();
    } catch (err: any) {
      addToast({ type: 'error', title: '创建失败', message: err.message });
    } finally {
      setCreating(false);
    }
  }

  /** 解绑容器（保留容器回池中 available）。 */
  async function handleUnbind(containerName: string) {
    if (!window.confirm(`解绑容器 ${containerName}？\n容器将回到池中 available 状态，可被其他任务绑定。`)) return;
    setBusy(containerName);
    try {
      await unbindContainerKeep(containerName);
      addToast({ type: 'info', title: '容器已解绑', message: containerName });
      await load();
    } catch (err: any) {
      addToast({ type: 'error', title: '解绑失败', message: err.message });
    } finally {
      setBusy(null);
    }
  }

  /** 删除容器（停止+移除+清理记录）。 */
  async function handleDelete(name: string) {
    if (!window.confirm(`删除容器 ${name}？\n容器将停止并移除，无法恢复。`)) return;
    setBusy(name);
    try {
      await deleteContainer(name);
      addToast({ type: 'info', title: '容器已删除', message: name });
      await load();
    } catch (err: any) {
      addToast({ type: 'error', title: '删除失败', message: err.message });
    } finally {
      setBusy(null);
    }
  }

  /** 启动已停止的容器（保留原配置与绑定关系）。 */
  async function handleStart(name: string) {
    setBusy(name);
    try {
      await startContainer(name);
      addToast({ type: 'success', title: '容器已启动', message: name });
      await load();
    } catch (err: any) {
      addToast({ type: 'error', title: '启动失败', message: err.message });
    } finally {
      setBusy(null);
    }
  }

  function formatDate(ts: string): string {
    if (!ts) return '—';
    try {
      return new Date(ts).toLocaleString('zh-CN', {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      });
    } catch { return ts; }
  }

  // 池容器：active 且 session_id 为空
  const availableContainers = containers.filter(
    c => c.status === 'active' && !c.session_id,
  );
  // 已绑定任务的容器：active 且 session_id 非空
  const boundContainers = containers.filter(
    c => c.status === 'active' && !!c.session_id,
  );
  const stoppedContainers = containers.filter(c => c.status === 'stopped');
  const orphanContainers = containers.filter(c => c.status === 'orphan');

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">容器管理</h1>
          <p className="text-sm text-gray-500 mt-1">
            容器池 · 最多并发 {stats.max} 个 · 创建任务时从可用容器中选一个绑定
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowCreateModal(true)}
            disabled={stats.available <= 0}
            className="px-4 py-2 text-sm bg-emerald-600/10 hover:bg-emerald-600/20 text-emerald-400 rounded-xl border border-emerald-600/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title={stats.available <= 0 ? '容器并发名额已用尽' : '创建一个独立容器（不绑定任务）'}
          >
            + 创建容器
          </button>
          <button onClick={load} disabled={loading}
            className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl transition-colors disabled:opacity-50">
            {loading ? '加载中...' : '刷新'}
          </button>
        </div>
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <StatCard label="活跃容器" value={stats.active_count} total={stats.max} accent="emerald" />
        <StatCard label="可用容器" value={availableContainers.length} accent="blue" />
        <StatCard label="可用名额" value={stats.available} total={stats.max} accent="gray" />
        <StatCard label="孤儿容器" value={orphanContainers.length} accent={orphanContainers.length > 0 ? 'red' : 'gray'} />
      </div>

      {/* 可用容器（池中待选） */}
      <ContainerTable
        title="可用容器（池中待选）"
        emptyText="无可用容器 · 点击「+ 创建容器」新建一个独立容器"
        items={availableContainers}
        busy={busy}
        formatDate={formatDate}
        onDelete={handleDelete}
        onUnbind={handleUnbind}
        canDelete={true}
        canUnbind={false}
      />

      {/* 已绑定任务的容器 */}
      <ContainerTable
        title="已绑定任务的容器"
        emptyText="无已绑定任务的容器 · 在新建任务对话框中选择可用容器进行绑定"
        items={boundContainers}
        busy={busy}
        formatDate={formatDate}
        onDelete={handleDelete}
        onUnbind={handleUnbind}
        canDelete={true}
        canUnbind={true}
      />

      {orphanContainers.length > 0 && (
        <div className="mt-6">
          <div className="px-3 py-2 mb-2 bg-red-600/10 border border-red-600/20 rounded-lg text-xs text-red-400">
            ⚠ 检测到孤儿容器：对应任务已归档/删除但容器仍在，占用并发名额
          </div>
          <ContainerTable
            title="孤儿容器"
            emptyText=""
            items={orphanContainers}
            busy={busy}
            formatDate={formatDate}
            onDelete={handleDelete}
            canDelete={true}
            canUnbind={false}
          />
        </div>
      )}

      {stoppedContainers.length > 0 && (
        <div className="mt-6">
          <ContainerTable
            title="停止的容器"
            emptyText=""
            items={stoppedContainers}
            busy={busy}
            formatDate={formatDate}
            onDelete={handleDelete}
            onStart={handleStart}
            canDelete={true}
            canUnbind={false}
            canStart={true}
          />
        </div>
      )}

      {containers.length === 0 && !loading && (
        <div className="text-center py-20 text-gray-600">
          暂无容器记录 · 点击「+ 创建容器」新建一个独立容器
        </div>
      )}

      {/* 创建容器对话框 */}
      {showCreateModal && (
        <div className="fixed inset-0 z-40 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => !creating && setShowCreateModal(false)} />
          <div className="relative bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-md p-6 shadow-2xl animate-slide-up">
            <div className="flex items-center justify-between mb-5">
              <div>
                <h2 className="text-lg font-semibold">创建容器</h2>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  创建独立池容器，不绑定任何任务 · 可用名额：{stats.available} / {stats.max}
                </p>
              </div>
              <button
                onClick={() => !creating && setShowCreateModal(false)}
                disabled={creating}
                className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>

            <div className="space-y-4">
              <div className="px-3 py-3 rounded-xl border border-gray-800 bg-gray-900/60">
                <div className="text-xs text-gray-400 leading-relaxed">
                  创建后容器进入「可用容器」列表。在新建任务对话框中可从下拉框选择该容器绑定到任务。
                  容器创建时会挂载临时工作区；绑定到任务时会重建以挂载任务工作区。
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">
                  容器显示名（可选）
                </label>
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="留空自动生成，支持中文（如：渗透测试-01）"
                  disabled={creating}
                  className="w-full px-3 py-2.5 bg-gray-800/50 border border-gray-800 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-emerald-600/50 disabled:opacity-50"
                />
                <div className="mt-1.5 text-[10px] text-gray-600">
                  显示名仅用于前端展示；Docker 实际容器名由系统自动生成
                </div>
              </div>

              <div className="flex gap-2 pt-2">
                <button
                  onClick={() => setShowCreateModal(false)}
                  disabled={creating}
                  className="flex-1 px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl transition-colors disabled:opacity-50"
                >
                  取消
                </button>
                <button
                  onClick={handleCreate}
                  disabled={creating || stats.available <= 0}
                  className="flex-1 px-4 py-2 text-sm bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 rounded-xl border border-emerald-600/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {creating ? '创建中...' : '创建'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, total, accent }: { label: string; value: number; total?: number; accent: 'emerald' | 'blue' | 'red' | 'gray' }): JSX.Element {
  const colors = {
    emerald: 'text-emerald-400 bg-emerald-600/10 border-emerald-600/20',
    blue: 'text-blue-400 bg-blue-600/10 border-blue-600/20',
    red: 'text-red-400 bg-red-600/10 border-red-600/20',
    gray: 'text-gray-400 bg-gray-700/30 border-gray-700',
  };
  return (
    <div className={`p-4 rounded-xl border ${colors[accent]}`}>
      <div className="text-xs font-medium uppercase tracking-wider opacity-70">{label}</div>
      <div className="mt-1 text-2xl font-bold">
        {value}{total !== undefined && <span className="text-sm text-gray-500 ml-1">/ {total}</span>}
      </div>
    </div>
  );
}

function ContainerTable({
  title, emptyText, items, busy, formatDate, onDelete, onUnbind, onStart, canDelete, canUnbind, canStart,
}: {
  title: string;
  emptyText: string;
  items: ContainerInfo[];
  busy: string | null;
  formatDate: (ts: string) => string;
  onDelete: (name: string) => void;
  onUnbind?: (containerName: string) => void;
  onStart?: (name: string) => void;
  canDelete: boolean;
  canUnbind: boolean;
  canStart?: boolean;
}): JSX.Element {
  if (items.length === 0 && emptyText) {
    return (
      <div className="mb-6">
        <h2 className="text-sm font-semibold text-gray-400 mb-2">{title}</h2>
        <div className="text-center py-6 text-xs text-gray-600 border border-gray-800 rounded-xl">
          {emptyText}
        </div>
      </div>
    );
  }
  if (items.length === 0) return <></>;
  return (
    <div className="mb-6">
      <h2 className="text-sm font-semibold text-gray-400 mb-2">{title} ({items.length})</h2>
      <div className="overflow-hidden rounded-2xl border border-gray-800">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900/50">
              <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase">容器名</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase">任务 ID</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase">运行时</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase">状态</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase">启动时间</th>
              {(canDelete || canUnbind || canStart) && <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase">操作</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {items.map(c => (
              <tr key={c.container_name} className="hover:bg-gray-900/30 transition-colors">
                <td className="px-4 py-3">
                  {c.display_name ? (
                    <div>
                      <div className="text-xs text-gray-200 font-medium">{c.display_name}</div>
                      <div className="text-[10px] font-mono text-gray-600 mt-0.5">{c.container_name}</div>
                    </div>
                  ) : (
                    <span className="text-xs font-mono text-gray-300">{c.container_name}</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {c.session_id ? (
                    <span className="text-xs font-mono text-gray-400">{c.session_id.slice(0, 16)}...</span>
                  ) : (
                    <span className="text-xs text-gray-600 italic">（池中待选）</span>
                  )}
                </td>
                <td className="px-4 py-3"><span className="text-xs text-gray-400">{c.runtime || '—'}</span></td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
                    c.status === 'active' ? 'bg-emerald-600/10 text-emerald-400' :
                    c.status === 'stopped' ? 'bg-amber-600/10 text-amber-400' :
                    'bg-red-600/10 text-red-400'
                  }`}>{c.status}</span>
                </td>
                <td className="px-4 py-3"><span className="text-xs text-gray-600">{formatDate(c.started_at)}</span></td>
                {(canDelete || canUnbind || canStart) && (
                  <td className="px-4 py-3">
                    <div className="flex gap-1.5">
                      {canStart && onStart && (
                        <button
                          onClick={() => onStart(c.container_name)}
                          disabled={busy === c.container_name}
                          className="px-2.5 py-1 text-xs bg-blue-600/10 hover:bg-blue-600/20 text-blue-400 rounded-lg border border-blue-600/20 transition-colors disabled:opacity-50"
                        >
                          {busy === c.container_name ? '启动中...' : '启动'}
                        </button>
                      )}
                      {canUnbind && onUnbind && c.session_id && (
                        <button
                          onClick={() => onUnbind(c.container_name)}
                          disabled={busy === c.container_name}
                          className="px-2.5 py-1 text-xs bg-amber-600/10 hover:bg-amber-600/20 text-amber-400 rounded-lg border border-amber-600/20 transition-colors disabled:opacity-50"
                        >
                          {busy === c.container_name ? '解绑中...' : '解绑'}
                        </button>
                      )}
                      {canDelete && (
                        <button
                          onClick={() => onDelete(c.container_name)}
                          disabled={busy === c.container_name}
                          className="px-2.5 py-1 text-xs bg-red-600/10 hover:bg-red-600/20 text-red-400 rounded-lg border border-red-600/20 transition-colors disabled:opacity-50"
                        >
                          {busy === c.container_name ? '删除中...' : '删除'}
                        </button>
                      )}
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
