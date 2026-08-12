/**
 * 流水线管理页面
 * 统一展示内置模板 + 用户自定义流水线，支持编辑/激活/删除/执行。
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from '../api/client';
import type { AgentMetadata } from '../types';

type PipelineSource = 'builtin' | 'custom';

interface PipelineNode {
  id: string;
  type: string;
  display_name?: string;
  description?: string;
  agent?: string;
  prompt_template?: string;
  branches?: Array<{ when?: string; goto: string; label?: string; default?: boolean }>;
  parallel_branches?: Array<{ node_id: string }>;
  confirm_prompt?: string;
  confirm_options?: string[];
  confirm_branches?: Record<string, string>;
}

interface PipelineEdge {
  source: string;
  target: string;
  label?: string;
  condition?: string;
}

interface UnifiedPipeline {
  id: string;
  name: string;
  description: string;
  type: string;       // 'auto' | 'manual'
  source: PipelineSource;
  nodes?: PipelineNode[];
  edges?: PipelineEdge[];
  active?: boolean;
  created_at?: string;
  updated_at?: string;
}

interface RunRecord {
  run_id: string;
  pipeline_id: string;
  pipeline_name?: string;
  status: string;
  started_at?: string;
  finished_at?: string;
  events_count?: number;
}

const STORAGE_KEY = 'baize_pipeline_editor';
const ICONS: Record<string, string> = {
  agent: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z',
  decision: 'M3 5v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2zm7 7h4v4h-4v-4zm0-6h4v4h-4V6z',
  confirm: 'M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z',
  parallel: 'M4 6h6v12H4V6zm10 0h6v12h-6V6z',
  transform: 'M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  subpipeline: 'M4 4h16v16H4V4zm2 2h12v12H6V6zm2 2h8v8H8V8z',
  receiver: 'M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h6l6-6V5c0-1.1-.9-2-2-2zm-5 4h4v4h-4V7zm-2 4H8v-4h4v4zm-2 2h4v4h-4v-4z',
  datatransformer: 'M4 21V3h16v18l-8-5-8 5z',
};

const NODE_LABELS: Record<string, string> = {
  receiver: '数据接收器',
  datatransformer: '数据转换',
  agent: '智能体',
  decision: '条件判断',
  confirm: '人工确认',
  parallel: '并行执行',
  transform: '数据转换',
  subpipeline: '子流水线',
};

const NODE_COLORS: Record<string, string> = {
  receiver: '#06b6d4',
  datatransformer: '#14b8a6',
  agent: '#3b82f6',
  decision: '#f59e0b',
  confirm: '#ec4899',
  parallel: '#8b5cf6',
  transform: '#10b981',
  subpipeline: '#6366f1',
};

const NODE_GRADIENTS: Record<string, [string, string]> = {
  receiver: ['#06b6d4', '#0891b2'],
  datatransformer: ['#14b8a6', '#0f766e'],
  agent: ['#3b82f6', '#1d4ed8'],
  decision: ['#f59e0b', '#b45309'],
  confirm: ['#ec4899', '#be185d'],
  parallel: ['#8b5cf6', '#6d28d9'],
  transform: ['#10b981', '#047857'],
  subpipeline: ['#6366f1', '#4338ca'],
};

export default function PipelineEditor() {
  const [activeTab, setActiveTab] = useState<'pipelines' | 'history'>('pipelines');
  const [pipelines, setPipelines] = useState<UnifiedPipeline[]>([]);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [selectedPipeline, setSelectedPipeline] = useState<UnifiedPipeline | null>(null);
  const [loading, setLoading] = useState(true);

  const [feedback, setFeedback] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // 编辑模式
  const [editing, setEditing] = useState(false);
  const [editData, setEditData] = useState<Partial<UnifiedPipeline>>({});

  // 创建流水线
  const [showCreate, setShowCreate] = useState(false);

  // 测试执行
  const [showTest, setShowTest] = useState<string | null>(null); // pipeline_id
  const [testInput, setTestInput] = useState('');
  const [testRunning, setTestRunning] = useState(false);
  const [testEvents, setTestEvents] = useState<Array<{ node_id?: string; event_type?: string; message?: string; timestamp?: string }>>([]);

  // ---- 数据加载 ----

  const loadPipelines = useCallback(async () => {
    try {
      const [tpl, custom] = await Promise.all([
        api.listPipelineTemplates(),
        api.listCustomPipelines(),
      ]);

      const activeStatuses = new Map<string, boolean>();
      for (const t of tpl.templates) {
        if (t.type === 'auto') {
          try {
            const st = await api.getPipelineStatus(t.id);
            activeStatuses.set(t.id, st.active);
          } catch { /* ignore */ }
        }
      }

      const unified: UnifiedPipeline[] = [
        // 内置模板
        ...tpl.templates.map((t: any) => ({
          id: t.id,
          name: t.name,
          description: t.description || '',
          type: t.type || 'manual',
          source: 'builtin' as PipelineSource,
          nodes: t.nodes || t.steps || [],
          edges: t.edges || [],
          active: activeStatuses.get(t.id) ?? false,
        })),
        // 用户自定义流水线
        ...custom.pipelines.map((p: any) => ({
          id: p.id,
          name: p.name,
          description: p.description || '',
          type: p.type || 'manual',
          source: 'custom' as PipelineSource,
          nodes: p.nodes || p.steps || [],
          edges: p.edges || [],
          created_at: p.created_at,
          updated_at: p.updated_at,
        })),
      ];

      setPipelines(unified);
    } catch (e: any) {
      showFeedback('err', '加载流水线失败: ' + (e.message || '未知错误'));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      const resp = await api.listRuns({ limit: 50 });
      setRuns(resp.runs || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    loadPipelines();
    if (activeTab === 'history') loadRuns();
  }, [activeTab, loadPipelines, loadRuns]);

  // 恢复保存的状态
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const { tab } = JSON.parse(saved);
        if (tab) setActiveTab(tab);
      }
    } catch { /* ignore */ }
  }, []);

  const saveState = (tab: string) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ tab }));
  };

  function showFeedback(type: 'ok' | 'err', msg: string) {
    setFeedback({ type, msg });
    setTimeout(() => setFeedback(null), 4000);
  }

  // ---- 激活/停止自动化 ----

  async function toggleActivation(p: UnifiedPipeline) {
    try {
      if (p.active) {
        await api.deactivatePipeline(p.id);
        showFeedback('ok', `已停止 "${p.name}"`);
      } else {
        await api.activatePipeline(p.id);
        showFeedback('ok', `已激活 "${p.name}"`);
      }
      setPipelines(prev =>
        prev.map(pp => (pp.id === p.id ? { ...pp, active: !pp.active } : pp))
      );
    } catch (e: any) {
      showFeedback('err', '操作失败: ' + (e.message || ''));
    }
  }

  // ---- 删除 ----

  async function handleDelete(p: UnifiedPipeline) {
    try {
      if (p.source === 'custom') {
        await api.deleteCustomPipeline(p.id);
      } else {
        await api.deleteBuiltinTemplate(p.id);
      }
      setConfirmDelete(null);
      showFeedback('ok', `已删除 "${p.name}"`);
      setPipelines(prev => prev.filter(pp => pp.id !== p.id));
      if (selectedPipeline?.id === p.id) setSelectedPipeline(null);
    } catch (e: any) {
      showFeedback('err', '删除失败: ' + (e.message || ''));
    }
  }




  // ---- 查看详情 ----

  function selectPipeline(p: UnifiedPipeline) {
    setSelectedPipeline(p);
    setEditing(false);
  }

  // ---- 编辑（仅自定义） ----

  function startEdit(p: UnifiedPipeline) {
    setEditing(true);
    setEditData({
      name: p.name,
      description: p.description,
      nodes: p.nodes,
      edges: p.edges,
    });
  }

  async function saveEdit() {
    if (!selectedPipeline || selectedPipeline.source !== 'custom') return;
    try {
      await (api as any).updateCustomPipeline(selectedPipeline.id, {
        name: editData.name || '',
        description: editData.description || '',
        nodes: editData.nodes || [],
        edges: editData.edges || [],
        steps: (editData.nodes || []).map((n: any) => ({
          agent_name: n.agent || n.display_name || n.id,
          display_name: n.display_name || n.id,
          description: n.description || '',
        })),
      });
      showFeedback('ok', '保存成功');
      setEditing(false);
      loadPipelines();
    } catch (e: any) {
      showFeedback('err', '保存失败: ' + (e.message || ''));
    }
  }

  // ---- 渲染 ----

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-slate-500 animate-pulse">加载中...</div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
          流水线管理
        </h1>
        <div className="flex gap-2 items-center">
          <button
            onClick={() => setShowCreate(true)}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 rounded-xl transition-colors font-medium"
          >
            + 新建流水线
          </button>
          {feedback && (
            <div
              className={`px-3 py-1.5 rounded-lg text-sm font-medium ${
                feedback.type === 'ok'
                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400'
                  : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
              }`}
            >
              {feedback.msg}
            </div>
          )}
          {confirmDelete && (
            <div className="flex items-center gap-2 px-3 py-1.5 bg-red-50 rounded-lg border border-red-200 dark:bg-red-900/20 dark:border-red-800 text-sm">
              <span className="text-red-700 dark:text-red-400">确认删除?</span>
              <button
                onClick={() => {
                  const p = pipelines.find(pp => pp.id === confirmDelete);
                  if (p) handleDelete(p);
                }}
                className="px-2 py-0.5 bg-red-600 text-white rounded text-xs font-medium"
              >
                删除
              </button>
              <button
                onClick={() => setConfirmDelete(null)}
                className="px-2 py-0.5 bg-slate-200 dark:bg-slate-700 rounded text-xs"
              >
                取消
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex border-b border-slate-200 dark:border-slate-700 gap-1">
        {([
          ['pipelines', '流水线'],
          ['history', '执行历史'],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            onClick={() => { setActiveTab(id); saveState(id); }}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
              activeTab === id
                ? 'border-blue-600 text-blue-600 dark:border-blue-400 dark:text-blue-400'
                : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ====== 流水线列表 ====== */}
      {activeTab === 'pipelines' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {/* 左侧：流水线卡片列表 */}
          <div className="lg:col-span-1 space-y-3 max-h-[calc(100vh-260px)] overflow-y-auto pr-1">
            {pipelines.length === 0 && (
              <div className="text-center py-12 text-slate-400 dark:text-slate-500">
                暂无流水线，请先{" "}
                <span className="text-blue-500 cursor-pointer" onClick={() => loadPipelines()}>
                  刷新
                </span>
              </div>
            )}
            {pipelines.map(p => (
              <div
                key={p.id}
                onClick={() => selectPipeline(p)}
                className={`p-4 rounded-xl border cursor-pointer transition-all ${
                  selectedPipeline?.id === p.id
                    ? 'border-blue-400 bg-blue-50 dark:border-blue-500 dark:bg-blue-900/20 shadow-sm'
                    : 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600 bg-white dark:bg-slate-800'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-semibold text-sm text-slate-900 dark:text-slate-100 truncate">
                        {p.name}
                      </h3>
                      {p.source === 'builtin' ? (
                        <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400">
                          预置
                        </span>
                      ) : (
                        <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400">
                          自定义
                        </span>
                      )}
                      <span
                        className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded ${
                          p.type === 'auto'
                            ? 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400'
                            : 'bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400'
                        }`}
                      >
                        {p.type === 'auto' ? '自动化' : '人工'}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-slate-500 dark:text-slate-400 line-clamp-2">
                      {p.description}
                    </p>
                  </div>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  {p.type === 'auto' && (
                    <button
                      onClick={e => { e.stopPropagation(); toggleActivation(p); }}
                      className={`text-[10px] px-2 py-0.5 rounded-full font-medium transition-colors ${
                        p.active
                          ? 'bg-emerald-500 text-white'
                          : 'bg-slate-200 text-slate-500 dark:bg-slate-600 dark:text-slate-400'
                      }`}
                    >
                      {p.active ? '● 已开启' : '○ 已关闭'}
                    </button>
                  )}
                  <button
                    onClick={e => {
                      e.stopPropagation();
                      selectPipeline(p);
                      setShowTest(p.id);
                      setTestInput('');
                      setTestEvents([]);
                    }}
                    className="text-[10px] px-2 py-0.5 rounded-full font-medium bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400 hover:bg-blue-200 transition-colors"
                  >
                    测试
                  </button>
                  <button
                    onClick={e => {
                      e.stopPropagation();
                      setConfirmDelete(p.id);
                    }}
                    className="text-[10px] px-2 py-0.5 rounded-full font-medium text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                  >
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>

          {/* 右侧：详情面板 */}
          <div className="lg:col-span-2">
            {!selectedPipeline ? (
              <div className="flex items-center justify-center h-64 border border-dashed border-slate-300 dark:border-slate-600 rounded-xl text-sm text-slate-400 dark:text-slate-500">
                选择左侧流水线查看详情
              </div>
            ) : editing ? (
              <PipelineEditPanel
                pipeline={selectedPipeline}
                editData={editData}
                setEditData={setEditData}
                onSave={saveEdit}
                onCancel={() => setEditing(false)}
              />
            ) : (
              <PipelineDetailPanel
                pipeline={selectedPipeline}
                onEdit={() => startEdit(selectedPipeline)}
                onTest={() => {
                  setShowTest(selectedPipeline.id);
                  setTestInput('');
                  setTestEvents([]);
                }}
                submitting={testRunning}
                isCustom={selectedPipeline.source === 'custom'}
              />
            )}
          </div>
        </div>
      )}

      {/* ====== 执行历史 ====== */}
      {activeTab === 'history' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300">历史执行记录</h2>
            <button
              onClick={loadRuns}
              className="text-xs text-blue-500 hover:text-blue-600"
            >
              刷新
            </button>
          </div>
          {runs.length === 0 ? (
            <div className="text-center py-12 text-slate-400 dark:text-slate-500 text-sm">
              暂无执行记录
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 dark:border-slate-700 text-left text-slate-500 dark:text-slate-400">
                    <th className="pb-2 font-medium">Run ID</th>
                    <th className="pb-2 font-medium">流水线</th>
                    <th className="pb-2 font-medium">状态</th>
                    <th className="pb-2 font-medium">开始时间</th>
                    <th className="pb-2 font-medium">事件数</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map(r => (
                    <tr key={r.run_id} className="border-b border-slate-100 dark:border-slate-800">
                      <td className="py-2 font-mono text-xs text-slate-600 dark:text-slate-400">
                        {r.run_id.slice(0, 12)}...
                      </td>
                      <td className="py-2 text-slate-700 dark:text-slate-300">
                        {r.pipeline_name || r.pipeline_id}
                      </td>
                      <td className="py-2">
                        <RunStatusBadge status={r.status} />
                      </td>
                      <td className="py-2 text-xs text-slate-500 dark:text-slate-400">
                        {r.started_at ? new Date(r.started_at).toLocaleString() : '-'}
                      </td>
                      <td className="py-2 text-slate-500 dark:text-slate-400">
                        {r.events_count ?? '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ====== 创建流水线弹窗（拖拽流程图编辑器） ====== */}
      {showCreate && <CreatePipelineModal
        onClose={() => setShowCreate(false)}
        onCreated={(name) => { showFeedback('ok', `流水线「${name}」已创建`); setShowCreate(false); loadPipelines(); }}
        showFeedback={showFeedback}
      />}

      {/* ====== 测试流水线弹窗 ====== */}
      {showTest && (() => {
        const testPipeline = pipelines.find(p => p.id === showTest);
        if (!testPipeline) return null;
        return (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowTest(null)}>
            <div className="w-full max-w-2xl mx-4 bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl max-h-[85vh] flex flex-col" onClick={e => e.stopPropagation()}>
              <div className="flex items-center justify-between p-5 border-b border-gray-800">
                <div>
                  <h2 className="text-lg font-semibold text-slate-100">测试流水线: {testPipeline.name}</h2>
                  <p className="text-xs text-gray-500 mt-0.5">输入测试数据，观察流水线各节点的运行情况</p>
                </div>
                <button onClick={() => setShowTest(null)} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
                </button>
              </div>
              <div className="p-5 space-y-4 overflow-y-auto flex-1">
                <div>
                  <label className="block text-xs font-semibold text-gray-400 mb-1.5">测试输入数据</label>
                  <textarea
                    value={testInput} onChange={e => setTestInput(e.target.value)}
                    rows={4} placeholder='输入 JSON 格式的测试数据，例如: {"target": "example.com", "action": "scan"}'
                    className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none resize-none font-mono"
                  />
                </div>
                <button onClick={async () => {
                  setTestRunning(true);
                  setTestEvents([]);
                  try {
                    let inputData: any = { trigger: 'test' };
                    if (testInput.trim()) {
                      try { inputData = { ...inputData, context: JSON.parse(testInput) }; }
                      catch { inputData = { ...inputData, context: { raw_input: testInput } }; }
                    }
                    const resp = await api.submitRun({ pipeline_id: testPipeline.id, context: inputData });
                    setTestEvents([{ event_type: 'started', message: `测试已提交 (Run #${resp.run_id.slice(0, 8)})`, timestamp: new Date().toISOString() }]);
                    let attempts = 0;
                    const poll = setInterval(async () => {
                      attempts++;
                      try {
                        const result: any = await api.getRun(resp.run_id);
                        const status = result.run || result;
                        const statusEvents = (status.events || []) as any[];
                        if (statusEvents.length > testEvents.length) {
                          setTestEvents(prev => {
                            const existingIds = new Set(prev.map((e: any) => e.node_id || e.event_type));
                            const newEvents = statusEvents.filter((e: any) => !existingIds.has(e.id || e.node_id)).map((e: any) => ({
                              node_id: e.node_id || e.id, event_type: e.event_type || e.type || 'step',
                              message: e.message || e.data?.message || JSON.stringify(e.data || {}),
                              timestamp: e.timestamp || new Date().toISOString()
                            }));
                            return [...prev, ...newEvents];
                          });
                        }
                        const st = (status as any).status || (status as any).state;
                        if (['completed', 'failed', 'cancelled'].includes(st) || attempts > 60) {
                          clearInterval(poll);
                          setTestEvents(prev => [...prev, { event_type: st, message: `流水线${st === 'completed' ? '执行完成' : st === 'failed' ? '执行失败' : '已取消'}`, timestamp: new Date().toISOString() }]);
                        }
                      } catch { clearInterval(poll); }
                    }, 1500);
                  } catch (e: any) {
                    setTestEvents([{ event_type: 'error', message: '提交测试失败: ' + (e.message || '未知'), timestamp: new Date().toISOString() }]);
                  } finally { setTestRunning(false); }
                }} disabled={testRunning}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 rounded-xl text-sm font-medium transition-colors">
                  {testRunning ? '提交中...' : '运行测试'}
                </button>
                {testEvents.length > 0 && (
                  <div>
                    <h3 className="text-xs font-semibold text-gray-400 mb-2 uppercase">执行日志</h3>
                    <div className="space-y-1.5 max-h-64 overflow-y-auto">
                      {testEvents.map((ev, i) => {
                        const colorMap: Record<string, string> = { started: 'text-blue-400', step: 'text-gray-300', completed: 'text-emerald-400', failed: 'text-red-400', cancelled: 'text-yellow-400', error: 'text-red-500' };
                        return (
                          <div key={i} className="flex items-start gap-2 text-xs">
                            <span className={`shrink-0 mt-0.5 ${colorMap[ev.event_type || ''] || 'text-gray-400'}`}>
                              {ev.event_type === 'started' ? '' : ev.event_type === 'completed' ? '' : ev.event_type === 'failed' ? '' : ''}
                            </span>
                            <div className="flex-1">
                              {ev.node_id && <span className="font-mono text-[10px] px-1 py-0.5 bg-gray-800 rounded text-gray-400 mr-1.5">{ev.node_id}</span>}
                              <span className="text-gray-300">{ev.message}</span>
                            </div>
                            {ev.timestamp && <span className="shrink-0 text-[10px] text-gray-600">{new Date(ev.timestamp).toLocaleTimeString()}</span>}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })()}

    </div>
  );
}

// ===== 子组件 =====

function RunStatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    pending: 'bg-slate-100 text-slate-600',
    running: 'bg-blue-100 text-blue-600',
    waiting_confirm: 'bg-amber-100 text-amber-600',
    completed: 'bg-emerald-100 text-emerald-600',
    failed: 'bg-red-100 text-red-600',
    cancelled: 'bg-slate-100 text-slate-500',
  };
  const labels: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    waiting_confirm: '等待确认',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  };
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${map[status] || 'bg-slate-100 text-slate-600'}`}>
      {labels[status] || status}
    </span>
  );
}

function PipelineDetailPanel({ pipeline, onEdit, onTest, submitting, isCustom }: {
  pipeline: UnifiedPipeline;
  onEdit: () => void;
  onTest: () => void;
  submitting: boolean;
  isCustom: boolean;
}) {
  const [viewMode, setViewMode] = useState<'list' | 'graph'>('graph');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // 画布拖拽
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const dragRef = useRef({ startX: 0, startY: 0, panX: 0, panY: 0 });

  function handlePanDown(e: React.MouseEvent) {
    const target = e.target as HTMLElement;
    if (target.closest('[data-node]') || target.closest('[data-edge]')) return;
    setIsDragging(true);
    dragRef.current = { startX: e.clientX, startY: e.clientY, panX: panOffset.x, panY: panOffset.y };
  }
  function handlePanMove(e: React.MouseEvent) {
    if (!isDragging) return;
    setPanOffset({ x: dragRef.current.panX + (e.clientX - dragRef.current.startX), y: dragRef.current.panY + (e.clientY - dragRef.current.startY) });
  }
  function handlePanUp() { setIsDragging(false); }

  // 计算 DAG 布局
  function computeLayout() {
    if (!pipeline.nodes || pipeline.nodes.length === 0) return { positions: [], layers: [], adj: new Map() };
    const nodes = pipeline.nodes;
    const edges = pipeline.edges || [];
    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    const inDegree = new Map<string, number>();
    const adj = new Map<string, string[]>();
    nodes.forEach(n => { inDegree.set(n.id, 0); adj.set(n.id, []); });
    edges.forEach(e => {
      if (!adj.has(e.source)) adj.set(e.source, []);
      adj.get(e.source)!.push(e.target);
      inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
    });
    const layers: string[][] = [];
    let queue: string[] = [];
    inDegree.forEach((deg, id) => { if (deg === 0) queue.push(id); });
    if (queue.length === 0 && nodes.length > 0) queue.push(nodes[0].id);
    let remaining = nodes.length;
    while (queue.length > 0 && remaining > 0) {
      const layer: string[] = [];
      const nextQ: string[] = [];
      for (const id of queue) {
        layer.push(id); remaining--;
        for (const nxt of (adj.get(id) || [])) {
          const nd = inDegree.get(nxt)! - 1; inDegree.set(nxt, nd);
          if (nd === 0) nextQ.push(nxt);
        }
      }
      if (layer.length > 0) layers.push(layer);
      if (nextQ.length === 0 && remaining > 0) {
        const rest: string[] = [];
        inDegree.forEach((deg, id) => { if (deg > 0) { rest.push(id); inDegree.set(id, 0); } });
        if (rest.length > 0) layers.push(rest);
        break;
      }
      queue = nextQ;
    }
    // card size
    const cw = 184, ch = 80, sx = 270, sy = 140, padL = 90, padT = 80;
    const positions: Array<{ id: string; x: number; y: number; node: any }> = [];
    layers.forEach((layer, li) => {
      layer.forEach((nodeId, ni) => {
        positions.push({ id: nodeId, x: padL + li * sx, y: padT + ni * sy, node: nodeMap.get(nodeId) });
      });
    });
    return { positions, layers, adj, cw, ch, padL, padT };
  }

  const layout = computeLayout();
  const { cw = 184, ch = 80 } = layout;
  const maxN = Math.max(...layout.layers.map((l: string[]) => l.length), 1);
  const svgW = Math.max(maxN * 270 + 180, 700);
  const svgH = Math.max(layout.layers.length * 140 + 160, 500);

  function txt(s: string, max: number) {
    if (!s) return ''; return s.length > max ? s.slice(0, max - 1) + '…' : s;
  }

  // 选中的节点详情
  const selectedNode = selectedNodeId ? (pipeline.nodes || []).find(n => n.id === selectedNodeId) : null;

  // 推导 edges：优先使用显式 edges，否则从分支/goto 推断
  function deriveEdges(): PipelineEdge[] {
    if (pipeline.edges && pipeline.edges.length > 0) return pipeline.edges;
    const result: PipelineEdge[] = [];
    const nodeList = pipeline.nodes || [];
    for (let i = 0; i < nodeList.length - 1; i++) {
      const n = nodeList[i];
      if (n.type === 'decision' && n.branches) {
        n.branches.forEach((b: any) => {
          result.push({ source: n.id, target: b.goto || b.target, label: b.label || b.when || b.condition });
        });
      } else if (n.type === 'confirm' && n.confirm_branches) {
        Object.entries(n.confirm_branches).forEach(([k, v]) => {
          result.push({ source: n.id, target: v as string, label: k });
        });
      } else if (n.type === 'parallel' && n.parallel_branches) {
        n.parallel_branches.forEach((b: any) => {
          result.push({ source: n.id, target: b.node_id || b, label: 'parallel' });
        });
      } else {
        result.push({ source: n.id, target: nodeList[i + 1].id });
      }
    }
    return result;
  }

  const displayEdges = deriveEdges();

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-5 space-y-4 relative overflow-hidden">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-slate-900 dark:text-slate-100">{pipeline.name}</h2>
            <span className={`text-xs px-2 py-0.5 rounded ${pipeline.source === 'builtin' ? 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400' : 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400'}`}>
              {pipeline.source === 'builtin' ? '预置模板' : '自定义流水线'}
            </span>
            <span className={`text-xs px-2 py-0.5 rounded ${pipeline.type === 'auto' ? 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400' : 'bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400'}`}>
              {pipeline.type === 'auto' ? '自动化' : '人工介入'}
            </span>
          </div>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{pipeline.description || '无描述'}</p>
        </div>
        <div className="flex gap-2">
          {isCustom && (<button onClick={onEdit} className="px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300 hover:bg-slate-200">编辑</button>)}
          <button onClick={onTest} disabled={submitting} className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">{submitting ? '提交中...' : '测试'}</button>
        </div>
      </div>

      {/* 流程图区域 */}
      {layout.positions.length > 0 ? (
        <div>
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1">
              <button onClick={() => setViewMode('graph')} className={`text-[10px] px-2 py-0.5 rounded ${viewMode === 'graph' ? 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400' : 'text-slate-400 hover:text-slate-600'}`}>流程图</button>
              <button onClick={() => setViewMode('list')} className={`text-[10px] px-2 py-0.5 rounded ${viewMode === 'list' ? 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400' : 'text-slate-400 hover:text-slate-600'}`}>列表</button>
            </div>
            <span className="text-[10px] text-slate-400">{pipeline.nodes!.length} 节点, {displayEdges.length} 连线</span>
          </div>

          {viewMode === 'graph' ? (
            <div className="border border-slate-200 dark:border-slate-700 rounded-xl bg-gradient-to-br from-slate-50 to-slate-100 dark:from-slate-900/40 dark:to-slate-800/40 overflow-auto" style={{ maxHeight: '560px' }}>
              <svg width={svgW} height={svgH} className={`block select-none ${isDragging ? 'cursor-grabbing' : 'cursor-grab'}`}
                onMouseDown={handlePanDown} onMouseMove={handlePanMove} onMouseUp={handlePanUp} onMouseLeave={handlePanUp}>
                <defs>
                  {/* 阴影滤镜 */}
                  <filter id="cardShadow" x="-20%" y="-20%" width="140%" height="140%">
                    <feDropShadow dx="0" dy="2" stdDeviation="3" floodColor="#0f172a" floodOpacity="0.08" />
                  </filter>
                  <filter id="cardShadowSel" x="-20%" y="-20%" width="140%" height="140%">
                    <feDropShadow dx="0" dy="3" stdDeviation="6" floodColor="#3b82f6" floodOpacity="0.25" />
                  </filter>
                  <filter id="hoverGlow" x="-30%" y="-30%" width="160%" height="160%">
                    <feDropShadow dx="0" dy="4" stdDeviation="8" floodColor="#6366f1" floodOpacity="0.15" />
                  </filter>
                  {/* 箭头 */}
                  <marker id="arrowDark" viewBox="0 0 12 12" refX={10} refY={6} markerWidth={6} markerHeight={6} orient="auto-start-reverse">
                    <path d="M 0 2 L 8 6 L 0 10" fill="none" stroke="#64748b" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round"/>
                  </marker>
                  <marker id="arrowAmber" viewBox="0 0 12 12" refX={10} refY={6} markerWidth={6} markerHeight={6} orient="auto-start-reverse">
                    <path d="M 0 2 L 8 6 L 0 10" fill="none" stroke="#f59e0b" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round"/>
                  </marker>
                  <marker id="arrowGreen" viewBox="0 0 12 12" refX={10} refY={6} markerWidth={6} markerHeight={6} orient="auto-start-reverse">
                    <path d="M 0 2 L 8 6 L 0 10" fill="none" stroke="#10b981" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round"/>
                  </marker>
                  {/* 渐变色 */}
                  {Object.entries(NODE_GRADIENTS).map(([type, [from, to]]) => (
                    <linearGradient key={type} id={`grad-${type}`} x1="0%" y1="0%" x2="100%" y2="100%">
                      <stop offset="0%" stopColor={from} stopOpacity={0.95} />
                      <stop offset="100%" stopColor={to} stopOpacity={0.95} />
                    </linearGradient>
                  ))}
                  {/* 网格背景 */}
                  <pattern id="dotGrid" width={24} height={24} patternUnits="userSpaceOnUse">
                    <circle cx={12} cy={12} r={1} fill="#cbd5e1" opacity="0.4" />
                  </pattern>
                </defs>
                {/* 网格背景 */}
                <rect x={0} y={0} width={svgW} height={svgH} fill="url(#dotGrid)" />
                <g transform={`translate(${panOffset.x}, ${panOffset.y})`}>
                  {/* 贝塞尔曲线边 */}
                  {displayEdges.map((e, ei) => {
                    const sp = layout.positions.find(p => p.id === e.source);
                    const tp = layout.positions.find(p => p.id === e.target);
                    if (!sp || !tp) return null;
                    const sn = sp.node || {};
                    const isDecision = sn.type === 'decision';
                    const isDefault = e.label === 'default';
                    const strokeColor = isDecision ? (isDefault ? '#94a3b8' : '#f59e0b') : '#64748b';
                    const midY = (sp.y + tp.y) / 2;
                    // 贝塞尔曲线：从源节点底部出，用两个控制点画出平滑弯曲路径
                    const x1 = sp.x, y1 = sp.y + ch / 2;
                    const x2 = tp.x, y2 = tp.y - ch / 2;
                    const dx = Math.abs(x2 - x1) * 0.4;
                    const d = `M ${x1} ${y1} C ${x1} ${y1 + dx}, ${x2} ${y2 - dx}, ${x2} ${y2}`;
                    const marker = isDecision ? (isDefault ? 'url(#arrowDark)' : 'url(#arrowAmber)') : 'url(#arrowDark)';
                    return (
                      <g key={`${e.source}-${e.target}-${ei}`} data-edge="true">
                        {/* 边缘阴影 */}
                        <path d={d} fill="none" stroke="#94a3b8" strokeWidth={5} opacity="0.08" />
                        {/* 主路径 */}
                        <path d={d} fill="none" stroke={strokeColor} strokeWidth={2}
                          strokeDasharray={isDecision && !isDefault ? '6,3' : undefined}
                          strokeLinecap="round" markerEnd={marker} />
                        {/* 标签 */}
                        {e.label && (
                          <rect x={(x1 + x2) / 2 - 36} y={midY - 13} width={72} height={16} rx={8} fill="white" stroke="#e2e8f0" strokeWidth={0.5} />
                        )}
                        {e.label && (
                          <text x={(x1 + x2) / 2} y={midY - 2} textAnchor="middle" fill={strokeColor} fontSize={10} fontWeight={600}>{txt(e.label, 10)}</text>
                        )}
                      </g>
                    );
                  })}
                  {/* 节点卡片 */}
                  {layout.positions.map((pos: any) => {
                    const node = pos.node || {};
                    const color = NODE_COLORS[node.type] || '#94a3b8';
                    const typeLabel = NODE_LABELS[node.type] || node.type;
                    const icon = ICONS[node.type] || ICONS.agent;
                    const isSelected = selectedNodeId === pos.id;
                    return (
                      <g key={pos.id} data-node="true" transform={`translate(${pos.x}, ${pos.y})`}
                        onClick={() => setSelectedNodeId(selectedNodeId === pos.id ? null : pos.id)}
                        style={{ cursor: 'pointer' }}>
                        {/* 选中时外圈光晕 */}
                        {isSelected && <rect x={-cw / 2 - 3} y={-ch / 2 - 3} width={cw + 6} height={ch + 6} rx={14} fill="none" stroke={color} strokeWidth={2.5} opacity={0.5} />}
                        {/* 卡片主体 */}
                        <rect x={-cw / 2} y={-ch / 2} width={cw} height={ch} rx={12} fill="white"
                          filter={isSelected ? 'url(#cardShadowSel)' : 'url(#cardShadow)'}
                          stroke={isSelected ? color : '#e2e8f0'} strokeWidth={isSelected ? 2 : 1} />
                        {/* 顶部颜色条 */}
                        <rect x={-cw / 2} y={-ch / 2} width={cw} height={32} rx={12} fill={`url(#grad-${node.type || 'agent'})`} />
                        <rect x={-cw / 2} y={-ch / 2 + 24} width={cw} height={8} fill={`url(#grad-${node.type || 'agent'})`} />
                        {/* 图标 */}
                        <svg x={-cw / 2 + 12} y={-ch / 2 + 10} width={14} height={14} viewBox="0 0 24 24" style={{ color: 'white' }}>
                          <path d={icon} fill="currentColor" />
                        </svg>
                        {/* 标题 */}
                        <text x={-cw / 2 + 32} y={-ch / 2 + 20} fill="white" fontSize={12} fontWeight={700} fontFamily="system-ui, sans-serif">
                          {txt(node.display_name || node.id || pos.id, 18)}
                        </text>
                        {/* 类型标签 */}
                        <rect x={-cw / 2 + 10} y={-ch / 2 + 40} width={46} height={16} rx={4} fill={color + '15'} />
                        <text x={-cw / 2 + 33} y={-ch / 2 + 51} textAnchor="middle" fill={color} fontSize={9} fontWeight={600}>
                          {typeLabel}
                        </text>
                        {/* 智能体名称 */}
                        {node.agent && (
                          <text x={cw / 2 - 10} y={-ch / 2 + 52} textAnchor="end" fill="#94a3b8" fontSize={9} fontFamily="monospace">
                            @{txt(node.agent, 16)}
                          </text>
                        )}
                        {/* 输入端口 */}
                        <circle cx={0} cy={-ch / 2} r={4} fill="white" stroke="#e2e8f0" strokeWidth={1.5} />
                        <circle cx={0} cy={-ch / 2} r={2.5} fill={isSelected ? color : '#cbd5e1'} />
                        {/* 输出端口 */}
                        {!(node.type === 'decision' || node.type === 'confirm' || node.type === 'parallel') && (
                          <>
                            <circle cx={0} cy={ch / 2} r={4} fill="white" stroke="#e2e8f0" strokeWidth={1.5} />
                            <circle cx={0} cy={ch / 2} r={2.5} fill={isSelected ? color : '#cbd5e1'} />
                          </>
                        )}
                        {/* 决策节点多个输出端口 */}
                        {node.type === 'decision' && (
                          <>
                            <circle cx={-cw / 3} cy={ch / 2} r={4} fill="white" stroke="#e2e8f0" strokeWidth={1.5} />
                            <circle cx={-cw / 3} cy={ch / 2} r={2.5} fill="#f59e0b" />
                            <circle cx={cw / 3} cy={ch / 2} r={4} fill="white" stroke="#e2e8f0" strokeWidth={1.5} />
                            <circle cx={cw / 3} cy={ch / 2} r={2.5} fill="#f59e0b" />
                          </>
                        )}
                        {/* subpipeline 层级徽章 */}
                        {node.type === 'subpipeline' && (
                          <circle cx={cw / 2 - 14} cy={-ch / 2 + 16} r={8} fill="white" opacity={0.25} />
                        )}
                      </g>
                    );
                  })}
                </g>
              </svg>
            </div>
          ) : (
            <div className="space-y-2">
              {pipeline.nodes!.map((node: any, idx: number) => (
                <div key={node.id || idx} onClick={() => setSelectedNodeId(selectedNodeId === node.id ? null : node.id)}
                  className={`flex items-center gap-3 p-2.5 rounded-lg border cursor-pointer transition-colors ${selectedNodeId === node.id ? 'border-blue-400 bg-blue-50 dark:border-blue-500 dark:bg-blue-900/20' : 'border-slate-100 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/50 hover:border-slate-200'}`}>
                  <div className="w-7 h-7 rounded flex items-center justify-center shrink-0" style={{ backgroundColor: (NODE_COLORS[node.type] || '#94a3b8') + '20' }}>
                    <svg viewBox="0 0 24 24" className="w-4 h-4" style={{ color: NODE_COLORS[node.type] || '#94a3b8' }}><path d={ICONS[node.type] || ICONS.agent} fill="currentColor" /></svg>
                  </div>
                  <div className="flex-1 min-w-0"><div className="text-sm font-medium text-slate-700 dark:text-slate-300">{node.display_name || node.id}</div><div className="text-[10px] text-slate-400">{NODE_LABELS[node.type] || node.type}</div></div>
                  {node.agent && <span className="text-[10px] px-1.5 py-0.5 bg-blue-50 text-blue-500 rounded dark:bg-blue-900/20">{node.agent}</span>}
                </div>
              ))}
            </div>
          )}

          {/* 图例 */}
          <div className="flex items-center gap-3 mt-2 flex-wrap">
            {Object.entries(NODE_LABELS).map(([type, label]) => (
              <div key={type} className="flex items-center gap-1 text-[10px] text-slate-400"><div className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: NODE_COLORS[type] || '#94a3b8' }} />{label}</div>
            ))}
            <div className="flex-1" />
            <div className="flex items-center gap-1 text-[10px] text-slate-400">
              <svg className="w-5 h-3"><line x1={0} y1={6} x2={18} y2={6} stroke="#f59e0b" strokeWidth={1.5} strokeDasharray="3,2"/></svg>条件分支
              <svg className="w-5 h-3 ml-1"><line x1={0} y1={6} x2={18} y2={6} stroke="#475569" strokeWidth={1.5}/></svg>顺序
            </div>
          </div>
        </div>
      ) : (
        <div className="text-sm text-slate-400 dark:text-slate-500">流水线为空（无节点定义）</div>
      )}

      {/* 节点详情侧边栏 */}
      {selectedNode && (
        <div className="absolute top-0 right-0 h-full w-80 bg-white dark:bg-slate-800 border-l border-slate-200 dark:border-slate-700 shadow-xl z-10 overflow-y-auto animate-slide-in">
          <NodeDetailSidebar node={selectedNode} onClose={() => setSelectedNodeId(null)} />
        </div>
      )}
    </div>
  );
}

// 节点详情侧边栏
function NodeDetailSidebar({ node, onClose }: { node: any; onClose: () => void }) {
  const color = NODE_COLORS[node.type] || '#94a3b8';
  const typeLabel = NODE_LABELS[node.type] || node.type;

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center justify-between sticky top-0 bg-white dark:bg-slate-800 pb-2 border-b border-slate-100 dark:border-slate-700">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded flex items-center justify-center" style={{ backgroundColor: color + '20' }}>
            <svg viewBox="0 0 24 24" className="w-4 h-4" style={{ color }}><path d={ICONS[node.type] || ICONS.agent} fill="currentColor" /></svg>
          </div>
          <div>
            <div className="text-sm font-bold text-slate-900 dark:text-slate-100">{node.id}</div>
            <div className="text-[10px] px-1.5 py-0.5 rounded-full inline-block mt-0.5" style={{ backgroundColor: color + '20', color }}>{typeLabel}</div>
          </div>
        </div>
        <button onClick={onClose} className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-400"><svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" d="M6 6l12 12M18 6L6 18"/></svg></button>
      </div>

      <Field label="显示名称" value={node.display_name} />
      <Field label="描述" value={node.description} />

      {node.type === 'agent' && (
        <>
          <Field label="绑定智能体" value={node.agent} mono />
          <Field label="Prompt 模板" value={node.prompt_template} long />
          {node.tools && Array.isArray(node.tools) && node.tools.length > 0 && (
            <div>
              <div className="text-[10px] font-semibold text-slate-400 mb-1 uppercase">绑定工具</div>
              <div className="flex flex-wrap gap-1">{node.tools.map((t: string) => <span key={t} className="text-[10px] px-1.5 py-0.5 bg-slate-100 dark:bg-slate-700 rounded text-slate-600 dark:text-slate-300">{t}</span>)}</div>
            </div>
          )}
          <Field label="超时时间" value={node.timeout_seconds != null ? `${node.timeout_seconds}s` : undefined} />
          <Field label="最大重试" value={node.max_retries != null ? String(node.max_retries) : undefined} />
        </>
      )}

      {node.type === 'decision' && node.branches && (
        <div>
          <div className="text-[10px] font-semibold text-slate-400 mb-1.5 uppercase">分支条件</div>
          <div className="space-y-1.5">
            {node.branches.map((b: any, i: number) => (
              <div key={i} className="p-2 rounded-lg bg-amber-50 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span className="text-[10px] px-1 py-0.5 bg-amber-100 dark:bg-amber-900/30 rounded text-amber-700 dark:text-amber-400 font-medium">分支 {i + 1}</span>
                  {b.default && <span className="text-[9px] text-slate-400">(默认)</span>}
                </div>
                <div className="text-[10px] text-slate-600 dark:text-slate-400">条件: <span className="font-mono">{b.condition || b.when || b.label || 'else'}</span></div>
                <div className="text-[10px] text-slate-500">跳转: <span className="font-mono text-blue-500">{b.goto || b.target}</span></div>
              </div>
            ))}
          </div>
        </div>
      )}

      {node.type === 'parallel' && (
        <>
          <Field label="最大并发" value={node.max_concurrency != null ? String(node.max_concurrency) : undefined} />
          <Field label="合并策略" value={node.merge_strategy} />
          {node.parallel_branches && (
            <div>
              <div className="text-[10px] font-semibold text-slate-400 mb-1 uppercase">并行分支 ({node.parallel_branches.length})</div>
              <div className="space-y-1">{node.parallel_branches.map((b: any, i: number) => (
                <div key={i} className="text-[10px] font-mono px-2 py-1 bg-purple-50 dark:bg-purple-900/10 rounded text-purple-600 dark:text-purple-400">
                  → {typeof b === 'string' ? b : b.node_id}
                </div>
              ))}</div>
            </div>
          )}
        </>
      )}

      {node.type === 'confirm' && (
        <>
          <Field label="确认提示" value={node.confirm_prompt} long />
          {node.confirm_options && (
            <div>
              <div className="text-[10px] font-semibold text-slate-400 mb-1 uppercase">确认选项</div>
              <div className="flex flex-wrap gap-1">{node.confirm_options.map((o: string) => <span key={o} className="text-[10px] px-2 py-1 bg-pink-50 dark:bg-pink-900/10 rounded text-pink-600 dark:text-pink-400">{o}</span>)}</div>
            </div>
          )}
          {node.confirm_branches && (
            <div>
              <div className="text-[10px] font-semibold text-slate-400 mb-1 uppercase">选项分支</div>
              <div className="space-y-0.5">{Object.entries(node.confirm_branches).map(([k, v]) => (
                <div key={k} className="text-[10px] flex justify-between"><span className="text-slate-500">{k}</span><span className="font-mono text-blue-500">→ {v as string}</span></div>
              ))}</div>
            </div>
          )}
        </>
      )}

      {node.type === 'subpipeline' && (
        <Field label="子流水线名称" value={node.pipeline_name} mono />
      )}

      {node.type === 'transform' && (
        <Field label="转换表达式" value={node.transform_expr} mono long />
      )}

      {/* 通用字段 */}
      <div className="pt-2 border-t border-slate-100 dark:border-slate-700">
        <div className="text-[10px] font-semibold text-slate-400 mb-1 uppercase">节点ID</div>
        <div className="text-xs font-mono text-slate-500 dark:text-slate-400">{node.id}</div>
      </div>
    </div>
  );
}

function Field({ label, value, mono, long }: { label: string; value?: string; mono?: boolean; long?: boolean }) {
  if (!value) return null;
  return (
    <div>
      <div className="text-[10px] font-semibold text-slate-400 mb-1 uppercase">{label}</div>
      <div className={`${long ? 'text-xs' : 'text-xs'} ${mono ? 'font-mono' : ''} text-slate-700 dark:text-slate-300 ${long ? 'whitespace-pre-wrap max-h-32 overflow-y-auto' : 'break-all'}`}>{value}</div>
    </div>
  );
}

function PipelineEditPanel({ pipeline, editData, setEditData, onSave, onCancel }: {
  pipeline: UnifiedPipeline;
  editData: Partial<UnifiedPipeline>;
  setEditData: (d: Partial<UnifiedPipeline>) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-5 space-y-4">
      <h2 className="text-lg font-bold text-slate-900 dark:text-slate-100">编辑: {pipeline.name}</h2>

      <label className="block">
        <span className="text-xs font-medium text-slate-500">名称</span>
        <input
          type="text"
          value={editData.name || ''}
          onChange={e => setEditData({ ...editData, name: e.target.value })}
          className="mt-1 w-full px-3 py-2 text-sm border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100"
        />
      </label>

      <label className="block">
        <span className="text-xs font-medium text-slate-500">描述</span>
        <textarea
          value={editData.description || ''}
          onChange={e => setEditData({ ...editData, description: e.target.value })}
          rows={3}
          className="mt-1 w-full px-3 py-2 text-sm border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 resize-none"
        />
      </label>

      <label className="block">
        <span className="text-xs font-medium text-slate-500">节点定义 (JSON)</span>
        <textarea
          value={JSON.stringify(editData.nodes || [], null, 2)}
          onChange={e => {
            try {
              const parsed = JSON.parse(e.target.value);
              setEditData({ ...editData, nodes: parsed });
            } catch { /* invalid JSON, ignore */ }
          }}
          rows={10}
          className="mt-1 w-full px-3 py-2 text-xs font-mono border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 resize-none"
        />
      </label>

      <label className="block">
        <span className="text-xs font-medium text-slate-500">边定义 (JSON)</span>
        <textarea
          value={JSON.stringify(editData.edges || [], null, 2)}
          onChange={e => {
            try {
              const parsed = JSON.parse(e.target.value);
              setEditData({ ...editData, edges: parsed });
            } catch { /* ignore */ }
          }}
          rows={5}
          className="mt-1 w-full px-3 py-2 text-xs font-mono border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 resize-none"
        />
      </label>

      <div className="flex gap-2 pt-2">
        <button onClick={onSave} className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700">
          保存
        </button>
        <button onClick={onCancel} className="px-4 py-2 bg-slate-200 dark:bg-slate-700 text-sm rounded-lg">
          取消
        </button>
      </div>
    </div>
  );
}

/* ================================================================
   CreatePipelineModal — 拖拽流程图编辑器
   ================================================================ */
interface CanvasNode {
  id: string;
  type: string;
  display_name: string;
  agent?: string;
  prompt_template?: string;
  description?: string;
  tools?: string[];
  branches?: Array<{ condition?: string; goto: string; label?: string; default?: boolean }>;
  parallel_branches?: string[];
  confirm_prompt?: string;
  confirm_options?: string[];
  pipeline_name?: string;
  transform_expr?: string;
  timeout_seconds?: number;
  max_retries?: number;
  merge_strategy?: string;
  max_concurrency?: number;
  x: number;
  y: number;
}

interface CanvasEdge {
  source: string;
  target: string;
  label?: string;
}

const NCW = 184, NCH = 80;

function CreatePipelineModal({ onClose, onCreated, showFeedback }: {
  onClose: () => void;
  onCreated: (name: string) => void;
  showFeedback: (type: 'ok' | 'err', msg: string) => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [pipelineType, setPipelineType] = useState<'manual' | 'auto'>('manual');
  const [nodes, setNodes] = useState<CanvasNode[]>([]);
  const [edges, setEdges] = useState<CanvasEdge[]>([]);
  const [agents, setAgents] = useState<AgentMetadata[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [connectStart, setConnectStart] = useState<string | null>(null);
  const [dragging, setDragging] = useState<{ id: string; sx: number; sy: number } | null>(null);
  const [creating, setCreating] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);

  // 加载已有智能体列表
  useEffect(() => {
    api.listAgents().then(r => setAgents(r.agents)).catch(() => {});
  }, []);

  const selectedNode = selectedNodeId ? nodes.find(n => n.id === selectedNodeId) : null;

  function addNode(nodeType: string) {
    const idx = nodes.length + 1;
    setNodes(prev => [...prev, {
      id: `node_${idx}`, type: nodeType, display_name: '',
      x: 180 + Math.random() * 300, y: 100 + Math.random() * 350,
    }]);
  }

  function updateNode(updates: Partial<CanvasNode>) {
    setNodes(prev => prev.map(n => n.id === selectedNodeId ? { ...n, ...updates } : n));
  }

  function deleteNode() {
    if (!selectedNodeId) return;
    setNodes(prev => prev.filter(n => n.id !== selectedNodeId));
    setEdges(prev => prev.filter(e => e.source !== selectedNodeId && e.target !== selectedNodeId));
    setSelectedNodeId(null);
  }

  function svgCoords(e: React.MouseEvent): { x: number; y: number } {
    const svg = svgRef.current;
    if (!svg) return { x: e.clientX, y: e.clientY };
    const pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return { x: e.clientX, y: e.clientY };
    const sp = pt.matrixTransform(ctm.inverse());
    return { x: sp.x, y: sp.y };
  }

  function handleNodeMouseDown(e: React.MouseEvent, nodeId: string) {
    e.stopPropagation();
    if (e.button !== 0) return;
    const node = nodes.find(n => n.id === nodeId);
    if (!node) return;
    const coords = svgCoords(e);
    setDragging({ id: nodeId, sx: coords.x - node.x, sy: coords.y - node.y });
    setSelectedNodeId(nodeId);
  }

  function handlePortClick(e: React.MouseEvent, nodeId: string, port: 'out' | 'in') {
    e.stopPropagation();
    if (port === 'out') {
      setConnectStart(nodeId);
    } else if (port === 'in' && connectStart && connectStart !== nodeId) {
      if (!edges.some(ed => ed.source === connectStart && ed.target === nodeId)) {
        setEdges(prev => [...prev, { source: connectStart, target: nodeId }]);
      }
      setConnectStart(null);
    }
  }

  function handleSvgMouseMove(e: React.MouseEvent) {
    if (dragging) {
      const coords = svgCoords(e);
      setNodes(prev => prev.map(n =>
        n.id === dragging.id ? { ...n, x: coords.x - dragging.sx, y: coords.y - dragging.sy } : n
      ));
    }
  }
  function handleSvgMouseUp() { setDragging(null); }

  async function handleSave() {
    if (!name.trim()) { showFeedback('err', '请填写流水线名称'); return; }
    setCreating(true);
    try {
      const payload = {
        name: name.trim(),
        description: description || undefined,
        type: pipelineType,
        nodes: nodes.map(n => { const { x, y, ...rest } = n; return rest; }),
        edges: edges.map(e => ({ source: e.source, target: e.target, label: e.label || undefined })),
        steps: nodes.map(n => ({ agent_name: n.agent || n.id, display_name: n.display_name || n.id, description: n.type || 'agent' })),
      };
      await (api as any).createCustomPipeline(payload);
      onCreated(name.trim());
    } catch (e: any) { showFeedback('err', '创建失败: ' + (e.message || '')); }
    finally { setCreating(false); }
  }

  const [mousePos, setMousePos] = useState<{ x: number; y: number } | null>(null);

  return (
    <div className="fixed inset-0 z-50 flex bg-black/60" onClick={onClose}>
      <div className="w-full h-full max-w-[95vw] max-h-[90vh] m-auto bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl flex flex-col overflow-hidden" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800 shrink-0">
          <div className="flex items-center gap-4">
            <h2 className="text-base font-semibold text-slate-100">新建流水线（拖拽编排）</h2>
            <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="流水线名称 *"
              className="w-48 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500" />
            <input type="text" value={description} onChange={e => setDescription(e.target.value)} placeholder="描述（可选）"
              className="w-40 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500" />
            <div className="flex gap-1">
              {(['manual', 'auto'] as const).map(t => (
                <button key={t} onClick={() => setPipelineType(t)}
                  className={`px-2.5 py-1 text-[11px] rounded ${pipelineType === t ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 border border-gray-700'}`}>
                  {t === 'auto' ? '自动化' : '人工介入'}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-gray-500">{nodes.length} 节点, {edges.length} 连线</span>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 flex overflow-hidden">
          {/* Left palette */}
          <div className="w-40 shrink-0 border-r border-gray-800 p-3 space-y-1.5 overflow-y-auto bg-gray-900/50">
            <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-2 px-1">拖拽节点</div>
            {Object.entries(NODE_LABELS).map(([k, v]) => (
              <button key={k} onClick={() => addNode(k)}
                className="w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-left text-xs font-medium transition-all duration-150 hover:bg-gray-800 border border-gray-800 hover:border-gray-600 hover:shadow-sm"
                style={{ color: NODE_COLORS[k] || '#94a3b8' }}>
                <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0" style={{ backgroundColor: (NODE_COLORS[k] || '#94a3b8') + '20' }}>
                  <svg viewBox="0 0 24 24" className="w-4 h-4" style={{ color: NODE_COLORS[k] || '#94a3b8' }}>
                    <path d={ICONS[k] || ICONS.agent} fill="currentColor" />
                  </svg>
                </div>
                <span className="text-gray-300">{v}</span>
              </button>
            ))}
            <div className="pt-3 mt-2 border-t border-gray-800">
              <p className="text-[10px] text-gray-600 leading-relaxed">点击节点类型添加 → 拖拽移动节点 → 点击端口连线 → 右侧编辑配置</p>
            </div>
          </div>

          {/* Center canvas */}
          <div className="flex-1 flex flex-col overflow-hidden">
            <div className="relative flex-1 bg-gray-950 border-b border-gray-800"
              onMouseMove={(e) => { handleSvgMouseMove(e); if (connectStart) setMousePos(svgCoords(e)); }}
              onMouseUp={handleSvgMouseUp} onMouseLeave={() => { setDragging(null); }}>
              <svg ref={svgRef} width="100%" height="100%" style={{ display: 'block' }}>
                <defs>
                  {/* 阴影 */}
                  <filter id="cvShadow" x="-20%" y="-20%" width="140%" height="140%">
                    <feDropShadow dx="0" dy="2" stdDeviation="4" floodColor="#000" floodOpacity="0.3" />
                  </filter>
                  <filter id="cvShadowSel" x="-20%" y="-20%" width="140%" height="140%">
                    <feDropShadow dx="0" dy="3" stdDeviation="8" floodColor="#3b82f6" floodOpacity="0.35" />
                  </filter>
                  {/* 箭头 */}
                  <marker id="cvArrow" viewBox="0 0 12 12" refX={10} refY={6} markerWidth={6} markerHeight={6} orient="auto-start-reverse">
                    <path d="M 0 2 L 8 6 L 0 10" fill="none" stroke="#64748b" strokeWidth={1.8} strokeLinecap="round"/>
                  </marker>
                  <marker id="cvArrowBlue" viewBox="0 0 12 12" refX={10} refY={6} markerWidth={6} markerHeight={6} orient="auto-start-reverse">
                    <path d="M 0 2 L 8 6 L 0 10" fill="none" stroke="#3b82f6" strokeWidth={1.8} strokeLinecap="round"/>
                  </marker>
                  {/* 渐变 */}
                  {Object.entries(NODE_GRADIENTS).map(([type, [from, to]]) => (
                    <linearGradient key={type} id={`cv-grad-${type}`} x1="0%" y1="0%" x2="100%" y2="100%">
                      <stop offset="0%" stopColor={from} stopOpacity={0.9} />
                      <stop offset="100%" stopColor={to} stopOpacity={0.9} />
                    </linearGradient>
                  ))}
                  {/* 网格 */}
                  <pattern id="cvGrid" width={28} height={28} patternUnits="userSpaceOnUse">
                    <circle cx={14} cy={14} r={0.8} fill="#334155" opacity="0.3" />
                  </pattern>
                </defs>
                {/* 网格背景 */}
                <rect width="100%" height="100%" fill="url(#cvGrid)" />
                {/* 边：贝塞尔曲线 */}
                {edges.map((e, i) => {
                  const sn = nodes.find(n => n.id === e.source);
                  const tn = nodes.find(n => n.id === e.target);
                  if (!sn || !tn) return null;
                  const x1 = sn.x, y1 = sn.y + NCH / 2;
                  const x2 = tn.x, y2 = tn.y - NCH / 2;
                  const dx = Math.abs(x2 - x1) * 0.4;
                  const d = `M ${x1} ${y1} C ${x1} ${y1 + dx}, ${x2} ${y2 - dx}, ${x2} ${y2}`;
                  return (
                    <g key={`edge-${i}`}>
                      <path d={d} fill="none" stroke="#334155" strokeWidth={5} opacity="0.3" />
                      <path d={d} fill="none" stroke="#64748b" strokeWidth={2} strokeLinecap="round" markerEnd="url(#cvArrow)" />
                      {e.label && (
                        <>
                          <rect x={(x1 + x2) / 2 - 30} y={(sn.y + tn.y) / 2 - 13} width={60} height={16} rx={8} fill="#1e293b" stroke="#334155" strokeWidth={0.5} />
                          <text x={(x1 + x2) / 2} y={(sn.y + tn.y) / 2 - 2} textAnchor="middle" fill="#cbd5e1" fontSize={9} fontWeight={600}>{e.label}</text>
                        </>
                      )}
                    </g>
                  );
                })}
                {/* 临时连线 */}
                {connectStart && mousePos && (() => {
                  const sn = nodes.find(n => n.id === connectStart);
                  if (!sn) return null;
                  const x1 = sn.x, y1 = sn.y + NCH / 2;
                  const dx = Math.abs(mousePos.x - x1) * 0.4;
                  const d = `M ${x1} ${y1} C ${x1} ${y1 + dx}, ${mousePos.x} ${mousePos.y - dx}, ${mousePos.x} ${mousePos.y}`;
                  return <path d={d} fill="none" stroke="#3b82f6" strokeWidth={2} strokeDasharray="6,4" strokeLinecap="round" markerEnd="url(#cvArrowBlue)" />;
                })()}
                {/* 节点卡片 */}
                {nodes.map(node => {
                  const color = NODE_COLORS[node.type] || '#94a3b8';
                  const isSel = selectedNodeId === node.id;
                  const isConn = connectStart === node.id;
                  return (
                    <g key={node.id} onMouseDown={e => handleNodeMouseDown(e, node.id)} style={{ cursor: dragging ? 'grabbing' : 'grab' }}>
                      {/* 选中光晕 */}
                      {isSel && <rect x={node.x - NCW/2 - 3} y={node.y - NCH/2 - 3} width={NCW + 6} height={NCH + 6} rx={14} fill="none" stroke={color} strokeWidth={2} opacity={0.4} />}
                      {/* 卡片体 */}
                      <rect x={node.x - NCW/2} y={node.y - NCH/2} width={NCW} height={NCH} rx={12}
                        fill="#1e293b" filter={isSel ? 'url(#cvShadowSel)' : 'url(#cvShadow)'}
                        stroke={isSel ? color : isConn ? '#3b82f6' : '#334155'} strokeWidth={isSel || isConn ? 2 : 1} />
                      {/* 颜色头部条 */}
                      <rect x={node.x - NCW/2} y={node.y - NCH/2} width={NCW} height={34} rx={12} fill={`url(#cv-grad-${node.type || 'agent'})`} />
                      <rect x={node.x - NCW/2} y={node.y - NCH/2 + 26} width={NCW} height={8} fill={`url(#cv-grad-${node.type || 'agent'})`} />
                      {/* 图标 */}
                      <svg x={node.x - NCW/2 + 14} y={node.y - NCH/2 + 10} width={14} height={14} viewBox="0 0 24 24" style={{ color: 'white' }}>
                        <path d={ICONS[node.type] || ICONS.agent} fill="currentColor" />
                      </svg>
                      {/* 标题 */}
                      <text x={node.x - NCW/2 + 34} y={node.y - NCH/2 + 20} fill="white" fontSize={12} fontWeight={700} fontFamily="system-ui">
                        {(node.display_name || node.id).slice(0, 18)}
                      </text>
                      {/* 类型标签 */}
                      <rect x={node.x - NCW/2 + 12} y={node.y - NCH/2 + 44} width={44} height={16} rx={4} fill={color + '20'} />
                      <text x={node.x - NCW/2 + 34} y={node.y - NCH/2 + 55} textAnchor="middle" fill={color} fontSize={9} fontWeight={600}>
                        {NODE_LABELS[node.type] || node.type}
                      </text>
                      {/* 智能体名称 */}
                      {node.agent && (
                        <text x={node.x + NCW/2 - 14} y={node.y - NCH/2 + 56} textAnchor="end" fill="#94a3b8" fontSize={9} fontFamily="monospace">
                          @{node.agent.slice(0, 14)}
                        </text>
                      )}
                      {/* 输入端口 */}
                      <circle cx={node.x} cy={node.y - NCH/2} r={5} fill="#0f172a" stroke="#334155" strokeWidth={1.5}
                        style={{ cursor: 'crosshair' }} onClick={e => handlePortClick(e, node.id, 'in')} />
                      <circle cx={node.x} cy={node.y - NCH/2} r={3} fill={isSel ? color : '#475569'} />
                      {/* 输出端口 */}
                      <circle cx={node.x} cy={node.y + NCH/2} r={5} fill="#0f172a" stroke={isConn ? '#60a5fa' : '#334155'} strokeWidth={1.5}
                        style={{ cursor: 'crosshair' }} onClick={e => handlePortClick(e, node.id, 'out')} />
                      <circle cx={node.x} cy={node.y + NCH/2} r={3} fill={isConn ? '#3b82f6' : (isSel ? color : '#475569')} />
                    </g>
                  );
                })}
              </svg>
            </div>
            <div className="h-6 bg-gray-900/80 backdrop-blur border-t border-gray-800 flex items-center justify-between px-4">
              <span className="text-[10px] text-gray-600">拖拽移动节点 | 点击 ○ 端口连线 | 点击节点编辑配置</span>
              <span className="text-[10px] text-gray-600">{nodes.length} 节点 · {edges.length} 连线</span>
            </div>
          </div>

          {/* Right config panel */}
          <div className="w-64 shrink-0 border-l border-gray-800 p-3 overflow-y-auto space-y-3">
            {selectedNode ? (
              <>
                <div className="flex items-center justify-between">
                  <div className="text-xs font-semibold" style={{ color: NODE_COLORS[selectedNode.type] || '#94a3b8' }}>
                    {NODE_LABELS[selectedNode.type] || selectedNode.type} 配置
                  </div>
                  <button onClick={deleteNode} className="p-1 rounded text-red-400 hover:bg-red-500/10">
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
                  </button>
                </div>
                <NodeConfigForm node={selectedNode} onChange={updateNode} agents={agents} />
              </>
            ) : (
              <div className="text-[10px] text-gray-500 pt-8 text-center">点击画布节点<br/>编辑配置</div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-gray-800 shrink-0">
          <span className="text-[10px] text-gray-500">{nodes.length > 0 ? `${nodes.length} 个节点` : '请从左侧面板添加节点'}</span>
          <div className="flex items-center gap-3">
            <button onClick={onClose} className="px-4 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-sm text-gray-300">取消</button>
            <button onClick={handleSave} disabled={creating || !name.trim()}
              className="px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-800 disabled:text-gray-600 rounded-xl text-sm font-medium">
              {creating ? '创建中...' : '确认创建'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function NodeConfigForm({ node, onChange, agents }: { node: CanvasNode; onChange: (u: Partial<CanvasNode>) => void; agents: AgentMetadata[] }) {
  return (
    <div className="space-y-2.5">
      <label className="block">
        <span className="text-[10px] text-gray-500">节点ID</span>
        <input type="text" value={node.id} onChange={e => onChange({ id: e.target.value })}
          className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 font-mono outline-none focus:border-blue-500" />
      </label>
      <label className="block">
        <span className="text-[10px] text-gray-500">显示名称</span>
        <input type="text" value={node.display_name || ''} onChange={e => onChange({ display_name: e.target.value })}
          className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500" />
      </label>
      {node.type === 'agent' && (
        <>
          <label className="block">
            <span className="text-[10px] text-gray-500">绑定智能体</span>
            <select value={node.agent || ''} onChange={e => onChange({ agent: e.target.value })}
              className="w-full mt-0.5 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500">
              <option value="">-- 选择智能体 --</option>
              {agents.map(a => (
                <option key={a.id || a.name} value={a.id || a.name}>{a.name}{a.description ? ` — ${a.description.slice(0, 40)}` : ''}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-[10px] text-gray-500">Prompt 模板</span>
            <textarea value={node.prompt_template || ''} onChange={e => onChange({ prompt_template: e.target.value })}
              rows={3} className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500 resize-none" />
          </label>
        </>
      )}
      {node.type === 'decision' && (
        <div>
          <span className="text-[10px] text-gray-500">分支条件</span>
          <BranchesEditor node={node} onChange={onChange} />
        </div>
      )}
      {node.type === 'parallel' && (
        <label className="block">
          <span className="text-[10px] text-gray-500">并行目标ID（逗号分隔）</span>
          <input type="text" value={(node.parallel_branches || []).join(', ')}
            onChange={e => onChange({ parallel_branches: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
            className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500" />
        </label>
      )}
      {node.type === 'confirm' && (
        <>
          <label className="block">
            <span className="text-[10px] text-gray-500">确认提示</span>
            <textarea value={node.confirm_prompt || ''} onChange={e => onChange({ confirm_prompt: e.target.value })}
              rows={2} className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500 resize-none" />
          </label>
          <label className="block">
            <span className="text-[10px] text-gray-500">确认选项（逗号分隔）</span>
            <input type="text" value={(node.confirm_options || []).join(', ')}
              onChange={e => onChange({ confirm_options: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
              className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500" />
          </label>
        </>
      )}
      {node.type === 'subpipeline' && (
        <label className="block">
          <span className="text-[10px] text-gray-500">子流水线名称</span>
          <input type="text" value={node.pipeline_name || ''} onChange={e => onChange({ pipeline_name: e.target.value })}
            className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 font-mono outline-none focus:border-blue-500" />
        </label>
      )}
      {node.type === 'transform' && (
        <label className="block">
          <span className="text-[10px] text-gray-500">转换表达式</span>
          <textarea value={node.transform_expr || ''} onChange={e => onChange({ transform_expr: e.target.value })}
            rows={3} className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 font-mono outline-none focus:border-blue-500 resize-none" />
        </label>
      )}
      {node.type === 'receiver' && (
        <>
          <label className="block">
            <span className="text-[10px] text-gray-500">绑定接收器 ID</span>
            <input type="text" value={node.agent || ''} onChange={e => onChange({ agent: e.target.value })}
              className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 font-mono outline-none focus:border-blue-500"
              placeholder="在设置中创建接收器后填入 ID" />
          </label>
          <p className="text-[9px] text-gray-600">
            输入在「系统设置 → 数据接收器」中创建的接收器 ID。流水线运行时将从此接收器拉取数据。
          </p>
        </>
      )}
      {node.type === 'datatransformer' && (
        <>
          <label className="block">
            <span className="text-[10px] text-gray-500">转换器名称</span>
            <select value={node.agent || 'identity'} onChange={e => onChange({ agent: e.target.value })}
              className="w-full mt-0.5 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500">
              <option value="identity">直通 (identity)</option>
              <option value="html_to_text">HTML → 纯文本</option>
              <option value="extract_json">提取 JSON</option>
              <option value="syslog_parse">Syslog 解析</option>
              <option value="passthrough">透传</option>
            </select>
          </label>
          <label className="block">
            <span className="text-[10px] text-gray-500">Jinja2 模板（可选，覆盖内置转换器）</span>
            <textarea value={node.prompt_template || ''} onChange={e => onChange({ prompt_template: e.target.value })}
              rows={3} className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 font-mono outline-none focus:border-blue-500 resize-none"
              placeholder={'{\n  "summary": "{{ input | truncate(200) }}"\n}'} />
          </label>
        </>
      )}
    </div>
  );
}

function BranchesEditor({ node, onChange }: { node: CanvasNode; onChange: (u: Partial<CanvasNode>) => void }) {
  const branches = node.branches || [];
  function updateBranches(newB: typeof branches) { onChange({ branches: newB }); }
  function updateBranch(idx: number, field: string, val: string | boolean) {
    const nb = [...branches]; nb[idx] = { ...nb[idx], [field]: val }; updateBranches(nb);
  }
  return (
    <div className="space-y-1.5 mt-1">
      {branches.map((b, i) => (
        <div key={i} className="p-2 rounded bg-gray-800/50 space-y-1">
          <input type="text" value={b.condition || b.label || ''} onChange={e => updateBranch(i, 'condition', e.target.value)}
            placeholder="条件 (如 status==ok)" className="w-full px-2 py-0.5 bg-gray-700 border border-gray-600 rounded text-[10px] text-gray-200 font-mono outline-none" />
          <div className="flex gap-1">
            <input type="text" value={b.goto || ''} onChange={e => updateBranch(i, 'goto', e.target.value)}
              placeholder="跳转目标节点ID" className="flex-1 px-2 py-0.5 bg-gray-700 border border-gray-600 rounded text-[10px] text-gray-200 font-mono outline-none" />
            <label className="flex items-center gap-0.5 text-[9px] text-gray-400">
              <input type="checkbox" checked={!!b.default} onChange={e => updateBranch(i, 'default', e.target.checked)} /> 默认
            </label>
          </div>
        </div>
      ))}
      <button onClick={() => updateBranches([...branches, { condition: '', goto: '', default: false }])}
        className="w-full text-[10px] py-1 bg-gray-800 border border-gray-700 rounded text-gray-400 hover:text-gray-300">+ 添加分支</button>
    </div>
  );
}


