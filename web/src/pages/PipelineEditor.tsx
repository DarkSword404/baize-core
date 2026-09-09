/**
 * 流水线管理页面
 * 正式两级模型：模板（图编排定义，内置 + 自定义）→ 流水线实例（可启用/停用、
 * 绑定接收器、设置并行上限）。实例在调度器内并行消费接收器入站数据，
 * 每次入站 = 一次独立 run/对话；对话保存在该 run 内，不进入对话/会话管理。
 * 数据接收器从设置页迁移至此页管理。
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from '../api/client';
import type { AgentMetadata } from '../types';
import type {
  PipelineInstance,
  InstanceStatus,
  ReceiverConfig,
  RunDetail,
  RunBrief,
} from '../api/client';

type PipelineSource = 'builtin' | 'custom';

interface PipelineNode {
  id: string;
  type: string;
  display_name?: string;
  description?: string;
  agent?: string;
  prompt_template?: string;
  branches?: Array<{ when?: string; condition?: string; goto?: string; target?: string; label?: string; default?: boolean }>;
  parallel_branches?: Array<{ node_id: string; node?: unknown } | string>;
  confirm_prompt?: string;
  confirm_options?: string[];
  confirm_branches?: Record<string, string>;
  // 结束对话节点：true = 对话归档保留，false = 运行完成后回收
  save_dialog?: boolean;
  // ---- 图编排/SOAR 增强字段 ----
  target?: string;              // 显式下一节点
  merge_strategy?: string;      // parallel 合并策略
  decision_prompt?: string;     // ai_decision: LLM 决策提示
  decision_model?: string;      // ai_decision: 覆盖模型
  decision_expression?: string; // decision: 简化表达式
  transform_expr?: string;
  pipeline_name?: string;
  tools?: string[];
  timeout_seconds?: number;     // 节点执行超时
  max_retries?: number;         // 失败重试次数
  error_target?: string;        // 失败分支路由目标
  on_error?: string;            // 兼容别名
  ignore_error?: boolean;       // 失败仅记录、继续走正常路径
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
  category?: string;
  tags?: string[];
  timeout_seconds?: number;
  max_concurrency?: number;
  created_at?: string;
  updated_at?: string;
}

// 历史/实例运行行（列表接口返回，可含对话统计）
interface RunRow extends Omit<RunBrief, 'error'> {
  pipeline_name?: string;
  events_count?: number;
  dialog_count?: number;
  dialog_retained?: boolean;
  error?: string;
}

const STORAGE_KEY = 'baize_pipeline_editor';
const ICONS: Record<string, string> = {
  agent: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z',
  decision: 'M3 5v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2zm7 7h4v4h-4v-4zm0-6h4v4h-4V6z',
  ai_decision: 'M9 3V1h2v2h2V1h2v2h2a2 2 0 0 1 2 2v2h2v2h-2v2h2v2h-2v2h2v2h-2v2a2 2 0 0 1-2 2h-2v2h-2v-2h-2v2H7v-2H5a2 2 0 0 1-2-2v-2H1v-2h2v-2H1V9h2V7H1V5h2a2 2 0 0 1 2-2h2V1h2v2h2V1zM7 7h10v10H7V7zm2 2v6h6V9H9z',
  confirm: 'M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z',
  parallel: 'M4 6h6v12H4V6zm10 0h6v12h-6V6z',
  transform: 'M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  subpipeline: 'M4 4h16v16H4V4zm2 2h12v12H6V6zm2 2h8v8H8V8z',
  receiver: 'M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h6l6-6V5c0-1.1-.9-2-2-2zm-5 4h4v4h-4V7zm-2 4H8v-4h4v4zm-2 2h4v4h-4v-4z',
  datatransformer: 'M4 21V3h16v18l-8-5-8 5z',
  end: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z',
};

const NODE_LABELS: Record<string, string> = {
  receiver: '数据接收器',
  datatransformer: '数据转换',
  agent: '智能体',
  decision: '条件判断',
  ai_decision: 'AI 决策',
  confirm: '人工确认',
  parallel: '并行执行',
  transform: '数据转换',
  end: '结束对话',
  subpipeline: '子流水线',
};

const NODE_COLORS: Record<string, string> = {
  receiver: '#06b6d4',
  datatransformer: '#14b8a6',
  agent: '#3b82f6',
  decision: '#f59e0b',
  ai_decision: '#d946ef',
  confirm: '#ec4899',
  parallel: '#8b5cf6',
  transform: '#10b981',
  subpipeline: '#6366f1',
  end: '#ef4444',
};

const NODE_GRADIENTS: Record<string, [string, string]> = {
  receiver: ['#06b6d4', '#0891b2'],
  datatransformer: ['#14b8a6', '#0f766e'],
  agent: ['#3b82f6', '#1d4ed8'],
  decision: ['#f59e0b', '#b45309'],
  ai_decision: ['#e879f9', '#c026d3'],
  confirm: ['#ec4899', '#be185d'],
  parallel: ['#8b5cf6', '#6d28d9'],
  transform: ['#10b981', '#047857'],
  subpipeline: ['#6366f1', '#4338ca'],
  end: ['#ef4444', '#b91c1c'],
};

export default function PipelineEditor() {
  const [activeTab, setActiveTab] = useState<'instances' | 'templates' | 'receivers' | 'history'>('instances');

  // 模板库（内置 + 自定义）
  const [templates, setTemplates] = useState<UnifiedPipeline[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<UnifiedPipeline | null>(null);

  // 流水线实例（由模板创建的可运行对象）
  const [instances, setInstances] = useState<PipelineInstance[]>([]);
  const [selectedInstance, setSelectedInstance] = useState<PipelineInstance | null>(null);
  const [instanceStatus, setInstanceStatus] = useState<InstanceStatus | null>(null);
  const [instRuns, setInstRuns] = useState<RunRow[]>([]);

  // 接收器（自设置页迁入）
  const [receivers, setReceivers] = useState<ReceiverConfig[]>([]);

  // 执行历史
  const [runs, setRuns] = useState<RunRow[]>([]);

  const [loading, setLoading] = useState(true);

  const [feedback, setFeedback] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<{ kind: 'template' | 'instance' | 'builtin'; id: string; name: string } | null>(null);

  // 新建/编辑模板（同一拖拽图编辑器，编辑仅自定义模板）
  const [showCreateTpl, setShowCreateTpl] = useState(false);
  const [editTpl, setEditTpl] = useState<UnifiedPipeline | null>(null);

  // 新建/编辑实例
  const [showCreateInst, setShowCreateInst] = useState(false);
  const [instPresetTemplateId, setInstPresetTemplateId] = useState<string | null>(null);
  const [editingInst, setEditingInst] = useState(false);
  const [editInstData, setEditInstData] = useState<Partial<PipelineInstance>>({});

  // 接收器表单
  const [showReceiverForm, setShowReceiverForm] = useState(false);
  const [editingReceiver, setEditingReceiver] = useState<ReceiverConfig | null>(null);
  const [receiverForm, setReceiverForm] = useState({
    name: '', kind: 'webhook',
    webhook_path: '', syslog_port: 514, syslog_host: '0.0.0.0',
    watch_dir: '', watch_patterns: '*',
  });
  const [receiverSaving, setReceiverSaving] = useState(false);

  // 测试执行（针对实例）
  const [showTest, setShowTest] = useState<string | null>(null); // instance_id

  // Run 详情（历史/对话查看）
  const [viewRun, setViewRun] = useState<RunDetail | null>(null);
  const [viewRunLoading, setViewRunLoading] = useState(false);

  function showFeedback(type: 'ok' | 'err', msg: string) {
    setFeedback({ type, msg });
    setTimeout(() => setFeedback(null), 4000);
  }

  // ---- 数据加载 ----

  const loadTemplates = useCallback(async () => {
    try {
      const tpl = await api.listUnifiedTemplates();
      const unified: UnifiedPipeline[] = (tpl.templates || []).map((t: any) => ({
        id: t.id,
        name: t.name,
        description: t.description || '',
        type: t.type || 'manual',
        source: t.source === 'custom' || t.is_custom ? ('custom' as PipelineSource) : ('builtin' as PipelineSource),
        nodes: (t.nodes || t.steps || []) as PipelineNode[],
        edges: (t.edges || []) as PipelineEdge[],
        category: t.category || '',
        tags: Array.isArray(t.tags) ? t.tags : [],
        timeout_seconds: t.timeout_seconds,
        max_concurrency: t.max_concurrency,
        created_at: t.created_at,
        updated_at: t.updated_at,
      }));
      setTemplates(unified);
    } catch (e: any) {
      showFeedback('err', '加载模板库失败: ' + (e.message || '未知错误'));
    }
  }, []);

  const loadInstances = useCallback(async () => {
    try {
      const resp = await api.listInstances();
      setInstances(resp.instances || []);
    } catch (e: any) {
      showFeedback('err', '加载流水线失败: ' + (e.message || '未知错误'));
    }
  }, []);

  const loadReceivers = useCallback(async () => {
    try {
      const resp = await api.listReceivers();
      setReceivers(resp.receivers || []);
    } catch { /* 接收器不可用时静默 */ }
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      const resp = await api.listRuns({ limit: 50 });
      setRuns(resp.runs || []);
    } catch { /* ignore */ }
  }, []);

  const refreshAll = useCallback(() => {
    setLoading(true);
    Promise.allSettled([loadTemplates(), loadInstances(), loadReceivers(), loadRuns()])
      .finally(() => setLoading(false));
  }, [loadTemplates, loadInstances, loadReceivers, loadRuns]);

  useEffect(() => { refreshAll(); }, [refreshAll]);

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

  // 模板库中被某实例选用的模板视图（用于实例详情展示快照图）
  function templateOf(inst: PipelineInstance): UnifiedPipeline | null {
    const tpl = templates.find(t => t.id === inst.template_id);
    const snap: any = (inst as any).template_snapshot;
    return {
      id: inst.template_id,
      name: (snap?.name as string) || inst.template_name || tpl?.name || inst.name,
      description: inst.description || tpl?.description || '',
      type: inst.type || tpl?.type || 'auto',
      source: (inst.template_source || tpl?.source || 'builtin') === 'custom' ? ('custom' as PipelineSource) : ('builtin' as PipelineSource),
      nodes: (snap?.nodes || tpl?.nodes || []) as PipelineNode[],
      edges: (snap?.edges || tpl?.edges || []) as PipelineEdge[],
    };
  }

  // ---- 模板库操作 ----

  function selectTemplate(t: UnifiedPipeline) {
    setSelectedTemplate(t);
  }

  /** 打开拖拽画布编辑自定义模板（节点/连线删除、重配均在此完成） */
  function startEditTemplate(t: UnifiedPipeline) {
    if (t.source !== 'custom') return;
    setEditTpl(t);
    setShowCreateTpl(false);
  }

  async function deleteTemplate(t: UnifiedPipeline) {
    try {
      if (t.source === 'custom') {
        await api.deleteCustomPipeline(t.id);
      } else {
        await api.deleteBuiltinTemplate(t.id);
      }
      setConfirmDelete(null);
      showFeedback('ok', `已删除模板 "${t.name}"`);
      setTemplates(prev => prev.filter(x => x.id !== t.id));
      if (selectedTemplate?.id === t.id) setSelectedTemplate(null);
    } catch (e: any) {
      showFeedback('err', '删除失败: ' + (e.message || ''));
    }
  }

  // ---- 流水线实例操作 ----

  async function selectInstance(inst: PipelineInstance) {
    setSelectedInstance(inst);
    setEditingInst(false);
    setInstanceStatus(null);
    setInstRuns([]);
    try {
      const st = await api.getInstanceStatus(inst.id);
      setInstanceStatus(st);
    } catch { /* ignore */ }
    try {
      const resp = await api.listInstanceRuns(inst.id, 20);
      setInstRuns(resp.runs || []);
    } catch { /* ignore */ }
  }

  async function toggleInstance(inst: PipelineInstance) {
    try {
      if (inst.enabled) {
        await api.disableInstance(inst.id);
        showFeedback('ok', `已停用 "${inst.name}"`);
      } else {
        await api.enableInstance(inst.id);
        showFeedback('ok', `已启用 "${inst.name}"，开始并行消费接收器数据`);
      }
      const next = { ...inst, enabled: !inst.enabled };
      setInstances(prev => prev.map(x => x.id === inst.id ? next : x));
      if (selectedInstance?.id === inst.id) selectInstance(next);
    } catch (e: any) {
      showFeedback('err', '操作失败: ' + (e.message || ''));
    }
  }

  async function syncInstance(inst: PipelineInstance) {
    try {
      const res = await api.syncInstance(inst.id);
      showFeedback('ok', `实例 "${inst.name}" 已同步到模板最新定义`);
      const next = { ...inst, ...res.instance };
      setInstances(prev => prev.map(x => x.id === inst.id ? next : x));
      if (selectedInstance?.id === inst.id) selectInstance(next);
    } catch (e: any) {
      showFeedback('err', '同步失败: ' + (e.message || ''));
    }
  }

  async function deleteInstance(inst: PipelineInstance) {
    try {
      await api.deleteInstance(inst.id);
      setConfirmDelete(null);
      showFeedback('ok', `已删除流水线 "${inst.name}"`);
      setInstances(prev => prev.filter(x => x.id !== inst.id));
      if (selectedInstance?.id === inst.id) { setSelectedInstance(null); setInstanceStatus(null); setInstRuns([]); }
    } catch (e: any) {
      showFeedback('err', '删除失败: ' + (e.message || ''));
    }
  }

  function openCreateInstance(presetTemplateId?: string) {
    setInstPresetTemplateId(presetTemplateId || null);
    setShowCreateInst(true);
  }

  function openEditInstance(inst: PipelineInstance) {
    setEditInstData({
      name: inst.name,
      description: inst.description,
      receiver_id: inst.receiver_id,
      max_concurrency: inst.max_concurrency,
    });
    setEditingInst(true);
  }

  async function saveInstanceEdit() {
    if (!selectedInstance) return;
    try {
      const res = await api.updateInstance(selectedInstance.id, {
        name: editInstData.name || undefined,
        description: editInstData.description,
        receiver_id: editInstData.receiver_id,
        max_concurrency: editInstData.max_concurrency,
      });
      setEditingInst(false);
      showFeedback('ok', '流水线已更新');
      setInstances(prev => prev.map(x => x.id === selectedInstance.id ? { ...x, ...res.instance } : x));
      selectInstance({ ...selectedInstance, ...res.instance });
    } catch (e: any) {
      showFeedback('err', '保存失败: ' + (e.message || ''));
    }
  }

  async function onInstanceCreated(inst: PipelineInstance) {
    showFeedback('ok', `流水线「${inst.name}」已创建（来自模板 ${inst.template_name || inst.template_id}）`);
    setShowCreateInst(false);
    loadInstances();
    setActiveTab('instances'); saveState('instances');
    selectInstance(inst);
  }

  async function onTemplateCreated(name: string) {
    showFeedback('ok', `模板「${name}」已创建，可进入「流水线实例」用它创建流水线`);
    setShowCreateTpl(false);
    setEditTpl(null);
    loadTemplates();
  }

  async function onTemplateUpdated(name: string) {
    showFeedback('ok', `模板「${name}」已更新`);
    setShowCreateTpl(false);
    setEditTpl(null);
    loadTemplates();
  }

  // ---- 接收器操作 ----

  function openCreateReceiver() {
    setEditingReceiver(null);
    setReceiverForm({ name: '', kind: 'webhook', webhook_path: '', syslog_port: 514, syslog_host: '0.0.0.0', watch_dir: '', watch_patterns: '*' });
    setShowReceiverForm(true);
  }
  function openEditReceiver(r: ReceiverConfig) {
    setEditingReceiver(r);
    setReceiverForm({
      name: r.name, kind: r.kind,
      webhook_path: r.webhook_path, syslog_port: r.syslog_port || 514,
      syslog_host: r.syslog_host || '0.0.0.0',
      watch_dir: r.watch_dir, watch_patterns: r.watch_patterns || '*',
    });
    setShowReceiverForm(true);
  }
  async function saveReceiver() {
    if (!receiverForm.name.trim()) return;
    setReceiverSaving(true);
    try {
      if (editingReceiver) {
        await api.updateReceiver(editingReceiver.id, receiverForm);
      } else {
        await api.createReceiver(receiverForm);
      }
      setShowReceiverForm(false);
      loadReceivers();
    } catch (e: any) {
      showFeedback('err', '保存失败: ' + (e.message || ''));
    } finally { setReceiverSaving(false); }
  }
  async function toggleReceiver(r: ReceiverConfig) {
    try {
      await api.updateReceiver(r.id, { enabled: !r.enabled });
      setReceivers(prev => prev.map(x => x.id === r.id ? { ...x, enabled: !r.enabled } : x));
    } catch (e: any) {
      showFeedback('err', '操作失败: ' + (e.message || ''));
    }
  }
  async function removeReceiver(r: ReceiverConfig) {
    if (!confirm(`确定删除接收器「${r.name}」？绑定它的流水线将无法继续接收数据。`)) return;
    try {
      await api.deleteReceiver(r.id);
      setReceivers(prev => prev.filter(x => x.id !== r.id));
      showFeedback('ok', `已删除接收器 "${r.name}"`);
    } catch (e: any) {
      showFeedback('err', '删除失败: ' + (e.message || ''));
    }
  }

  // ---- Run 详情 ----

  async function openRunDetail(runId: string) {
    setViewRunLoading(true);
    setViewRun(null);
    try {
      const resp = await api.getRun(runId);
      setViewRun(resp.run || (resp as any));
    } catch (e: any) {
      showFeedback('err', '加载运行详情失败: ' + (e.message || ''));
    } finally { setViewRunLoading(false); }
  }

  // 判断接收器被哪些实例使用
  function receiverBindings(r: ReceiverConfig): PipelineInstance[] {
    return instances.filter(i => i.receiver_id === r.id);
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">流水线管理</h1>
          <p className="text-xs text-gray-500 dark:text-slate-400 mt-1">
            模板（图编排定义）→ 流水线实例（绑定接收器、可并行处理入站数据，每次入站 = 一次独立对话，不进入对话/会话管理）
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refreshAll}
            title="刷新全部数据"
            className="px-3 py-2 text-sm rounded-xl border border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            刷新
          </button>
          {activeTab === 'templates' && (
            <button
              onClick={() => setShowCreateTpl(true)}
              className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 rounded-xl transition-colors font-medium text-white"
            >
              + 新建模板
            </button>
          )}
          {activeTab === 'instances' && (
            <button
              onClick={() => openCreateInstance()}
              className="px-4 py-2 text-sm bg-emerald-600 hover:bg-emerald-700 rounded-xl transition-colors font-medium text-white"
            >
              + 新建流水线
            </button>
          )}
          {activeTab === 'receivers' && (
            <button
              onClick={openCreateReceiver}
              className="px-4 py-2 text-sm bg-cyan-600 hover:bg-cyan-500 rounded-xl transition-colors font-medium text-white"
            >
              + 新建接收器
            </button>
          )}
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
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex border-b border-slate-200 dark:border-slate-700 gap-1 flex-wrap">
        {([
          ['instances', '流水线实例'],
          ['templates', '模板库'],
          ['receivers', '数据接收器'],
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

      {/* ====== 流水线实例 ====== */}
      {activeTab === 'instances' && (
        <InstancesTab
          instances={instances}
          templates={templates}
          receivers={receivers}
          selected={selectedInstance}
          status={instanceStatus}
          instRuns={instRuns}
          editing={editingInst}
          editData={editInstData}
          setEditData={setEditInstData}
          onSelect={selectInstance}
          onToggle={toggleInstance}
          onSync={syncInstance}
          onDelete={inst => setConfirmDelete({ kind: 'instance', id: inst.id, name: inst.name })}
          onEdit={openEditInstance}
          onSaveEdit={saveInstanceEdit}
          onCancelEdit={() => setEditingInst(false)}
          onTest={inst => setShowTest(inst.id)}
          onOpenRun={openRunDetail}
          onOpenRunsHistory={() => { setActiveTab('history'); saveState('history'); }}
          templateOf={templateOf}
        />
      )}

      {/* ====== 模板库 ====== */}
      {activeTab === 'templates' && (
        <TemplatesTab
          templates={templates}
          selected={selectedTemplate}
          onSelect={selectTemplate}
          onStartEdit={startEditTemplate}
          onDelete={t => setConfirmDelete({ kind: t.source === 'custom' ? 'template' : 'builtin', id: t.id, name: t.name })}
          onCreateInstance={t => openCreateInstance(t.id)}
          onOpenCreate={() => setShowCreateTpl(true)}
          onOpenRun={openRunDetail}
        />
      )}

      {/* ====== 数据接收器 ====== */}
      {activeTab === 'receivers' && (
        <ReceiversTab
          receivers={receivers}
          instances={instances}
          onOpenCreate={openCreateReceiver}
          onOpenEdit={openEditReceiver}
          onToggle={toggleReceiver}
          onDelete={removeReceiver}
          bindingsOf={receiverBindings}
          onGotoInstance={inst => {
            setActiveTab('instances'); saveState('instances');
            const found = instances.find(i => i.id === inst.id);
            if (found) selectInstance(found);
          }}
        />
      )}

      {/* ====== 执行历史 ====== */}
      {activeTab === 'history' && (
        <HistoryTab runs={runs} onRefresh={loadRuns} onOpenRun={openRunDetail} />
      )}

      {/* ====== 新建/编辑模板（拖拽流程图编辑器） ====== */}
      {(showCreateTpl || editTpl) && (
        <CreatePipelineModal
          initial={editTpl}
          onClose={() => { setShowCreateTpl(false); setEditTpl(null); }}
          onCreated={onTemplateCreated}
          onUpdated={onTemplateUpdated}
          showFeedback={showFeedback}
        />
      )}

      {/* ====== 新建流水线实例 ====== */}
      {showCreateInst && (
        <CreateInstanceModal
          templates={templates}
          receivers={receivers}
          presetTemplateId={instPresetTemplateId}
          onClose={() => setShowCreateInst(false)}
          onCreated={onInstanceCreated}
          showFeedback={showFeedback}
        />
      )}

      {/* ====== 接收器表单 ====== */}
      {showReceiverForm && (
        <ReceiverFormModal
          editing={editingReceiver}
          form={receiverForm}
          setForm={setReceiverForm}
          saving={receiverSaving}
          onClose={() => setShowReceiverForm(false)}
          onSave={saveReceiver}
        />
      )}

      {/* ====== 删除确认 ====== */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setConfirmDelete(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-2xl p-6 w-full max-w-sm mx-4" onClick={e => e.stopPropagation()}>
            <h3 className="text-base font-semibold text-slate-100">确认删除</h3>
            <p className="text-sm text-gray-400 mt-1">
              {confirmDelete.kind === 'instance'
                ? `流水线实例「${confirmDelete.name}」将被删除，正在排队/运行的任务会被取消，历史记录保留。`
                : confirmDelete.kind === 'template'
                  ? `自定义模板「${confirmDelete.name}」将被删除；已由此模板创建的流水线实例仍可继续运行（保留快照）。`
                  : `内置模板「${confirmDelete.name}」将从模板库隐藏（可稍后恢复）。`}
            </p>
            <div className="flex gap-3 mt-5">
              <button
                onClick={async () => {
                  if (confirmDelete.kind === 'instance') {
                    const inst = instances.find(i => i.id === confirmDelete.id);
                    if (inst) await deleteInstance(inst);
                  } else {
                    const t = templates.find(x => x.id === confirmDelete.id);
                    if (t) await deleteTemplate(t);
                  }
                }}
                className="flex-1 px-4 py-2.5 bg-red-600 hover:bg-red-500 rounded-xl text-sm font-medium text-white"
              >
                删除
              </button>
              <button
                onClick={() => setConfirmDelete(null)}
                className="px-4 py-2.5 bg-gray-800 hover:bg-gray-700 rounded-xl text-sm text-gray-300"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ====== 测试实例弹窗 ====== */}
      {showTest && (() => {
        const inst = instances.find(i => i.id === showTest);
        if (!inst) return null;
        return (
          <TestPipelineModal
            instance={inst}
            onClose={() => setShowTest(null)}
            onFinished={() => { loadRuns(); if (selectedInstance?.id === inst.id) selectInstance(selectedInstance); }}
          />
        );
      })()}

      {/* ====== Run 详情（流水线对话） ====== */}
      {(viewRunLoading || viewRun) && (
        <RunDialogModal
          loading={viewRunLoading}
          run={viewRun}
          onClose={() => { setViewRun(null); setViewRunLoading(false); }}
        />
      )}
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

function PipelineDetailPanel({ pipeline, onEdit, onPrimary, primaryLabel, submitting, isCustom, hideActions }: {
  pipeline: UnifiedPipeline;
  onEdit?: () => void;
  onPrimary?: () => void;
  primaryLabel?: string;
  submitting?: boolean;
  isCustom?: boolean;
  hideActions?: boolean;
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
          {!hideActions && isCustom && onEdit && (
            <button onClick={onEdit} className="px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300 hover:bg-slate-200">编辑</button>
          )}
          {!hideActions && onPrimary && (
            <button onClick={onPrimary} disabled={submitting} className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">
              {submitting ? '提交中...' : (primaryLabel || '执行')}
            </button>
          )}
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

/* ================================================================
   CreatePipelineModal — 拖拽流程图编辑器（新建 & 编辑共用）
   ================================================================ */
interface CanvasNode {
  id: string;
  type: string;
  display_name: string;
  agent?: string;
  prompt_template?: string;
  description?: string;
  tools?: string[];
  branches?: Array<{ when?: string; condition?: string; goto?: string; target?: string; label?: string; default?: boolean }>;
  parallel_branches?: string[];
  confirm_prompt?: string;
  confirm_options?: string[];
  confirm_branches?: Record<string, string>;
  decision_prompt?: string;
  decision_model?: string;
  decision_expression?: string;
  pipeline_name?: string;
  transform_expr?: string;
  timeout_seconds?: number;
  max_retries?: number;
  merge_strategy?: string;
  max_concurrency?: number;
  target?: string;
  error_target?: string;
  ignore_error?: boolean;
  // 结束对话节点：true=保留归档，false/缺省=运行完成后回收
  save_dialog?: boolean;
  x: number;
  y: number;
}

interface CanvasEdge {
  source: string;
  target: string;
  label?: string;
}

const NCW = 184, NCH = 80;

/** 分支类节点：目标由节点内分支元数据驱动（画布连线仅作为视图） */
function isBranchNodeType(t: string) {
  return t === 'decision' || t === 'ai_decision' || t === 'confirm' || t === 'parallel';
}

function edgeKey(e: { source: string; target: string }) { return `${e.source}→${e.target}`; }

/** 从模板节点推导连线（与详情页展示一致，供布局与编辑初始化使用） */
function deriveGraphEdges(nodeList: CanvasNode[]): CanvasEdge[] {
  const result: CanvasEdge[] = [];
  for (let i = 0; i < nodeList.length - 1; i++) {
    const n = nodeList[i];
    if ((n.type === 'decision' || n.type === 'ai_decision') && n.branches && n.branches.length > 0) {
      n.branches.forEach((b: any) => {
        const tgt = b.goto || b.target;
        if (tgt && tgt !== n.id) result.push({ source: n.id, target: tgt, label: b.label || b.condition || b.when || (b.default ? '默认' : '') });
      });
    } else if (n.type === 'confirm' && n.confirm_branches && Object.keys(n.confirm_branches).length > 0) {
      Object.entries(n.confirm_branches).forEach(([k, v]) => result.push({ source: n.id, target: v as string, label: k }));
    } else if (n.type === 'parallel' && n.parallel_branches && n.parallel_branches.length > 0) {
      n.parallel_branches.forEach((b: any) => {
        const tgt = typeof b === 'string' ? b : (b && b.node_id);
        if (tgt && tgt !== n.id) result.push({ source: n.id, target: tgt, label: '并行' });
      });
    } else {
      const nx = nodeList[i + 1];
      if (nx && nx.id !== n.id) result.push({ source: n.id, target: nx.id });
    }
  }
  const seen = new Set<string>();
  return result.filter(e => {
    if (e.source === e.target) return false;
    const k = edgeKey(e);
    if (seen.has(k)) return false;
    seen.add(k); return true;
  });
}

/** 分层布局：为缺少坐标的模板节点计算画布位置（中心坐标） */
function layoutTemplateNodes(nodeList: CanvasNode[], edges: CanvasEdge[]): Array<{ id: string; x: number; y: number }> {
  const out: Array<{ id: string; x: number; y: number }> = [];
  if (nodeList.length === 0) return out;
  const nodeMap = new Map(nodeList.map(n => [n.id, n]));
  const inDegree = new Map<string, number>();
  const adj = new Map<string, string[]>();
  nodeList.forEach(n => { inDegree.set(n.id, 0); adj.set(n.id, []); });
  edges.forEach(e => {
    if (!nodeMap.has(e.source) || !nodeMap.has(e.target) || e.source === e.target) return;
    if (!adj.has(e.source)) adj.set(e.source, []);
    adj.get(e.source)!.push(e.target);
    inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
  });
  const layers: string[][] = [];
  let queue: string[] = [];
  inDegree.forEach((deg, id) => { if (deg === 0) queue.push(id); });
  if (queue.length === 0) queue.push(nodeList[0].id);
  let remaining = nodeList.length;
  const done = new Set<string>();
  while (queue.length > 0 && remaining > 0) {
    const layer: string[] = [];
    const nextQ: string[] = [];
    for (const id of queue) {
      if (done.has(id)) continue;
      done.add(id); layer.push(id); remaining--;
      for (const nxt of (adj.get(id) || [])) {
        if (done.has(nxt)) continue;
        const nd = (inDegree.get(nxt) || 1) - 1; inDegree.set(nxt, nd);
        if (nd <= 0) nextQ.push(nxt);
      }
    }
    if (layer.length > 0) layers.push(layer);
    if (nextQ.length === 0 && remaining > 0) {
      nodeList.forEach(n => { if (!done.has(n.id)) { layers.push([n.id]); done.add(n.id); remaining--; } });
      break;
    }
    queue = nextQ;
  }
  // 层内排序：保持与原列表一致的相对顺序，减少交叉
  const orderOf = new Map(nodeList.map((n, i) => [n.id, i]));
  layers.forEach(l => l.sort((a, b) => (orderOf.get(a) || 0) - (orderOf.get(b) || 0)));
  const sx = NCW + 70, sy = NCH + 46, padL = NCW / 2 + 40, padT = NCH / 2 + 30;
  layers.forEach((layer, li) => {
    layer.forEach((nodeId, ni) => {
      out.push({ id: nodeId, x: padL + li * sx, y: padT + ni * sy });
    });
  });
  return out;
}

function canvasSizeFor(nodes: CanvasNode[]): { width: number; height: number } {
  if (nodes.length === 0) return { width: 960, height: 560 };
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  nodes.forEach(n => {
    minX = Math.min(minX, n.x - NCW / 2); maxX = Math.max(maxX, n.x + NCW / 2);
    minY = Math.min(minY, n.y - NCH / 2); maxY = Math.max(maxY, n.y + NCH / 2);
  });
  return {
    width: Math.max(Math.ceil(maxX - minX) + 120, 960),
    height: Math.max(Math.ceil(maxY - minY) + 120, 560),
  };
}

function CreatePipelineModal({ initial, onClose, onCreated, onUpdated, showFeedback }: {
  initial?: UnifiedPipeline | null;
  onClose: () => void;
  onCreated: (name: string) => void;
  onUpdated?: (name: string) => void;
  showFeedback: (type: 'ok' | 'err', msg: string) => void;
}) {
  const isEdit = !!initial;
  const [name, setName] = useState(initial?.name || '');
  const [description, setDescription] = useState(initial?.description || '');
  const [pipelineType, setPipelineType] = useState<'manual' | 'auto'>(initial?.type === 'auto' ? 'auto' : 'manual');
  const [nodes, setNodes] = useState<CanvasNode[]>([]);
  const [edges, setEdges] = useState<CanvasEdge[]>([]);
  const [agents, setAgents] = useState<AgentMetadata[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [connectStart, setConnectStart] = useState<string | null>(null);
  const [dragging, setDragging] = useState<{ id: string; sx: number; sy: number } | null>(null);
  const [selEdgeKey, setSelEdgeKey] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const initRef = useRef(false);

  // 加载已有智能体列表
  useEffect(() => {
    api.listAgents().then(r => setAgents(r.agents)).catch(() => {});
  }, []);

  // 编辑模板：把模板 nodes 装入画布（自动补坐标 / 推导连线）
  useEffect(() => {
    if (!initial || initRef.current) return;
    initRef.current = true;
    const raw: any[] = (initial.nodes as any[]) || [];
    if (raw.length === 0) return;
    const list: CanvasNode[] = raw.map((n: any) => {
      const { x, y, ...rest } = n;
      const b: CanvasNode = { ...rest, x: 0, y: 0 } as CanvasNode;
      // 兼容老字段
      if (Array.isArray(b.parallel_branches)) {
        b.parallel_branches = b.parallel_branches.map((pb: any) => typeof pb === 'string' ? pb : (pb && pb.node_id) || '').filter(Boolean);
      }
      if (!b.confirm_branches) b.confirm_branches = {};
      return b;
    }).filter((n: any) => n && n.id);
    // 优先用存储边，否则从节点分支/顺序推导（仅作编辑底稿）
    let es: CanvasEdge[] = ((initial.edges as any[]) || []).map((e: any) => ({ source: e.source, target: e.target, label: e.label || '' }));
    if (es.length === 0) es = deriveGraphEdges(list);
    // 既有显式连线也要与分支节点合并展示（去重）
    const derived = deriveGraphEdges(list);
    const seen = new Set(es.map(edgeKey));
    derived.forEach(e => { if (!seen.has(edgeKey(e))) { es.push(e); seen.add(edgeKey(e)); } });
    const placed = layoutTemplateNodes(list, es);
    const posMap = new Map(placed.map(p => [p.id, p]));
    const positioned = list.map((n, i) => {
      const p = posMap.get(n.id);
      if (p) return { ...n, x: p.x, y: p.y };
      return { ...n, x: 120 + (i % 4) * 230, y: 80 + Math.floor(i / 4) * 130 };
    });
    setNodes(positioned);
    setEdges(es);
  }, [initial]);

  const selectedNode = selectedNodeId ? nodes.find(n => n.id === selectedNodeId) : null;

  function nextNodeId() {
    let max = 0;
    nodes.forEach(n => { const m = /^node_(\d+)$/.exec(n.id); if (m) max = Math.max(max, parseInt(m[1], 10)); });
    return `node_${max + 1}`;
  }

  function addNode(nodeType: string) {
    const id = nextNodeId();
    const count = nodes.length + 1;
    setNodes(prev => [...prev, {
      id, type: nodeType, display_name: '',
      x: 160 + (count % 3) * 230 + Math.random() * 40, y: 120 + (count % 4) * 120,
    }]);
    setSelectedNodeId(id);
  }

  function updateNode(updates: Partial<CanvasNode>) {
    const cur = nodes.find(n => n.id === selectedNodeId);
    if (!cur) return;
    const oldId = cur.id;
    const nextId = (updates.id || '').trim();
    // 改名后级联修正其它节点引用与连线，避免产生悬空引用
    const renamed = nextId !== '' && nextId !== oldId;
    setNodes(prev => prev.map(n => {
      let nn: CanvasNode = n.id === selectedNodeId ? { ...n, ...updates } : { ...n };
      if (!renamed) return nn;
      const ref = (v: string | undefined) => (v === oldId ? nextId : v);
      if (n.id === oldId) nn.id = nextId;
      nn.target = ref(nn.target);
      nn.error_target = ref(nn.error_target);
      if (Array.isArray(nn.branches)) nn.branches = nn.branches.map(b =>
        ((b.goto || b.target) === oldId) ? { ...b, goto: nextId, target: nextId } : b);
      if (Array.isArray(nn.parallel_branches)) nn.parallel_branches = nn.parallel_branches.map(pb => pb === oldId ? nextId : pb);
      if (nn.confirm_branches) {
        const cb: Record<string, string> = {};
        Object.entries(nn.confirm_branches).forEach(([k, v]) => { cb[k] = v === oldId ? nextId : v; });
        nn.confirm_branches = cb;
      }
      return nn;
    }));
    if (renamed) {
      setEdges(prev => prev.map(e => ({
        ...e,
        source: e.source === oldId ? nextId : e.source,
        target: e.target === oldId ? nextId : e.target,
      })));
      setSelectedNodeId(nextId);
    }
  }

  /** 删除节点时同步清除其它节点内对该节点的引用，避免“删不掉/校验失败” */
  function deleteNode() {
    if (!selectedNodeId) return;
    const dead = selectedNodeId;
    setNodes(prev => prev
      .filter(n => n.id !== dead)
      .map(n => {
        let next = { ...n };
        if (next.target === dead) next.target = undefined;
        if (next.error_target === dead) next.error_target = undefined;
        if (Array.isArray(next.branches)) next.branches = next.branches.filter(b => (b.goto || b.target) !== dead);
        if (Array.isArray(next.parallel_branches)) next.parallel_branches = next.parallel_branches.filter(b => b !== dead);
        if (next.confirm_branches) {
          const cb: Record<string, string> = {};
          Object.entries(next.confirm_branches).forEach(([k, v]) => { if (v !== dead) cb[k] = v; });
          next.confirm_branches = cb;
        }
        return next;
      }));
    setEdges(prev => prev.filter(e => e.source !== dead && e.target !== dead));
    setSelectedNodeId(null);
  }

  function deleteEdge() {
    if (!selEdgeKey) return;
    const [s, t] = selEdgeKey.split('→');
    setEdges(prev => prev.filter(e => !(e.source === s && e.target === t)));
    // 若该连线源节点是分支类节点，则同步删除对应分支元数据
    setNodes(prev => prev.map(n => {
      if (n.id !== s) return n;
      let next = { ...n };
      if (n.type === 'decision' || n.type === 'ai_decision') {
        next.branches = (n.branches || []).filter(b => (b.goto || b.target) !== t);
      } else if (n.type === 'confirm' && n.confirm_branches) {
        const cb: Record<string, string> = {};
        Object.entries(n.confirm_branches).forEach(([k, v]) => { if (v !== t) cb[k] = v; });
        next.confirm_branches = cb;
      } else if (n.type === 'parallel' && Array.isArray(n.parallel_branches)) {
        next.parallel_branches = n.parallel_branches.filter(pb => pb !== t);
      }
      return next;
    }));
    setSelEdgeKey(null);
  }

  /** 点键盘 Delete/Backspace 删除选中的节点或连线（输入框聚焦时不触发） */
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)) return;
      if ((e.key === 'Delete' || e.key === 'Backspace')) {
        if (selEdgeKey) { e.preventDefault(); deleteEdge(); }
        else if (selectedNodeId) { e.preventDefault(); deleteNode(); }
      }
      if (e.key === 'Escape') { setConnectStart(null); setSelEdgeKey(null); setSelectedNodeId(null); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

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
    setSelEdgeKey(null);
  }

  function handleEdgeClick(e: React.MouseEvent, key: string) {
    e.stopPropagation();
    setSelEdgeKey(prev => prev === key ? null : key);
    setSelectedNodeId(null);
  }

  /** 分支类节点连线时自动写入分支元数据，保证运行路由生效 */
  function linkBranchMeta(n: CanvasNode, targetId: string): CanvasNode {
    const upd: CanvasNode = { ...n };
    if (n.type === 'decision' || n.type === 'ai_decision') {
      const brs = n.branches || [];
      if (!brs.some(b => (b.goto || b.target) === targetId)) {
        const anyDefault = brs.some(b => !!b.default);
        upd.branches = [...brs, {
          target: targetId, goto: targetId, condition: '', label: `分支${brs.length + 1}`,
          default: !anyDefault && brs.length === 0,
        }];
      }
    } else if (n.type === 'confirm') {
      const opts = (n.confirm_options || []).slice();
      if (opts.length === 0) opts.push('确认');
      const cb = { ...(n.confirm_branches || {}) };
      let opt: string | null = null;
      for (const o of opts) if (!cb[o]) { opt = o; break; }
      if (!opt) { opt = `操作${Object.keys(cb).length + 1}`; opts.push(opt); }
      cb[opt] = targetId;
      upd.confirm_options = opts;
      upd.confirm_branches = cb;
    } else if (n.type === 'parallel') {
      const pb = n.parallel_branches || [];
      if (!pb.includes(targetId)) upd.parallel_branches = [...pb, targetId];
    }
    return upd;
  }

  function handlePortClick(e: React.MouseEvent, nodeId: string, port: 'out' | 'in') {
    e.stopPropagation();
    if (port === 'out') {
      setSelEdgeKey(null);
      setConnectStart(prev => prev === nodeId ? null : nodeId);
    } else if (port === 'in' && connectStart && connectStart !== nodeId) {
      const src = connectStart;
      if (!nodes.some(n => n.id === src)) { setConnectStart(null); return; }
      const srcType = nodes.find(n => n.id === src)?.type || '';
      if (!edges.some(ed => ed.source === src && ed.target === nodeId)) {
        setEdges(prev => [...prev, { source: src, target: nodeId }]);
      }
      if (isBranchNodeType(srcType)) {
        setNodes(prev => prev.map(n => n.id === src ? linkBranchMeta(n, nodeId) : n));
      }
      setConnectStart(null);
      setSelEdgeKey(null);
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

  /** 画布展示 = 已存连线 + 从分支元数据实时推导的连线（去重） */
  const displayEdges = (() => {
    const out: CanvasEdge[] = [];
    const seen = new Set<string>();
    edges.forEach(e => { out.push(e); seen.add(edgeKey(e)); });
    for (const n of nodes) {
      if ((n.type === 'decision' || n.type === 'ai_decision') && n.branches) {
        n.branches.forEach((b: any) => {
          const t = b.goto || b.target;
          if (t && t !== n.id && !seen.has(edgeKey({ source: n.id, target: t }))) {
            seen.add(edgeKey({ source: n.id, target: t }));
            out.push({ source: n.id, target: t, label: b.label || b.condition || b.when || (b.default ? '默认' : '') });
          }
        });
      } else if (n.type === 'confirm' && n.confirm_branches) {
        Object.entries(n.confirm_branches).forEach(([k, v]) => {
          if (v && !seen.has(edgeKey({ source: n.id, target: v }))) {
            seen.add(edgeKey({ source: n.id, target: v }));
            out.push({ source: n.id, target: v, label: k });
          }
        });
      } else if (n.type === 'parallel' && n.parallel_branches) {
        n.parallel_branches.forEach(pb => {
          if (pb && !seen.has(edgeKey({ source: n.id, target: pb }))) {
            seen.add(edgeKey({ source: n.id, target: pb }));
            out.push({ source: n.id, target: pb, label: '并行' });
          }
        });
      }
      if (n.target && n.target !== n.id && !seen.has(edgeKey({ source: n.id, target: n.target }))) {
        seen.add(edgeKey({ source: n.id, target: n.target }));
        out.push({ source: n.id, target: n.target, label: '继续' });
      }
    }
    return out;
  })();

  /** 节点保存前校验：发现悬空分支引用、无入口等基础问题 */
  function validateGraph(): string[] {
    const problems: string[] = [];
    if (nodes.length === 0) { problems.push('尚未添加任何节点'); return problems; }
    const seenIds = new Map<string, number>();
    nodes.forEach(n => {
      if (!n.id.trim()) problems.push('存在空节点 ID，请为每个节点填写 ID');
      else if (!/^[A-Za-z0-9_.-]+$/.test(n.id)) problems.push(`节点 ID「${n.id}」含非法字符，仅允许字母数字 . _ -`);
      seenIds.set(n.id, (seenIds.get(n.id) || 0) + 1);
    });
    seenIds.forEach((cnt, id) => { if (cnt > 1) problems.push(`节点 ID「${id}」重复出现 ${cnt} 次，请改为唯一`); });
    if (problems.length > 0) return problems;
    const nodeIds = new Set(nodes.map(n => n.id));
    nodes.forEach(n => {
      const refs: string[] = [];
      if (n.target) refs.push(n.target);
      if (n.error_target) refs.push(n.error_target);
      (n.branches || []).forEach(b => { const t = b.goto || b.target; if (t) refs.push(t); });
      (n.parallel_branches || []).forEach(b => refs.push(b));
      Object.values(n.confirm_branches || {}).forEach(v => refs.push(v));
      refs.forEach(r => { if (r && !nodeIds.has(r)) problems.push(`节点 ${n.id} 引用了不存在的节点「${r}」`); });
    });
    // 分支条件缺失提示（默认分支允许留空）
    nodes.forEach(n => {
      if ((n.type === 'decision' || n.type === 'ai_decision') && (n.branches || []).length > 0) {
        (n.branches || []).forEach((b, i) => {
          const hasTarget = !!(b.goto || b.target);
          if (!hasTarget) problems.push(`节点 ${n.id} 第 ${i + 1} 个分支缺少跳转目标`);
        });
        const defaultCount = (n.branches || []).filter(b => !!b.default).length;
        if (defaultCount > 1) problems.push(`节点 ${n.id} 有 ${defaultCount} 个默认分支，只能保留一个`);
      }
    });
    return problems;
  }

  async function handleSave() {
    if (!name.trim()) { showFeedback('err', '请填写流水线名称'); return; }
    const problems = validateGraph();
    if (problems.length > 0) { showFeedback('err', problems[0]); return; }
    setCreating(true);
    try {
      const payload = {
        name: name.trim(),
        description: description || undefined,
        type: pipelineType,
        category: (initial as any)?.category || undefined,
        tags: (initial as any)?.tags || undefined,
        nodes: nodes.map(n => {
          const { x, y, ...rest } = n;
          // 清理空引用，避免后端校验报错
          const nn: any = { ...rest };
          if (nn.branches && Array.isArray(nn.branches)) nn.branches = nn.branches.filter((b: any) => b.goto || b.target || b.default);
          if (nn.parallel_branches) nn.parallel_branches = nn.parallel_branches.filter(Boolean);
          if (nn.confirm_branches) {
            const cb: Record<string, string> = {};
            Object.entries(nn.confirm_branches).forEach(([k, v]) => { if (v) cb[k] = v; });
            nn.confirm_branches = Object.keys(cb).length ? cb : undefined;
          }
          if (nn.target === '') delete nn.target;
          if (nn.error_target === '') delete nn.error_target;
          return nn;
        }),
        edges: edges.map(e => ({ source: e.source, target: e.target, label: e.label || undefined })),
        steps: nodes.map(n => ({ agent_name: n.agent || n.id, display_name: n.display_name || n.id, description: n.type || 'agent' })),
      };
      if (isEdit && initial) {
        await (api as any).updateCustomPipeline(initial.id, payload);
        onUpdated && onUpdated(name.trim());
      } else {
        await (api as any).createCustomPipeline(payload);
        onCreated(name.trim());
      }
    } catch (e: any) {
      showFeedback('err', (isEdit ? '保存' : '创建') + '失败: ' + (e.message || ''));
    } finally { setCreating(false); }
  }

  const [mousePos, setMousePos] = useState<{ x: number; y: number } | null>(null);
  const dims = canvasSizeFor(nodes);

  return (
    <div className="fixed inset-0 z-50 flex bg-black/60" onClick={onClose}>
      <div className="w-full h-full max-w-[95vw] max-h-[90vh] m-auto bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl flex flex-col overflow-hidden" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800 shrink-0">
          <div className="flex items-center gap-4">
            <h2 className="text-base font-semibold text-slate-100">{isEdit ? '编辑模板（拖拽编排）' : '新建流水线（拖拽编排）'}</h2>
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
            <span className="text-[10px] text-gray-500">{nodes.length} 节点, {displayEdges.length} 连线</span>
            {selEdgeKey && (
              <button onClick={deleteEdge} title="删除选中的连线（节点分支目标也会同步清理）"
                className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium text-red-300 bg-red-500/10 border border-red-500/30 hover:bg-red-500/20">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24"><path strokeLinecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
                删除选中连线
              </button>
            )}
            {isEdit && (
              <span className="text-[10px] px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20">编辑模式</span>
            )}
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
            <div className="relative flex-1 bg-gray-950 border-b border-gray-800 overflow-auto"
              onMouseMove={(e) => { handleSvgMouseMove(e); if (connectStart) setMousePos(svgCoords(e)); }}
              onMouseUp={handleSvgMouseUp} onMouseLeave={() => { setDragging(null); }}>
              <svg ref={svgRef} width={dims.width} height={dims.height} style={{ display: 'block', minWidth: '100%', minHeight: '100%' }}>
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
                <rect width={dims.width} height={dims.height} fill="url(#cvGrid)" />
                {/* 边：贝塞尔曲线（点击选中，Delete/Backspace 或中点 ╳ 删除） */}
                {displayEdges.map((e) => {
                  const sn = nodes.find(n => n.id === e.source);
                  const tn = nodes.find(n => n.id === e.target);
                  if (!sn || !tn) return null;
                  const x1 = sn.x, y1 = sn.y + NCH / 2;
                  const x2 = tn.x, y2 = tn.y - NCH / 2;
                  const dx = Math.abs(x2 - x1) * 0.4;
                  const d = `M ${x1} ${y1} C ${x1} ${y1 + dx}, ${x2} ${y2 - dx}, ${x2} ${y2}`;
                  const key = edgeKey(e);
                  const isSel = selEdgeKey === key;
                  const lx = (x1 + x2) / 2, ly = (sn.y + tn.y) / 2;
                  return (
                    <g key={`edge-${key}`}>
                      <path d={d} fill="none" stroke={isSel ? '#3b82f6' : '#334155'} strokeWidth={isSel ? 10 : 6} opacity={isSel ? 0.22 : 0.28}
                        style={{ cursor: 'pointer' }} onClick={ev => handleEdgeClick(ev, key)} />
                      <path d={d} fill="none" stroke={isSel ? '#60a5fa' : '#64748b'} strokeWidth={isSel ? 2.5 : 2} strokeLinecap="round"
                        markerEnd="url(#cvArrow)" style={{ pointerEvents: 'none' }} />
                      {e.label ? (
                        <>
                          <rect x={lx - 34} y={ly - 13} width={68} height={16} rx={8}
                            fill={isSel ? '#1e40af' : '#1e293b'} stroke={isSel ? '#60a5fa' : '#334155'} strokeWidth={0.5}
                            style={{ pointerEvents: 'none' }} />
                          <text x={lx} y={ly - 2} textAnchor="middle" fill="#cbd5e1" fontSize={9} fontWeight={600}>{e.label}</text>
                        </>
                      ) : null}
                      {isSel && (
                        <g onClick={ev => { ev.stopPropagation(); deleteEdge(); }} style={{ cursor: 'pointer' }}>
                          <circle cx={lx} cy={ly + 24} r={8} fill="#dc2626" stroke="#7f1d1d" strokeWidth={1} />
                          <path d={`M ${lx - 3} ${ly + 21} l 6 6 M ${lx + 3} ${ly + 21} l -6 6`} stroke="#fff" strokeWidth={1.6} strokeLinecap="round" />
                        </g>
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
              <span className="text-[10px] text-gray-600">拖拽移动 · ○ 端口连线 · 点击连线/节点后用 Delete/Backspace 删除 · Esc 取消选择</span>
              <span className="text-[10px] text-gray-600">{nodes.length} 节点 · {displayEdges.length} 连线</span>
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
                  <div className="flex items-center gap-1">
                    <span className="text-[9px] text-gray-600 font-mono">#{selectedNode.id}</span>
                    <button onClick={deleteNode} title="删除该节点（自动清理引用它的分支/连线）"
                      className="flex items-center gap-1 px-2 py-1 rounded-lg text-red-400 bg-red-500/10 border border-red-500/25 text-[10px] font-medium hover:bg-red-500/20">
                      删除
                    </button>
                  </div>
                </div>
                <NodeConfigForm node={selectedNode} onChange={updateNode} agents={agents} allNodes={nodes} />
                <div className="pt-1 border-t border-gray-800 mt-1">
                  <p className="text-[9px] text-gray-600 leading-relaxed">
                    分支类节点（条件/AI决策/确认/并行）：连出线会自动写入右侧分支目标；条件、默认分支可在此配置。
                  </p>
                </div>
              </>
            ) : (
              <div className="text-[10px] text-gray-500 pt-6 text-center space-y-3">
                <div>点击画布节点<br/>编辑配置</div>
                {selEdgeKey && (
                  <button onClick={deleteEdge} className="px-3 py-1.5 rounded-lg text-red-300 bg-red-500/10 border border-red-500/30 text-[11px] hover:bg-red-500/20">
                    删除选中的连线
                  </button>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-gray-800 shrink-0">
          <span className="text-[10px] text-gray-500">{nodes.length > 0 ? `${nodes.length} 个节点` : '请从左侧面板添加节点'}</span>
          <div className="flex items-center gap-3">
            {isEdit && (
              <span className="text-[10px] text-amber-400/80 max-w-[280px] truncate">模板 {initial?.name} 的节点结构将整体替换</span>
            )}
            <button onClick={onClose} className="px-4 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-sm text-gray-300">取消</button>
            <button onClick={handleSave} disabled={creating || !name.trim()}
              className="px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-800 disabled:text-gray-600 rounded-xl text-sm font-medium">
              {creating ? (isEdit ? '保存中...' : '创建中...') : (isEdit ? '保存修改' : '确认创建')}
            </button>
          </div>
        </div>
        {/* 全画布节点 ID 自动补全（分支目标 / 错误目标 / 并行目标输入框共用） */}
        <datalist id="cvNodeOptions">
          {nodes.map(n => (
            <option key={n.id} value={n.id}>{n.display_name || n.id}</option>
          ))}
        </datalist>
      </div>
    </div>
  );
}

function NodeConfigForm({ node, onChange, agents, allNodes }: {
  node: CanvasNode;
  onChange: (u: Partial<CanvasNode>) => void;
  agents: AgentMetadata[];
  allNodes?: CanvasNode[];
}) {
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
      {(node.type === 'decision' || node.type === 'ai_decision') && (
        <div className="space-y-1.5">
          <span className="text-[10px] text-gray-500">{node.type === 'ai_decision' ? 'AI 决策路由' : '条件分支路由'}</span>
          <p className="text-[9px] text-gray-600 leading-relaxed">条件为空的分支作为兜底（勾选“默认”）。也可在画布从本节点输出端口连到目标节点自动生成分支。</p>
          <BranchesEditor node={node} onChange={onChange} allNodes={allNodes} />
        </div>
      )}
      {node.type === 'ai_decision' && (
        <>
          <label className="block">
            <span className="text-[10px] text-gray-500">决策模型（留空用流水线默认模型）</span>
            <input type="text" value={node.decision_model || ''} onChange={e => onChange({ decision_model: e.target.value })}
              placeholder="如 deepseek-chat / gpt-4o"
              className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 font-mono outline-none focus:border-blue-500" />
          </label>
          <label className="block">
            <span className="text-[10px] text-gray-500">AI 决策指令</span>
            <textarea value={node.decision_prompt || ''} onChange={e => onChange({ decision_prompt: e.target.value })}
              rows={3} className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500 resize-none"
              placeholder={'判断输入后输出与某个分支标签（label）一致的短词，例如 verdict: blocked'} />
          </label>
        </>
      )}
      {node.type === 'parallel' && (
        <>
          <label className="block">
            <span className="text-[10px] text-gray-500">并行子分支目标ID（逗号分隔）</span>
            <input list="cvNodeOptions" type="text" value={(node.parallel_branches || []).join(', ')}
              onChange={e => onChange({ parallel_branches: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
              className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500" />
            <p className="text-[9px] text-gray-600 mt-0.5">在画布上从「并行」节点输出端口分别连到子分支节点，会自动加入此列表。</p>
          </label>
          <label className="block">
            <span className="text-[10px] text-gray-500">最大并发数</span>
            <input type="number" min={1} max={10} value={node.max_concurrency ?? ''}
              onChange={e => onChange({ max_concurrency: e.target.value === '' ? undefined : Math.max(1, Math.min(10, Math.floor(Number(e.target.value)))) })}
              className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500" />
          </label>
          <label className="block">
            <span className="text-[10px] text-gray-500">并行收尾后 → 下一节点</span>
            <input list="cvNodeOptions" type="text" value={node.target || ''}
              onChange={e => onChange({ target: e.target.value })}
              placeholder="不填则按模板顺序寻找后续节点"
              className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 font-mono outline-none focus:border-blue-500" />
          </label>
          <label className="block">
            <span className="text-[10px] text-gray-500">合并策略</span>
            <select value={node.merge_strategy || 'all'} onChange={e => onChange({ merge_strategy: e.target.value })}
              className="w-full mt-0.5 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500">
              <option value="all">等待全部完成 (all)</option>
              <option value="first">最快返回 (first)</option>
            </select>
          </label>
        </>
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
              onChange={e => {
                const opts = e.target.value.split(',').map(s => s.trim()).filter(Boolean);
                const cb = { ...(node.confirm_branches || {}) };
                (node.confirm_options || []).forEach(o => { if (!opts.includes(o)) delete cb[o]; });
                onChange({ confirm_options: opts, confirm_branches: cb });
              }}
              className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500" />
          </label>
          {(node.confirm_options || []).length > 0 && (
            <div className="space-y-1">
              <span className="text-[10px] text-gray-500">选项 → 跳转目标（人工选择后路由）</span>
              {(node.confirm_options || []).map((opt, i) => (
                <div key={`${opt}-${i}`} className="flex items-center gap-1">
                  <span className="text-[10px] text-gray-300 w-20 truncate shrink-0">{opt}</span>
                  <input list="cvNodeOptions" type="text" value={(node.confirm_branches || {})[opt] || ''}
                    onChange={e => onChange({ confirm_branches: { ...(node.confirm_branches || {}), [opt]: e.target.value } })}
                    placeholder="目标节点ID"
                    className="flex-1 px-2 py-0.5 bg-gray-800 border border-gray-700 rounded text-[10px] text-gray-200 font-mono outline-none focus:border-blue-500" />
                </div>
              ))}
            </div>
          )}
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
              placeholder="在「数据接收器」页创建后填入 ID" />
          </label>
          <p className="text-[9px] text-gray-600">
            输入在「流水线 → 数据接收器」页创建的接收器 ID。流水线运行时将从此接收器拉取数据。
          </p>
        </>
      )}
      {node.type === 'end' && (
        <label className="block">
          <span className="text-[10px] text-gray-500">对话处置（运行到本节点时）</span>
          <select value={node.save_dialog ? 'save' : 'discard'}
            onChange={e => onChange({ save_dialog: e.target.value === 'save' })}
            className="w-full mt-0.5 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500">
            <option value="discard">回收删除（不保存本次对话）</option>
            <option value="save">保留归档（保存本次对话）</option>
          </select>
          <p className="text-[9px] text-gray-600 mt-1">流水线对话仅保留在运行历史中，不进入对话/会话管理。</p>
        </label>
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
      {/* 失败处理 / 超时 / 重试（对所有节点生效；end 由自身处置策略控制） */}
      {node.type !== 'end' && (
        <div className="pt-2 border-t border-gray-800 space-y-2.5">
          <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">失败处理 / 执行参数</span>
          <label className="block">
            <span className="text-[10px] text-gray-500">失败策略</span>
            <select
              value={node.ignore_error ? 'ignore' : (node.error_target ? 'route' : 'default')}
              onChange={e => {
                const v = e.target.value;
                if (v === 'ignore') onChange({ ignore_error: true, error_target: undefined, on_error: undefined });
                else if (v === 'route') onChange({ ignore_error: false, error_target: node.error_target || '', on_error: undefined });
                else onChange({ ignore_error: false, error_target: undefined, on_error: undefined });
              }}
              className="w-full mt-0.5 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500">
              <option value="default">失败即整体失败（可配合重试）</option>
              <option value="route">失败时路由到错误处理节点</option>
              <option value="ignore">忽略错误，记录后继续正常流程</option>
            </select>
          </label>
          {!node.ignore_error && node.error_target && (
            <label className="block">
              <span className="text-[10px] text-gray-500">错误处理节点 ID</span>
              <input list="cvNodeOptions" type="text" value={node.error_target || ''}
                onChange={e => onChange({ error_target: e.target.value })}
                placeholder="填写一个节点的 ID（如 handoff）"
                className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 font-mono outline-none focus:border-blue-500" />
              <p className="text-[9px] text-gray-600 mt-0.5">节点执行异常（重试耗尽后）将从正常连线改走该分支；路由器与其它条件分支互斥。</p>
            </label>
          )}
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="text-[10px] text-gray-500">失败重试次数</span>
              <input type="number" min={0} max={5} value={node.max_retries ?? ''}
                onChange={e => onChange({ max_retries: e.target.value === '' ? undefined : Math.max(0, Math.min(5, Math.floor(Number(e.target.value)))) })}
                placeholder="0"
                className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500" />
            </label>
            <label className="block">
              <span className="text-[10px] text-gray-500">执行超时（秒）</span>
              <input type="number" min={1} max={3600} value={node.timeout_seconds ?? ''}
                onChange={e => onChange({ timeout_seconds: e.target.value === '' ? undefined : Math.max(1, Math.min(3600, Math.floor(Number(e.target.value)))) })}
                placeholder="不限制"
                className="w-full mt-0.5 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 outline-none focus:border-blue-500" />
            </label>
          </div>
        </div>
      )}
    </div>
  );
}

function BranchesEditor({ node, onChange, allNodes }: {
  node: CanvasNode;
  onChange: (u: Partial<CanvasNode>) => void;
  allNodes?: CanvasNode[];
}) {
  const branches = node.branches || [];
  const isAi = node.type === 'ai_decision';
  function updateBranches(newB: typeof branches) { onChange({ branches: newB }); }
  function updateBranch(idx: number, patch: Record<string, string | boolean | undefined>) {
    const nb = branches.map((b, i) => i === idx ? { ...b, ...patch } : b);
    updateBranches(nb);
  }
  return (
    <div className="space-y-1.5 mt-1">
      {branches.length === 0 && (
        <p className="text-[10px] text-gray-600">暂无分支。在画布上从本节点输出端口（下方 ●）拖到目标节点会自动生成分支；或手动添加。</p>
      )}
      {branches.map((b, i) => {
        const target = b.goto || b.target || '';
        return (
          <div key={i} className="p-2 rounded bg-gray-800/50 border border-gray-700/60 space-y-1">
            <div className="flex gap-1 items-center">
              <input
                type="text"
                value={isAi ? (b.label || '') : (b.condition || '')}
                onChange={e => {
                  if (isAi) updateBranch(i, { label: e.target.value, condition: undefined });
                  else updateBranch(i, { condition: e.target.value, label: e.target.value });
                }}
                placeholder={isAi ? '分支标签/提示（LLM 判断依据，如 blocked）' : '条件表达式 (如 status==ok)'}
                className="flex-1 px-2 py-0.5 bg-gray-700 border border-gray-600 rounded text-[10px] text-gray-200 font-mono outline-none focus:border-blue-400" />
              <button onClick={() => updateBranches(branches.filter((_, j) => j !== i))} title="删除该分支"
                className="p-1 rounded text-gray-500 hover:text-red-400 hover:bg-red-500/10">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24"><path strokeLinecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
              </button>
            </div>
            {isAi && (
              <input type="text" value={b.condition || ''} onChange={e => updateBranch(i, { condition: e.target.value })}
                placeholder="额外路由条件（可选，如包含 IP）"
                className="w-full px-2 py-0.5 bg-gray-700 border border-gray-600 rounded text-[10px] text-gray-200 font-mono outline-none focus:border-blue-400" />
            )}
            <div className="flex gap-1">
              <input list="cvNodeOptions" type="text" value={target}
                onChange={e => { updateBranch(i, { goto: e.target.value, target: e.target.value }); }}
                placeholder="跳转目标节点ID"
                className="flex-1 px-2 py-0.5 bg-gray-700 border border-gray-600 rounded text-[10px] text-gray-200 font-mono outline-none focus:border-blue-400" />
              <label className="flex items-center gap-1 text-[9px] text-gray-400 shrink-0" title="勾选后无匹配/异常时走此分支">
                <input type="checkbox" checked={!!b.default}
                  onChange={e => updateBranches(branches.map((bb, j) => j === i ? { ...bb, default: e.target.checked } : (bb.default ? { ...bb, default: false } : bb)))} />
                默认
              </label>
            </div>
          </div>
        );
      })}
      <button onClick={() => updateBranches([...branches, { condition: '', label: '', goto: '', target: '', default: branches.length === 0 }])}
        className="w-full text-[10px] py-1 bg-gray-800 border border-gray-700 rounded text-gray-400 hover:text-gray-200 hover:border-gray-500">+ 添加分支</button>
    </div>
  );
}

/* ================================================================
   管理模块四页签子组件
   ================================================================ */

const RECEIVER_KIND_LABEL: Record<string, string> = {
  webhook: 'Webhook',
  syslog: 'Syslog 收集',
  file: '文件监听',
  watch: '文件监听',
};

/** 时间格式化：兼容秒级时间戳(number)与 ISO 字符串 */
function fmtTime(ts?: number | string | null): string {
  if (ts === null || ts === undefined || ts === '') return '-';
  const d = new Date(ts);
  return isNaN(d.getTime()) ? '-' : d.toLocaleString();
}

function shortId(s?: string | null, n = 8): string {
  if (!s) return '-';
  return s.length <= n ? s : s.slice(0, n) + '…';
}

/* ------------------------------------------------------------------
   InstancesTab — 流水线实例（左列表 + 右详情/编辑）
   ------------------------------------------------------------------ */
function InstancesTab(props: {
  instances: PipelineInstance[];
  templates: UnifiedPipeline[];
  receivers: ReceiverConfig[];
  selected: PipelineInstance | null;
  status: InstanceStatus | null;
  instRuns: RunRow[];
  editing: boolean;
  editData: Partial<PipelineInstance>;
  setEditData: (d: Partial<PipelineInstance>) => void;
  onSelect: (inst: PipelineInstance) => void;
  onToggle: (inst: PipelineInstance) => void;
  onSync: (inst: PipelineInstance) => void;
  onDelete: (inst: PipelineInstance) => void;
  onEdit: (inst: PipelineInstance) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onTest: (inst: PipelineInstance) => void;
  onOpenRun: (runId: string) => void;
  onOpenRunsHistory: () => void;
  templateOf: (inst: PipelineInstance) => UnifiedPipeline | null;
}) {
  const receiverName = (id: string | undefined | null) =>
    id ? (props.receivers.find(r => r.id === id)?.name || '') : '';

  const runRows = props.instRuns;
  const sel = props.selected;

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)] gap-4 items-start">
      {/* 左侧列表 */}
      <div className="space-y-2">
        <div className="flex items-center justify-between px-1">
          <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">流水线实例 ({props.instances.length})</span>
        </div>
        {props.instances.length === 0 ? (
          <div className="text-sm text-slate-400 border border-dashed border-slate-300 dark:border-slate-700 rounded-xl p-6 text-center leading-6">
            还没有流水线实例。
            <br />点击右上角「+ 新建流水线」从模板创建。
          </div>
        ) : (
          <div className="space-y-2 max-h-[72vh] overflow-y-auto pr-1">
            {props.instances.map(inst => {
              const active = sel?.id === inst.id;
              const rn = receiverName(inst.receiver_id);
              return (
                <div key={inst.id}
                  onClick={() => props.onSelect(inst)}
                  className={`rounded-xl border p-3 cursor-pointer transition-all ${
                    active
                      ? 'border-emerald-400 ring-1 ring-emerald-400/30 bg-emerald-50/60 dark:bg-emerald-900/10'
                      : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 hover:border-slate-300 dark:hover:border-slate-600'
                  }`}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium text-slate-800 dark:text-slate-200 truncate">{inst.name}</div>
                    <span className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full font-medium ${
                      inst.enabled
                        ? 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400'
                        : 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400'
                    }`}>
                      {inst.enabled ? '已启用' : '已停用'}
                    </span>
                  </div>
                  <div className="mt-1 text-[11px] text-slate-400 truncate">
                    模板: {inst.template_name || inst.template_id || '-'}
                    {inst.template_deleted ? <span className="text-amber-500 ml-1">(已删)</span> : null}
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-400 flex items-center justify-between gap-2">
                    <span className="truncate">{rn ? `接收器: ${rn}` : '未绑定接收器'}</span>
                    <span className="shrink-0 text-slate-300 dark:text-slate-600">并发 {inst.max_concurrency}</span>
                  </div>
                  <div className="mt-2 flex items-center gap-1.5">
                    <button
                      onClick={e => { e.stopPropagation(); props.onToggle(inst); }}
                      className={`px-2 py-1 text-[10px] rounded-md font-medium ${
                        inst.enabled
                          ? 'bg-amber-100 text-amber-600 hover:bg-amber-200 dark:bg-amber-900/20 dark:text-amber-400'
                          : 'bg-emerald-100 text-emerald-600 hover:bg-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-400'
                      }`}>
                      {inst.enabled ? '停用' : '启用'}
                    </button>
                    <button onClick={e => { e.stopPropagation(); props.onTest(inst); }}
                      className="px-2 py-1 text-[10px] rounded-md font-medium bg-blue-100 text-blue-600 hover:bg-blue-200 dark:bg-blue-900/20 dark:text-blue-400">
                      测试
                    </button>
                    <button onClick={e => { e.stopPropagation(); props.onDelete(inst); }}
                      className="ml-auto px-2 py-1 text-[10px] rounded-md font-medium text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20">
                      删除
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 右侧详情 */}
      <div className="min-w-0 space-y-4">
        {!sel ? (
          <div className="border border-dashed border-slate-300 dark:border-slate-700 rounded-xl p-12 text-center text-sm text-slate-400">
            在左侧选择一条流水线实例查看详情
          </div>
        ) : props.editing ? (
          /* ---- 编辑模式 ---- */
          <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-5 space-y-4">
            <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">编辑实例: {sel.name}</h3>
            <label className="block">
              <span className="text-xs font-medium text-slate-500 dark:text-slate-400">名称</span>
              <input type="text" value={props.editData.name || ''}
                onChange={e => props.setEditData({ ...props.editData, name: e.target.value })}
                className="mt-1 w-full px-3 py-2 text-sm border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100" />
            </label>
            <label className="block">
              <span className="text-xs font-medium text-slate-500 dark:text-slate-400">描述</span>
              <textarea value={props.editData.description || ''} rows={3}
                onChange={e => props.setEditData({ ...props.editData, description: e.target.value })}
                className="mt-1 w-full px-3 py-2 text-sm border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 resize-none" />
            </label>
            <label className="block">
              <span className="text-xs font-medium text-slate-500 dark:text-slate-400">绑定接收器</span>
              <select value={props.editData.receiver_id || ''}
                onChange={e => props.setEditData({ ...props.editData, receiver_id: e.target.value })}
                className="mt-1 w-full px-3 py-2 text-sm border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100">
                <option value="">不绑定（可手动触发）</option>
                {props.receivers.map(r => (
                  <option key={r.id} value={r.id}>{r.name}（{RECEIVER_KIND_LABEL[r.kind] || r.kind}）{r.enabled ? '' : '· 已停用'}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-xs font-medium text-slate-500 dark:text-slate-400">并行上限（1-50，默认 10）</span>
              <input type="number" min={1} max={50}
                value={props.editData.max_concurrency ?? 10}
                onChange={e => props.setEditData({ ...props.editData, max_concurrency: Math.max(1, Math.min(50, parseInt(e.target.value, 10) || 10)) })}
                className="mt-1 w-32 px-3 py-2 text-sm border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100" />
            </label>
            <div className="flex gap-2 pt-1">
              <button onClick={props.onSaveEdit} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700">保存</button>
              <button onClick={props.onCancelEdit} className="px-4 py-2 text-sm bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-200 rounded-lg">取消</button>
            </div>
          </div>
        ) : (
          <>
            {/* 操作区 */}
            <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-4 flex flex-wrap items-center gap-2">
              <div className="mr-auto min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold text-slate-900 dark:text-slate-100 truncate">{sel.name}</span>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${sel.enabled ? 'bg-emerald-100 text-emerald-600' : 'bg-slate-100 text-slate-500'}`}>
                    {sel.enabled ? '运行中' : '已停用'}
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] text-slate-400 flex gap-2 flex-wrap">
                  <span>来自模板: {sel.template_name || shortId(sel.template_id, 12)}</span>
                  <span>接收器: {receiverName(sel.receiver_id) || '未绑定'}</span>
                  <span>并行上限: {sel.max_concurrency}</span>
                </div>
              </div>
              {!sel.enabled && (
                <button onClick={() => props.onToggle(sel)} className="px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-600 text-white hover:bg-emerald-700">
                  启用
                </button>
              )}
              <button onClick={() => props.onEdit(sel)} className="px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-200 hover:bg-slate-200">编辑</button>
              <button onClick={() => props.onSync(sel)} className="px-3 py-1.5 text-xs font-medium rounded-lg bg-indigo-100 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 hover:bg-indigo-200">同步到模板</button>
              <button onClick={() => props.onTest(sel)} className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700">测试投递</button>
              <button onClick={() => props.onDelete(sel)} className="px-3 py-1.5 text-xs font-medium rounded-lg text-red-500 bg-red-50 dark:bg-red-900/20 hover:bg-red-100">删除</button>
            </div>

            {/* 运行状态 */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: '并行消费中', value: props.status ? `${props.status.active_count ?? 0}/${props.status.max_concurrency ?? sel.max_concurrency}` : '—', sub: props.status?.enabled || sel.enabled ? '消费中' : '未启用' },
                { label: '累计处理', value: String(props.status?.processed ?? 0), sub: '完成入站' },
                { label: '失败', value: String(props.status?.failed ?? 0), sub: props.status?.dead ? `失效 ${props.status.dead}` : '' },
                { label: '最近运行', value: props.status?.last_run_at ? fmtTime(props.status.last_run_at) : '—', sub: '—' },
              ].map(s => (
                <div key={s.label} className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-3">
                  <div className="text-[10px] text-slate-400">{s.label}</div>
                  <div className="text-base font-semibold text-slate-800 dark:text-slate-100 truncate mt-0.5" title={s.value}>{s.value}</div>
                  <div className="text-[10px] text-slate-400">{s.sub}</div>
                </div>
              ))}
            </div>
            {props.status?.last_error && (
              <div className="text-xs text-red-500 bg-red-50 dark:bg-red-900/20 border border-red-100 dark:border-red-900/40 rounded-lg px-3 py-2 break-all">
                {props.status.last_error}
              </div>
            )}

            {/* 模板定义图 */}
            {(() => {
              const tpl = props.templateOf(sel);
              if (!tpl) return null;
              return <PipelineDetailPanel pipeline={tpl} hideActions />;
            })()}

            {/* 该实例最近运行 */}
            <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">最近对话执行（{runRows.length}）</span>
                <button onClick={props.onOpenRunsHistory} className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline">查看全部历史 →</button>
              </div>
              {runRows.length === 0 ? (
                <div className="text-xs text-slate-400 py-4 text-center border border-dashed border-slate-200 dark:border-slate-700 rounded-lg">
                  暂无执行记录。启用后用「测试投递」发送一条入站数据，或等待接收器数据。
                </div>
              ) : (
                <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1">
                  {runRows.map(run => (
                    <div key={run.run_id} onClick={() => props.onOpenRun(run.run_id)}
                      className="flex items-center gap-3 px-3 py-2 rounded-lg border border-slate-100 dark:border-slate-700 cursor-pointer hover:border-blue-200 dark:hover:border-blue-800 bg-slate-50 dark:bg-slate-900/40">
                      <RunStatusBadge status={run.status} />
                      <span className="text-[11px] font-mono text-slate-500 dark:text-slate-400">{shortId(run.run_id, 12)}</span>
                      <span className="text-[11px] text-slate-400 hidden sm:inline">{fmtTime(run.created_at)}</span>
                      <span className="ml-auto flex items-center gap-1 text-[11px] text-slate-400">
                        {run.dialog_count != null && (
                          <span title={`${run.dialog_count} 条对话`}>对话 {run.dialog_count}</span>
                        )}
                        {run.dialog_retained && <span className="text-emerald-500">·保留</span>}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   TemplatesTab — 模板库（内置 + 自定义）
   ------------------------------------------------------------------ */
function TemplatesTab(props: {
  templates: UnifiedPipeline[];
  selected: UnifiedPipeline | null;
  onSelect: (t: UnifiedPipeline) => void;
  onStartEdit: (t: UnifiedPipeline) => void;
  onDelete: (t: UnifiedPipeline) => void;
  onCreateInstance: (t: UnifiedPipeline) => void;
  onOpenCreate: () => void;
  onOpenRun: (runId: string) => void;
}) {
  const sel = props.selected;
  return (
    <div className="grid grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)] gap-4 items-start">
      {/* 左侧模板列表 */}
      <div className="space-y-2">
        <div className="flex items-center justify-between px-1">
          <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">模板库 ({props.templates.length})</span>
          <span className="text-[10px] text-slate-400">内置可直接使用 · 自定义可编辑</span>
        </div>
        {props.templates.length === 0 ? (
          <div className="text-sm text-slate-400 border border-dashed border-slate-300 dark:border-slate-700 rounded-xl p-6 text-center leading-6">
            暂无模板。
            <br />点击「+ 新建模板」通过图编排创建。
          </div>
        ) : (
          <div className="space-y-2 max-h-[72vh] overflow-y-auto pr-1">
            {props.templates.map(t => {
              const active = sel?.id === t.id;
              return (
                <div key={t.id} onClick={() => props.onSelect(t)}
                  className={`rounded-xl border p-3 cursor-pointer transition-all ${
                    active
                      ? 'border-blue-400 ring-1 ring-blue-400/30 bg-blue-50/60 dark:bg-blue-900/10'
                      : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 hover:border-slate-300 dark:hover:border-slate-600'
                  }`}>
                  <div className="flex items-center gap-2">
                    <div className="text-sm font-medium text-slate-800 dark:text-slate-200 truncate">{t.name}</div>
                    <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                      t.source === 'custom'
                        ? 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400'
                        : 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400'
                    }`}>
                      {t.source === 'custom' ? '自定义' : '内置'}
                    </span>
                    <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                      t.type === 'auto' ? 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30' : 'bg-amber-100 text-amber-600 dark:bg-amber-900/30'
                    }`}>
                      {t.type === 'auto' ? '自动' : '人工介入'}
                    </span>
                  </div>
                  <div className="mt-1 text-[11px] text-slate-400 line-clamp-1">{t.description || '无描述'}</div>
                  <div className="mt-1 text-[11px] text-slate-300 dark:text-slate-600">{(t.nodes || []).length} 节点 · {(t.edges || []).length} 连线</div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 右侧详情 */}
      <div className="min-w-0 space-y-4">
        {!sel ? (
          <div className="border border-dashed border-slate-300 dark:border-slate-700 rounded-xl p-12 text-center">
            <div className="text-sm text-slate-400">在左侧选择模板查看定义，或</div>
            <button onClick={props.onOpenCreate} className="mt-3 px-4 py-2 text-sm bg-blue-600 text-white rounded-xl hover:bg-blue-700">
              通过图编排新建模板
            </button>
          </div>
        ) : (
          <>
            <PipelineDetailPanel
              pipeline={sel}
              isCustom={sel.source === 'custom'}
              onEdit={sel.source === 'custom' ? () => props.onStartEdit(sel) : undefined}
              onPrimary={() => props.onCreateInstance(sel)}
              primaryLabel="由此模板创建流水线实例"
            />
            {/* 元信息 + 危险操作 */}
            <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-4 flex flex-wrap items-center gap-3">
              <div className="text-[11px] text-slate-400 mr-auto space-x-3">
                <span>ID: <span className="font-mono">{shortId(sel.id, 14)}</span></span>
                {sel.created_at ? <span>创建: {fmtTime(sel.created_at)}</span> : null}
                {sel.updated_at ? <span>更新: {fmtTime(sel.updated_at)}</span> : null}
              </div>
              {sel.source === 'custom' && (
                <button
                  onClick={() => props.onStartEdit(sel)}
                  className="px-3 py-1.5 text-xs font-medium rounded-lg text-blue-600 bg-blue-50 dark:bg-blue-900/20 hover:bg-blue-100"
                  title="在拖拽画布中重画/删除节点与连线"
                >
                  画布编辑
                </button>
              )}
              <button onClick={() => props.onDelete(sel)} className="px-3 py-1.5 text-xs font-medium rounded-lg text-red-500 bg-red-50 dark:bg-red-900/20 hover:bg-red-100">
                {sel.source === 'custom' ? '删除模板' : '隐藏内置模板'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   ReceiversTab — 数据接收器（自设置页迁入）
   ------------------------------------------------------------------ */
function ReceiversTab(props: {
  receivers: ReceiverConfig[];
  instances: PipelineInstance[];
  onOpenCreate: () => void;
  onOpenEdit: (r: ReceiverConfig) => void;
  onToggle: (r: ReceiverConfig) => void;
  onDelete: (r: ReceiverConfig) => void;
  bindingsOf: (r: ReceiverConfig) => PipelineInstance[];
  onGotoInstance: (i: PipelineInstance) => void;
}) {
  const rc = props.receivers;
  if (rc.length === 0) {
    return (
      <div className="border border-dashed border-slate-300 dark:border-slate-700 rounded-xl p-12 text-center">
        <div className="text-sm text-slate-400 leading-6">
          还没有数据接收器。<br />
          创建 Webhook / Syslog / 文件监听接收器后，再在流水线实例上绑定它，即可自动消费入站数据。
        </div>
        <button onClick={props.onOpenCreate} className="mt-4 px-4 py-2 text-sm bg-cyan-600 text-white rounded-xl hover:bg-cyan-500">
          + 新建接收器
        </button>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between px-1">
        <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">数据接收器 ({rc.length})</span>
        <span className="text-[10px] text-slate-400">入站数据进入绑定流水线的对话流</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {rc.map(r => {
          const usedBy = props.bindingsOf(r);
          const kindColor: Record<string, string> = {
            webhook: '#06b6d4', syslog: '#8b5cf6', watch: '#f59e0b',
          };
          const color = kindColor[r.kind] || '#64748b';
          return (
            <div key={r.id} className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-4 flex flex-col gap-3">
              {/* 头 */}
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0" style={{ backgroundColor: color + '20' }}>
                  <svg viewBox="0 0 24 24" className="w-5 h-5" style={{ color }}><path d={ICONS.receiver} fill="currentColor" /></svg>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold text-slate-800 dark:text-slate-200 truncate">{r.name}</div>
                  <div className="text-[10px] text-slate-400 flex items-center gap-1.5">
                    <span style={{ color }}>{RECEIVER_KIND_LABEL[r.kind] || r.kind}</span>
                    <span>· {shortId(r.id, 10)}</span>
                  </div>
                </div>
                {/* 开关 */}
                <button onClick={() => props.onToggle(r)} title={r.enabled ? '停用' : '启用'}
                  className={`relative w-9 h-5 rounded-full transition-colors ${r.enabled ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-600'}`}>
                  <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${r.enabled ? 'left-4.5 translate-x-0' : 'left-0.5'}`} style={{ left: r.enabled ? 18 : 2 }} />
                </button>
              </div>

              {/* 接入信息 */}
              <div className="text-[11px] space-y-1 bg-slate-50 dark:bg-slate-900/50 rounded-lg p-2.5 border border-slate-100 dark:border-slate-700">
                {r.kind === 'webhook' && (
                  <>
                    <div className="text-slate-400">POST JSON 到</div>
                    <div className="font-mono text-slate-600 dark:text-slate-300 break-all">/api/v1/hook/{r.webhook_path || '...'}</div>
                    <div className="text-slate-400">字段: text / content / payload</div>
                  </>
                )}
                {r.kind === 'syslog' && (
                  <>
                    <div className="text-slate-400">监听地址</div>
                    <div className="font-mono text-slate-600 dark:text-slate-300">{(r.syslog_host || '0.0.0.0')}:{r.syslog_port || 514} {r.syslog_protocol || 'udp'}</div>
                  </>
                )}
                {(r.kind === 'file' || r.kind === 'watch') && (
                  <>
                    <div className="text-slate-400">监听目录</div>
                    <div className="font-mono text-slate-600 dark:text-slate-300 break-all">{r.watch_dir || '—'}（{r.watch_patterns || '*'}）</div>
                  </>
                )}
              </div>

              {/* 统计 */}
              <div className="flex items-center gap-3 text-[11px] text-slate-400">
                <span>累计 <b className="text-slate-600 dark:text-slate-300">{r.total_received ?? 0}</b></span>
                <span>排队 <b className="text-slate-600 dark:text-slate-300">{r.queue_size ?? 0}</b></span>
                <span className="ml-auto truncate">最近 {fmtTime(r.last_received_at)}</span>
              </div>

              {/* 绑定流水线 */}
              <div className="space-y-1 min-h-0">
                {usedBy.length === 0 ? (
                  <div className="text-[11px] text-slate-400 border border-dashed border-slate-200 dark:border-slate-700 rounded-lg px-2 py-1.5 text-center">
                    未绑定流水线实例
                  </div>
                ) : (
                  usedBy.map(inst => (
                    <div key={inst.id} className="flex items-center gap-2 px-2 py-1 rounded-lg bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/40">
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${inst.enabled ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                      <span className="text-[11px] text-slate-600 dark:text-slate-300 truncate">{inst.name}</span>
                      <span className="text-[10px] text-slate-400 shrink-0">{inst.enabled ? '消费中' : '未启用'}</span>
                      <button onClick={() => props.onGotoInstance(inst)} className="ml-auto text-[10px] text-blue-600 dark:text-blue-400 hover:underline shrink-0">打开</button>
                    </div>
                  ))
                )}
              </div>

              {/* 操作 */}
              <div className="flex gap-2 mt-auto pt-1">
                <button onClick={() => props.onOpenEdit(r)} className="flex-1 px-2 py-1.5 text-[11px] font-medium rounded-lg bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200">
                  编辑
                </button>
                <button onClick={() => props.onDelete(r)} className="flex-1 px-2 py-1.5 text-[11px] font-medium rounded-lg text-red-500 bg-red-50 dark:bg-red-900/20 hover:bg-red-100">
                  删除
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   HistoryTab — 执行历史
   ------------------------------------------------------------------ */
function HistoryTab(props: {
  runs: RunRow[];
  onRefresh: () => void;
  onOpenRun: (runId: string) => void;
}) {
  const runs = props.runs;
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 dark:border-slate-700">
        <div>
          <span className="text-sm font-semibold text-slate-800 dark:text-slate-200">执行历史</span>
          <span className="ml-2 text-[11px] text-slate-400">流水线实例 / 模板触发（最近 {runs.length}）</span>
        </div>
        <button onClick={props.onRefresh} className="px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200">
          刷新
        </button>
      </div>
      {runs.length === 0 ? (
        <div className="text-sm text-slate-400 py-12 text-center">暂无执行记录</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase text-slate-400 border-b border-slate-100 dark:border-slate-700">
                <th className="px-4 py-2 font-semibold">时间</th>
                <th className="px-3 py-2 font-semibold">流水线</th>
                <th className="px-3 py-2 font-semibold">Run ID</th>
                <th className="px-3 py-2 font-semibold">状态</th>
                <th className="px-3 py-2 font-semibold text-right">事件</th>
                <th className="px-3 py-2 font-semibold text-right">对话</th>
                <th className="px-4 py-2 font-semibold">备注</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(run => (
                <tr key={run.run_id} onClick={() => props.onOpenRun(run.run_id)}
                  className="border-b border-slate-50 dark:border-slate-700/60 cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-700/40 transition-colors">
                  <td className="px-4 py-2 text-[11px] text-slate-500 dark:text-slate-400 whitespace-nowrap">{fmtTime(run.created_at)}</td>
                  <td className="px-3 py-2 text-xs text-slate-700 dark:text-slate-300 max-w-[180px] truncate">
                    {run.pipeline_name || '模板执行'}
                    <span className="text-slate-400 block font-mono text-[10px]">{shortId(run.pipeline_id, 16)}</span>
                  </td>
                  <td className="px-3 py-2 text-[11px] font-mono text-slate-500">{shortId(run.run_id, 14)}</td>
                  <td className="px-3 py-2"><RunStatusBadge status={run.status} /></td>
                  <td className="px-3 py-2 text-right text-xs text-slate-500">{run.events_count ?? 0}</td>
                  <td className="px-3 py-2 text-right">
                    {run.dialog_count != null ? (
                      <span className="text-[11px] text-slate-600 dark:text-slate-300">
                        {run.dialog_count}
                        {run.dialog_retained ? <span className="ml-1 text-[10px] text-emerald-500 font-medium">保留</span> : <span className="ml-1 text-[10px] text-slate-400">回收</span>}
                      </span>
                    ) : (
                      <span className="text-[11px] text-slate-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-[11px] text-red-500 max-w-[160px] truncate" title={run.error || ''}>
                    {run.error || <span className="text-slate-300 dark:text-slate-600">点击查看对话</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ================================================================
   CreateInstanceModal — 从模板创建流水线实例（模板图快照进实例）
   ================================================================ */
interface ReceiverFormState {
  name: string;
  kind: string;
  webhook_path: string;
  syslog_port: number;
  syslog_host: string;
  watch_dir: string;
  watch_patterns: string;
}

function CreateInstanceModal(props: {
  templates: UnifiedPipeline[];
  receivers: ReceiverConfig[];
  presetTemplateId?: string | null;
  onClose: () => void;
  onCreated: (inst: PipelineInstance) => void;
  showFeedback: (type: 'ok' | 'err', msg: string) => void;
}) {
  const [templateId, setTemplateId] = useState(
    props.presetTemplateId && props.templates.some(t => t.id === props.presetTemplateId)
      ? props.presetTemplateId
      : (props.templates[0]?.id || ''),
  );
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [receiverId, setReceiverId] = useState('');
  const [maxConcurrency, setMaxConcurrency] = useState(10);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (props.presetTemplateId && props.templates.some(t => t.id === props.presetTemplateId)) {
      setTemplateId(props.presetTemplateId);
    }
  }, [props.presetTemplateId, props.templates]);

  const template = props.templates.find(t => t.id === templateId) || null;

  async function submit() {
    if (!templateId) { props.showFeedback('err', '请先选择要创建的模板'); return; }
    setSaving(true);
    try {
      const res = await api.createInstance({
        template_id: templateId,
        name: name.trim() || undefined,
        description: description.trim() || undefined,
        receiver_id: receiverId || undefined,
        max_concurrency: Math.max(1, Math.min(50, maxConcurrency || 10)),
      });
      props.onCreated(res.instance);
    } catch (e: any) {
      props.showFeedback('err', '创建流水线失败: ' + (e.message || ''));
    } finally {
      setSaving(false);
    }
  }

  const inputCls = 'w-full px-3 py-2 text-sm border border-gray-700 rounded-lg bg-gray-800 text-gray-200 outline-none focus:border-blue-500';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={props.onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-2xl p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-base font-semibold text-slate-100">从模板创建流水线实例</h3>
          <button onClick={props.onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
          </button>
        </div>
        <p className="text-[11px] text-gray-500 mb-4">
          模板的图定义会快照进实例，之后模板的修改不会影响已创建的实例。
        </p>

        {props.templates.length === 0 ? (
          <div className="text-sm text-gray-400 py-8 text-center border border-dashed border-gray-700 rounded-xl">
            暂无可用模板，请先在「模板库」创建。
          </div>
        ) : (
          <div className="space-y-4">
            <label className="block">
              <span className="text-xs font-medium text-gray-400">模板</span>
              <select value={templateId} onChange={e => setTemplateId(e.target.value)} className={inputCls + ' mt-1'}>
                <option value="">-- 选择模板 --</option>
                {props.templates.map(t => (
                  <option key={t.id} value={t.id}>
                    {t.name}（{t.source === 'custom' ? '自定义' : '内置'} · {t.type === 'auto' ? '自动' : '人工介入'}）
                  </option>
                ))}
              </select>
              {template && (
                <span className="block text-[10px] text-gray-500 mt-1">
                  {template.description || '无描述'} · {(template.nodes || []).length} 节点
                </span>
              )}
            </label>

            <label className="block">
              <span className="text-xs font-medium text-gray-400">名称（留空用模板名）</span>
              <input type="text" value={name} onChange={e => setName(e.target.value)}
                placeholder={template?.name || '流水线实例名称'} className={inputCls + ' mt-1'} />
            </label>

            <label className="block">
              <span className="text-xs font-medium text-gray-400">描述</span>
              <textarea value={description} rows={2} onChange={e => setDescription(e.target.value)}
                className={inputCls + ' mt-1 resize-none'} placeholder="可选" />
            </label>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs font-medium text-gray-400">绑定接收器</span>
                <select value={receiverId} onChange={e => setReceiverId(e.target.value)} className={inputCls + ' mt-1'}>
                  <option value="">不绑定</option>
                  {props.receivers.map(r => (
                    <option key={r.id} value={r.id}>{r.name}（{RECEIVER_KIND_LABEL[r.kind] || r.kind}）</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-gray-400">并行上限（默认 10）</span>
                <input type="number" min={1} max={50} value={maxConcurrency}
                  onChange={e => setMaxConcurrency(Math.max(1, Math.min(50, parseInt(e.target.value, 10) || 10)))}
                  className={inputCls + ' mt-1'} />
              </label>
            </div>
            <p className="text-[10px] text-gray-500 leading-5">
              启用后，实例以最多「并行上限」条同时处理绑定接收器的入站数据；每条入站数据 = 一次独立 run/对话，
              对话保存在 run 历史中，不进入对话/会话管理。
            </p>

            <div className="flex gap-3 pt-1">
              <button onClick={submit} disabled={saving || !templateId}
                className="flex-1 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-xl text-sm font-medium text-white transition-all">
                {saving ? '创建中...' : '创建流水线实例'}
              </button>
              <button onClick={props.onClose} className="px-4 py-2.5 bg-gray-800 hover:bg-gray-700 rounded-xl text-sm text-gray-300">
                取消
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   ReceiverFormModal — 接收器创建/编辑表单（webhook / syslog / file）
   ------------------------------------------------------------------ */
function ReceiverFormModal(props: {
  editing: ReceiverConfig | null;
  form: ReceiverFormState;
  setForm: (fn: (prev: ReceiverFormState) => ReceiverFormState) => void;
  saving: boolean;
  onClose: () => void;
  onSave: () => void;
}) {
  const { form } = props;
  const up = (patch: Partial<ReceiverFormState>) => props.setForm(p => ({ ...p, ...patch }));
  const inputCls = 'w-full px-3 py-2 text-sm border border-gray-700 rounded-lg bg-gray-800 text-gray-200 outline-none focus:border-cyan-500';
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={props.onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-2xl p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h3 className="text-base font-semibold text-slate-100 mb-4">{props.editing ? '编辑接收器' : '新建接收器'}</h3>
        <div className="space-y-3.5">
          <label className="block">
            <span className="text-xs font-medium text-gray-400">名称</span>
            <input type="text" value={form.name} onChange={e => up({ name: e.target.value })}
              className={inputCls + ' mt-1'} placeholder="如：SOC 告警接收器" />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-gray-400">协议类型</span>
            <select value={form.kind} onChange={e => up({ kind: e.target.value })} className={inputCls + ' mt-1'}>
              <option value="webhook">HTTP Webhook</option>
              <option value="syslog">Syslog (UDP)</option>
              <option value="file">文件监听</option>
            </select>
          </label>

          {form.kind === 'webhook' && (
            <label className="block">
              <span className="text-xs font-medium text-gray-400">Webhook 路径</span>
              <div className="flex items-center mt-1">
                <span className="text-xs text-gray-500 px-2.5 py-2 bg-gray-800 border border-r-0 border-gray-700 rounded-l-lg">/api/v1/hook/</span>
                <input type="text" value={form.webhook_path} onChange={e => up({ webhook_path: e.target.value })}
                  className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-r-lg text-sm text-gray-200 outline-none focus:border-cyan-500" placeholder="my-receiver" />
              </div>
              <span className="text-[10px] text-gray-500 block mt-1">外部系统 POST JSON 到此路径，字段支持 text / content / payload</span>
            </label>
          )}

          {form.kind === 'syslog' && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-xs font-medium text-gray-400">监听端口</span>
                  <input type="number" value={form.syslog_port} onChange={e => up({ syslog_port: Number(e.target.value) || 0 })}
                    className={inputCls + ' mt-1'} placeholder="514" />
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-gray-400">绑定地址</span>
                  <input type="text" value={form.syslog_host} onChange={e => up({ syslog_host: e.target.value })}
                    className={inputCls + ' mt-1'} placeholder="0.0.0.0" />
                </label>
              </div>
              <span className="text-[10px] text-gray-500 block">接收 Syslog 设备（如防火墙/IPS）的 UDP 告警。</span>
            </>
          )}

          {form.kind === 'file' && (
            <>
              <label className="block">
                <span className="text-xs font-medium text-gray-400">监听目录</span>
                <input type="text" value={form.watch_dir} onChange={e => up({ watch_dir: e.target.value })}
                  className={inputCls + ' mt-1'} placeholder="/var/reports/" />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-gray-400">文件匹配模式（逗号分隔）</span>
                <input type="text" value={form.watch_patterns} onChange={e => up({ watch_patterns: e.target.value })}
                  className={inputCls + ' mt-1'} placeholder="*.pdf,*.html,*.json" />
              </label>
              <span className="text-[10px] text-gray-500 block">监视目录内新出现的匹配文件，将作为一条入站数据进入流水线。</span>
            </>
          )}

          <p className="text-[10px] text-gray-500 leading-5">
            接收器只负责「接收 + 入队」。接收器从设置页迁移至流水线页管理；绑定到流水线实例并启用后，入站数据才会被并行消费。
          </p>
        </div>
        <div className="flex gap-3 mt-5">
          <button onClick={props.onSave} disabled={props.saving}
            className="flex-1 px-4 py-2.5 bg-cyan-600 hover:bg-cyan-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-xl text-sm font-medium text-white transition-all">
            {props.saving ? '保存中...' : (props.editing ? '更新' : '创建')}
          </button>
          <button onClick={props.onClose} className="px-4 py-2.5 bg-gray-800 hover:bg-gray-700 rounded-xl text-sm text-gray-300">
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   TestPipelineModal — 向流水线实例投递一条测试入站数据（走真实收件箱）
   ------------------------------------------------------------------ */
function TestPipelineModal(props: {
  instance: PipelineInstance;
  onClose: () => void;
  onFinished: () => void;
}) {
  const [text, setText] = useState('这是一条来自流水线「测试投递」的入站数据，请解析并给出结论。');
  const [asJson, setAsJson] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [result, setResult] = useState<{
    ok: boolean; instance_id?: string; receiver_id?: string; seq?: number;
    created?: boolean; enabled?: boolean; message?: string; error?: string;
  } | null>(null);

  async function submit() {
    if (!text.trim() && !asJson) return;
    setSending(true);
    setResult(null);
    let payload: unknown;
    if (asJson) {
      try { payload = JSON.parse(text); }
      catch { setSending(false); setResult({ ok: false, error: 'JSON 格式不正确，无法解析' }); return; }
    }
    try {
      const res = await api.testInstance(props.instance.id, asJson ? { payload } : { content: text });
      setResult(res);
      if (res.ok) setSent(true);
    } catch (e: any) {
      setResult({ ok: false, error: (e.message || '投递失败') });
    } finally {
      setSending(false);
    }
  }

  function close() {
    if (sent) props.onFinished();
    props.onClose();
  }

  const inputCls = 'w-full px-3 py-2 text-sm border border-gray-700 rounded-lg bg-gray-800 text-gray-200 outline-none focus:border-blue-500';
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={close}>
      <div className="bg-gray-900 border border-gray-700 rounded-2xl p-6 w-full max-w-xl mx-4 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-base font-semibold text-slate-100">测试投递</h3>
          <button onClick={close} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
          </button>
        </div>
        <p className="text-[11px] text-gray-500 mb-4">
          实例「{props.instance.name}」· 模板 {props.instance.template_name || props.instance.template_id} · 并行上限 {props.instance.max_concurrency}
          {props.instance.enabled
            ? <span className="text-emerald-400"> · 已启用，数据将即时消费</span>
            : <span className="text-amber-400"> · 未启用，数据先排队，启用后自动消费</span>}
        </p>

        <label className="block">
          <span className="text-xs font-medium text-gray-400">入站数据内容</span>
          <textarea value={text} rows={5} onChange={e => setText(e.target.value)}
            className={inputCls + ' mt-1 resize-y font-mono text-xs'} placeholder="输入要投递的内容；开启 JSON 模式后可投递结构化对象/数组" />
        </label>

        <label className="flex items-center gap-2 mt-2 text-xs text-gray-400">
          <input type="checkbox" checked={asJson} onChange={e => setAsJson(e.target.checked)} className="accent-blue-600" />
          按 JSON 解析并投递（payload）
        </label>

        {result && (
          <div className={`mt-3 px-3 py-2.5 rounded-lg text-xs leading-5 break-all ${
            result.ok
              ? 'bg-emerald-900/20 border border-emerald-700/40 text-emerald-300'
              : 'bg-red-900/20 border border-red-700/40 text-red-300'
          }`}>
            {result.ok ? (
              <>
                已入队，队列序号 seq=<b>{result.seq ?? '-'}</b>（receiver: {result.receiver_id || '-'}）。
                <br />
                {result.enabled === false
                  ? '实例当前未启用，数据将排队等待，启用后自动消费。'
                  : '实例已启用，将在并行会话中生成一次独立 run/对话，可在「执行历史 / 实例最近运行」查看。'}
              </>
            ) : (
              <>{result.error || result.message || '投递失败'}</>
            )}
          </div>
        )}

        <div className="flex gap-3 mt-4">
          <button onClick={submit} disabled={sending}
            className="flex-1 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-xl text-sm font-medium text-white transition-all">
            {sending ? '投递中...' : '投递测试数据'}
          </button>
          <button onClick={close} className="px-4 py-2.5 bg-gray-800 hover:bg-gray-700 rounded-xl text-sm text-gray-300">
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   RunDialogModal — run 详情查看：状态、摘要、报告与流水线对话实录
   ------------------------------------------------------------------ */
const DIALOG_KIND_STYLE: Record<string, string> = {
  input: 'bg-blue-900/20 border-blue-800/50 text-blue-100',
  llm: 'bg-gray-800/60 border-gray-700 text-gray-200',
  note: 'bg-slate-800/40 border-slate-700/50 text-slate-300',
};

function RunDialogModal(props: {
  loading: boolean;
  run: RunDetail | null;
  onClose: () => void;
}) {
  const { run } = props;
  const dialogEntries = (run?.dialog || []).filter(d => d.content || d.output || d.prompt);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={props.onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-3xl mx-4 max-h-[90vh] flex flex-col overflow-hidden" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <h3 className="text-base font-semibold text-slate-100">运行详情</h3>
            {run && <RunStatusBadge status={run.status} />}
            {run?.dialog_action === 'save' && <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-900/30 text-emerald-400">对话已保留</span>}
            {run?.dialog_action === 'discard' && <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-400">对话已回收</span>}
          </div>
          <button onClick={props.onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 shrink-0">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
          </button>
        </div>

        {props.loading || !run ? (
          <div className="flex items-center justify-center py-20 text-sm text-gray-500 animate-pulse">
            {props.loading ? '加载运行详情...' : '暂无数据'}
          </div>
        ) : (
          <div className="p-5 space-y-4 overflow-y-auto">
            {/* 元信息 */}
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-gray-400">
              <span>Run: <b className="font-mono text-gray-300">{run.run_id}</b></span>
              <span>流水线: <b className="font-mono text-gray-300">{run.pipeline_id}</b></span>
              <span>创建: {fmtTime(run.created_at)}</span>
              {run.started_at != null && <span>开始: {fmtTime(run.started_at)}</span>}
              {run.ended_at != null && <span>结束: {fmtTime(run.ended_at)}</span>}
              <span>事件: {run.events_count ?? (run.events || []).length}</span>
              {run.dialog_count != null && <span>对话条数: {run.dialog_count}</span>}
            </div>

            {run.error && (
              <div className="text-xs text-red-300 bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2 break-all whitespace-pre-wrap">
                {run.error}
              </div>
            )}

            {run.report && (
              <div>
                <div className="text-xs font-semibold text-gray-300 mb-1.5">执行报告</div>
                <pre className="text-xs text-gray-300 bg-gray-950/60 border border-gray-800 rounded-lg p-3 whitespace-pre-wrap break-words max-h-56 overflow-y-auto">
                  {run.report}
                </pre>
              </div>
            )}

            {/* 流水线对话实录 */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-semibold text-gray-300">流水线对话实录</span>
                <span className="text-[10px] text-gray-500">每次入站 = 一次独立对话；仅在 run 历史中查看</span>
              </div>
              {dialogEntries.length === 0 ? (
                <div className="text-xs text-gray-500 py-6 text-center border border-dashed border-gray-700 rounded-lg">
                  无对话内容（未产生 LLM 调用，或对话已被回收）
                </div>
              ) : (
                <div className="space-y-2">
                  {dialogEntries.map((d, i) => {
                    const style = DIALOG_KIND_STYLE[d.kind || ''] || DIALOG_KIND_STYLE.note;
                    const body = d.output || d.content || d.prompt || '';
                    const label = d.kind === 'input' ? '入站' : d.kind === 'llm' ? 'LLM' : (d.kind === 'note' ? '说明' : (d.kind || '事件'));
                    return (
                      <div key={i} className={`rounded-lg border px-3 py-2 ${style}`}>
                        <div className="flex items-center gap-2 text-[10px] opacity-80">
                          <span className="font-mono">#{d.seq ?? i + 1}</span>
                          <span className="font-semibold">{label}</span>
                          {d.node ? <span className="text-gray-400">节点 {d.node}</span> : null}
                          {d.agent ? <span className="text-gray-400">智能体 {d.agent}</span> : null}
                          {d.source ? <span className="text-gray-400">来源 {d.source}</span> : null}
                        </div>
                        <div className="mt-1 text-xs whitespace-pre-wrap break-words">{body}</div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* 节点/事件流水 */}
            {(run.nodes && Object.keys(run.nodes).length > 0) && (
              <div>
                <div className="text-xs font-semibold text-gray-300 mb-1.5">节点执行</div>
                <div className="space-y-1">
                  {Object.values(run.nodes).map((n, i) => (
                    <div key={i} className="flex items-center gap-2 text-[11px] px-3 py-1.5 rounded-lg bg-gray-800/40 border border-gray-800">
                      <RunStatusBadge status={n.status} />
                      <span className="font-mono text-gray-300">{n.node_id}</span>
                      <span className="text-gray-500">{n.node_type}</span>
                      {n.error && <span className="ml-auto text-red-400 truncate max-w-[220px]" title={n.error}>{n.error}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <div className="px-5 py-3 border-t border-gray-800 flex justify-end shrink-0">
          <button onClick={props.onClose} className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm text-gray-300">
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}


