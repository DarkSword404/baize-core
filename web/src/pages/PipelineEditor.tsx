import { useState, useEffect, useCallback } from 'react';
import { useApp } from '../context/AppContext';
import {
  listAgents,
  listCustomPipelines,
  createCustomPipeline,
  updateCustomPipeline,
  deleteCustomPipeline,
  listCustomAgents,
  createCustomAgent,
  deleteCustomAgent,
  updateCustomAgent,
  listAvailableTools,
  type CustomPipeline,
  type CustomAgent,
  type ToolInfo,
} from '../api/client';
import type { AgentMetadata as AgentMeta } from '../types';
import type { JSX } from 'react';

// ===== 子组件: 智能体创建弹窗 =====
function AgentCreatorModal({
  open,
  onClose,
  agent,
  onSave,
}: {
  open: boolean;
  onClose: () => void;
  agent?: CustomAgent | null;
  onSave?: () => void;
}): JSX.Element | null {
  const { addToast } = useApp();
  const [name, setName] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [description, setDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [model, setModel] = useState('');
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [availableTools, setAvailableTools] = useState<ToolInfo[]>([]);
  const [toolsLoading, setToolsLoading] = useState(false);

  useEffect(() => {
    if (open && agent) {
      setName(agent.name);
      setDisplayName(agent.display_name);
      setDescription(agent.description);
      setInstructions(agent.instructions);
      setModel(agent.model);
      setSelectedTools(agent.tools || []);
    } else if (open) {
      setName('');
      setDisplayName('');
      setDescription('');
      setInstructions('');
      setModel('');
      setSelectedTools([]);
    }
  }, [open, agent]);

  // 加载可用工具列表
  useEffect(() => {
    if (open && availableTools.length === 0) {
      setToolsLoading(true);
      listAvailableTools()
        .then(r => setAvailableTools(r.tools))
        .catch(() => { /* ignore */ })
        .finally(() => setToolsLoading(false));
    }
  }, [open]);

  // 按分类分组工具
  const toolsByCategory: Record<string, ToolInfo[]> = {};
  availableTools.forEach(t => {
    const cat = t.category || '通用';
    if (!toolsByCategory[cat]) toolsByCategory[cat] = [];
    toolsByCategory[cat].push(t);
  });

  function toggleTool(toolId: string) {
    setSelectedTools(prev =>
      prev.includes(toolId) ? prev.filter(id => id !== toolId) : [...prev, toolId]
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !displayName.trim()) return;
    setSaving(true);
    try {
      if (agent) {
        await updateCustomAgent(agent.id, { name: name.trim(), display_name: displayName.trim(), description, instructions, model, tools: selectedTools });
        addToast({ type: 'success', title: '已更新', message: `智能体 "${displayName}" 已更新` });
      } else {
        await createCustomAgent({ name: name.trim(), display_name: displayName.trim(), description, instructions, model, tools: selectedTools });
        addToast({ type: 'success', title: '已创建', message: `智能体 "${displayName}" 已创建` });
      }
      onSave?.();
      onClose();
    } catch (e: any) {
      addToast({ type: 'error', title: '保存失败', message: e.message });
    } finally {
      setSaving(false);
    }
  }

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-xl p-6 shadow-2xl animate-slide-up max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-semibold">{agent ? '编辑智能体' : '创建智能体'}</h3>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">标识名（英文）</label>
              <input value={name} onChange={e => setName(e.target.value)} placeholder="如: sql_injector"
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:border-purple-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">显示名称（中文）</label>
              <input value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="如: SQL注入检测器"
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:border-purple-500 focus:outline-none"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">描述</label>
            <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} placeholder="简要描述该智能体的功能"
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:border-purple-500 focus:outline-none resize-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">模型</label>
            <input value={model} onChange={e => setModel(e.target.value)} placeholder="留空使用默认模型"
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:border-purple-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">系统指令</label>
            <textarea value={instructions} onChange={e => setInstructions(e.target.value)} rows={4} placeholder="输入该智能体的系统指令/提示词..."
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:border-purple-500 focus:outline-none resize-none font-mono"
            />
          </div>

          {/* 工具选择 */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-2">
              功能工具
              {selectedTools.length > 0 && <span className="ml-2 text-purple-400">(已选 {selectedTools.length} 个)</span>}
            </label>
            {toolsLoading ? (
              <div className="text-xs text-gray-600 py-2">加载工具列表...</div>
            ) : availableTools.length === 0 ? (
              <div className="text-xs text-gray-600 py-2">暂无可选工具</div>
            ) : (
              <div className="space-y-3 max-h-56 overflow-y-auto p-1">
                {Object.entries(toolsByCategory).map(([category, tools]) => (
                  <div key={category}>
                    <div className="text-[10px] font-medium text-gray-500 uppercase tracking-wider mb-1.5">{category}</div>
                    <div className="flex flex-wrap gap-1.5">
                      {tools.map(tool => {
                        const isSelected = selectedTools.includes(tool.id);
                        return (
                          <button
                            key={tool.id}
                            type="button"
                            onClick={() => toggleTool(tool.id)}
                            title={tool.description}
                            className={`px-2 py-1 rounded-md text-xs border transition-all ${
                              isSelected
                                ? 'border-purple-500/40 bg-purple-600/20 text-purple-300'
                                : 'border-gray-700/50 bg-gray-800/50 text-gray-500 hover:text-gray-300 hover:border-gray-600'
                            }`}
                          >
                            {tool.name}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <button type="submit" disabled={saving || !name.trim() || !displayName.trim()}
            className="w-full py-2.5 bg-purple-600 hover:bg-purple-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-xl text-sm font-semibold transition-all"
          >
            {saving ? '保存中...' : agent ? '更新智能体' : '创建智能体'}
          </button>
        </form>
      </div>
    </div>
  );
}

// ===== 主编排页面 =====
export function PipelineEditor(): JSX.Element {
  const { addToast } = useApp();

  // 流水线列表
  const [pipelines, setPipelines] = useState<CustomPipeline[]>([]);
  const [loadingPipelines, setLoadingPipelines] = useState(true);
  const [selectedPipelineId, setSelectedPipelineId] = useState<string | null>(null);

  // 可用智能体列表
  const [availableAgents, setAvailableAgents] = useState<AgentMeta[]>([]);

  // 自定义智能体列表
  const [customAgents, setCustomAgents] = useState<CustomAgent[]>([]);

  // 工具 ID → 中文名 映射
  const [toolMap, setToolMap] = useState<Record<string, string>>({});

  // 编辑中的流水线
  const [editName, setEditName] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [editSteps, setEditSteps] = useState<Array<{ agent_name: string; display_name: string; description: string }>>([]);

  // 新建智能体弹窗
  const [agentCreatorOpen, setAgentCreatorOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<CustomAgent | null>(null);

  // UI 状态
  const [isNew, setIsNew] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);

  // 标签页
  const [activeTab, setActiveTab] = useState<'pipelines' | 'agents'>('pipelines');

  // 加载数据
  const loadPipelines = useCallback(async () => {
    setLoadingPipelines(true);
    try {
      const r = await listCustomPipelines();
      setPipelines(r.pipelines);
    } catch { /* ignore */ }
    finally { setLoadingPipelines(false); }
  }, []);

  const loadAgents = useCallback(async () => {
    try {
      const [builtin, custom, tools] = await Promise.all([
        listAgents(), listCustomAgents(), listAvailableTools()
      ]);
      setAvailableAgents(builtin.agents);
      setCustomAgents(custom.agents);
      const map: Record<string, string> = {};
      tools.tools.forEach(t => { map[t.id] = t.name; });
      setToolMap(map);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadPipelines(); loadAgents(); }, [loadPipelines, loadAgents]);

  // 选中流水线时加载数据
  useEffect(() => {
    if (selectedPipelineId) {
      const p = pipelines.find(p => p.id === selectedPipelineId);
      if (p) {
        setEditName(p.name);
        setEditDesc(p.description);
        setEditSteps([...p.steps]);
        setIsNew(false);
      }
    }
  }, [selectedPipelineId, pipelines]);

  // 新建流水线
  function handleNew() {
    setSelectedPipelineId(null);
    setEditName('');
    setEditDesc('');
    setEditSteps([]);
    setIsNew(true);
  }

  // 保存流水线
  async function handleSave() {
    if (!editName.trim()) return;
    setSaving(true);
    try {
      if (isNew || !selectedPipelineId) {
        const p = await createCustomPipeline({ name: editName.trim(), description: editDesc, steps: editSteps });
        setPipelines(prev => [...prev, p]);
        setSelectedPipelineId(p.id);
        setIsNew(false);
        addToast({ type: 'success', title: '已创建', message: `流水线 "${editName}" 已创建` });
      } else {
        await updateCustomPipeline(selectedPipelineId, { name: editName.trim(), description: editDesc, steps: editSteps });
        setPipelines(prev => prev.map(p => p.id === selectedPipelineId ? { ...p, name: editName, description: editDesc, steps: editSteps } : p));
        addToast({ type: 'success', title: '已更新', message: `流水线 "${editName}" 已更新` });
      }
    } catch (e: any) {
      addToast({ type: 'error', title: '保存失败', message: e.message });
    } finally {
      setSaving(false);
    }
  }

  // 删除流水线
  async function handleDeletePipeline(id: string) {
    if (!confirm('确定要删除此流水线吗？此操作不可撤销。')) return;
    try {
      await deleteCustomPipeline(id);
      setPipelines(prev => prev.filter(p => p.id !== id));
      if (selectedPipelineId === id) {
        setSelectedPipelineId(null);
        setEditName('');
        setEditDesc('');
        setEditSteps([]);
        setIsNew(false);
      }
      addToast({ type: 'success', title: '已删除', message: '流水线已删除' });
    } catch (e: any) {
      addToast({ type: 'error', title: '删除失败', message: e.message });
    }
  }

  // 删除智能体
  async function handleDeleteAgent(id: string, name: string) {
    if (!confirm(`确定要删除智能体 "${name}" 吗？`)) return;
    try {
      await deleteCustomAgent(id);
      setCustomAgents(prev => prev.filter(a => a.id !== id));
      addToast({ type: 'success', title: '已删除', message: `智能体 "${name}" 已删除` });
    } catch (e: any) {
      addToast({ type: 'error', title: '删除失败', message: e.message });
    }
  }

  // 步骤管理
  function addStep(idx?: number) {
    const step = { agent_name: '', display_name: '', description: '' };
    if (idx === undefined || idx >= editSteps.length) {
      setEditSteps(prev => [...prev, step]);
    } else {
      setEditSteps(prev => { const n = [...prev]; n.splice(idx + 1, 0, step); return n; });
    }
  }

  function removeStep(idx: number) {
    setEditSteps(prev => prev.filter((_, i) => i !== idx));
  }

  function updateStep(idx: number, field: string, value: string) {
    setEditSteps(prev => prev.map((s, i) => i === idx ? { ...s, [field]: value } : s));
  }

  // 拖拽排序
  function handleDragStart(idx: number) { setDragIdx(idx); }
  function handleDragOver(e: React.DragEvent, _idx: number) { e.preventDefault(); }
  function handleDrop(dropIdx: number) {
    if (dragIdx === null || dragIdx === dropIdx) { setDragIdx(null); return; }
    setEditSteps(prev => {
      const next = [...prev];
      const [item] = next.splice(dragIdx, 1);
      next.splice(dropIdx, 0, item);
      return next;
    });
    setDragIdx(null);
  }

  // 过滤掉 swarm/pattern 类型的 pseudo-agent，只留实际可用的智能体
  const usableAgents = availableAgents.filter(a => !a.pattern_type && a.name !== 'x-ray');

  const selectedPipeline = pipelines.find(p => p.id === selectedPipelineId);
  const hasChanges = isNew || (selectedPipeline && (
    selectedPipeline.name !== editName ||
    selectedPipeline.description !== editDesc ||
    JSON.stringify(selectedPipeline.steps) !== JSON.stringify(editSteps)
  ));

  return (
    <div className="h-full flex flex-col bg-gray-950">
      {/* Header */}
      <header className="flex-shrink-0 h-14 border-b border-gray-800 flex items-center px-5 gap-4 bg-gray-950/80 backdrop-blur-sm">
        <div className="flex items-center gap-2">
          <span className="text-xl">⚙️</span>
          <h1 className="text-base font-semibold">智能体编排平台</h1>
        </div>
        <div className="flex items-center gap-1 ml-4 bg-gray-900 rounded-lg border border-gray-800 p-0.5">
          <button onClick={() => setActiveTab('pipelines')}
            className={`px-3 py-1 text-xs rounded-md font-medium transition-colors ${activeTab === 'pipelines' ? 'bg-purple-600/20 text-purple-400' : 'text-gray-500 hover:text-gray-300'}`}>
            🔗 流水线管理
          </button>
          <button onClick={() => setActiveTab('agents')}
            className={`px-3 py-1 text-xs rounded-md font-medium transition-colors ${activeTab === 'agents' ? 'bg-purple-600/20 text-purple-400' : 'text-gray-500 hover:text-gray-300'}`}>
            🤖 智能体管理
          </button>
        </div>
        <div className="flex-1" />
      </header>

      {/* Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* ===== 流水线管理标签页 ===== */}
        {activeTab === 'pipelines' && (
          <>
            {/* Left Sidebar - Pipeline List */}
            <aside className="w-56 flex-shrink-0 border-r border-gray-800 bg-gray-950/50 flex flex-col">
              <div className="px-3 py-3 border-b border-gray-800 flex items-center justify-between">
                <span className="text-xs font-medium text-gray-400">自定义流水线</span>
                <button onClick={handleNew} className="p-1 rounded hover:bg-gray-800 text-gray-500 hover:text-purple-400 transition-colors" title="新建流水线">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" d="M12 4v16m8-8H4" />
                  </svg>
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-2 space-y-1">
                {loadingPipelines ? (
                  <p className="text-xs text-gray-600 text-center py-6">加载中...</p>
                ) : pipelines.length === 0 ? (
                  <p className="text-xs text-gray-600 text-center py-6">暂无流水线<br />点击 + 创建</p>
                ) : (
                  pipelines.map(p => (
                    <button key={p.id} onClick={() => setSelectedPipelineId(p.id)}
                      className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all border ${
                        selectedPipelineId === p.id
                          ? 'border-purple-500/30 bg-purple-600/10 text-purple-300'
                          : 'border-transparent bg-gray-800/30 text-gray-300 hover:bg-gray-800/50 hover:border-gray-700/50'
                      }`}
                    >
                      <div className="font-medium text-xs truncate">{p.name}</div>
                      <div className="text-[10px] text-gray-600 mt-0.5">{p.steps.length} 个步骤</div>
                    </button>
                  ))
                )}
              </div>
            </aside>

            {/* Main - Pipeline Editor */}
            <main className="flex-1 flex flex-col overflow-hidden">
              {selectedPipeline || isNew ? (
                <>
                  {/* Edit Header */}
                  <div className="flex-shrink-0 px-5 py-4 border-b border-gray-800 space-y-3 bg-gray-950/30">
                    <div className="flex items-center gap-3">
                      <div className="flex-1">
                        <input value={editName} onChange={e => setEditName(e.target.value)}
                          placeholder="流水线名称" className="w-full px-3 py-1.5 bg-gray-900 border border-gray-800 rounded-lg text-sm font-semibold text-gray-200 placeholder-gray-600 focus:border-purple-500 focus:outline-none"
                        />
                      </div>
                      <div className="flex items-center gap-2">
                        {selectedPipelineId && (
                          <button onClick={() => handleDeletePipeline(selectedPipelineId)}
                            className="px-3 py-1.5 text-xs rounded-lg bg-red-600/10 hover:bg-red-600/20 text-red-400 border border-red-600/20 transition-colors">
                            删除
                          </button>
                        )}
                        <button onClick={handleSave} disabled={saving || !editName.trim() || !hasChanges}
                          className="px-4 py-1.5 text-xs rounded-lg bg-purple-600 hover:bg-purple-500 disabled:bg-gray-700 disabled:text-gray-500 font-medium transition-colors">
                          {saving ? '保存中...' : '保存流水线'}
                        </button>
                      </div>
                    </div>
                    <textarea value={editDesc} onChange={e => setEditDesc(e.target.value)}
                      placeholder="流水线描述（选填）" rows={2}
                      className="w-full px-3 py-1.5 bg-gray-900 border border-gray-800 rounded-lg text-xs text-gray-400 placeholder-gray-600 focus:border-purple-500 focus:outline-none resize-none"
                    />
                  </div>

                  {/* Steps List */}
                  <div className="flex-1 overflow-y-auto px-5 py-4 space-y-2">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-xs font-medium text-gray-400">执行步骤 (拖拽排序)</span>
                      <span className="text-[10px] text-gray-600">共 {editSteps.length} 步</span>
                    </div>
                    {editSteps.length === 0 && (
                      <div className="text-center py-10 text-gray-600 text-sm">
                        暂无步骤，点击下方按钮添加第一个步骤
                      </div>
                    )}
                    {editSteps.map((step, idx) => (
                      <div key={idx}
                        draggable
                        onDragStart={() => handleDragStart(idx)}
                        onDragOver={(e) => handleDragOver(e, idx)}
                        onDrop={() => handleDrop(idx)}
                        className={`group flex items-start gap-3 p-3 rounded-xl border transition-all ${
                          dragIdx === idx ? 'border-purple-500 bg-purple-600/10 opacity-50' : 'border-gray-800 bg-gray-900/50 hover:border-gray-700'
                        }`}
                      >
                        {/* Drag handle + step number */}
                        <div className="flex items-center gap-2 pt-1 flex-shrink-0">
                          <span className="cursor-grab text-gray-600 hover:text-gray-400">
                            <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></svg>
                          </span>
                          <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${
                            dragIdx === idx ? 'bg-purple-600 text-white' : 'bg-gray-800 text-gray-500'
                          }`}>{idx + 1}</span>
                        </div>

                        {/* Step fields */}
                        <div className="flex-1 grid grid-cols-12 gap-2">
                          {/* Agent selection */}
                          <div className="col-span-4">
                            <label className="text-[10px] text-gray-600 mb-0.5 block">智能体</label>
                            <select value={step.agent_name} onChange={e => updateStep(idx, 'agent_name', e.target.value)}
                              className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-xs text-gray-300 focus:border-purple-500 focus:outline-none"
                            >
                              <option value="">选择智能体...</option>
                              {usableAgents.map(a => (
                                <option key={a.name} value={a.name}>{a.name}</option>
                              ))}
                            </select>
                          </div>
                          {/* Display name */}
                          <div className="col-span-3">
                            <label className="text-[10px] text-gray-600 mb-0.5 block">步骤名称</label>
                            <input value={step.display_name} onChange={e => updateStep(idx, 'display_name', e.target.value)}
                              placeholder="如: 漏洞扫描" className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-xs text-gray-300 placeholder-gray-600 focus:border-purple-500 focus:outline-none"
                            />
                          </div>
                          {/* Description */}
                          <div className="col-span-4">
                            <label className="text-[10px] text-gray-600 mb-0.5 block">描述</label>
                            <input value={step.description} onChange={e => updateStep(idx, 'description', e.target.value)}
                              placeholder="步骤说明" className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-xs text-gray-300 placeholder-gray-600 focus:border-purple-500 focus:outline-none"
                            />
                          </div>
                          {/* Actions */}
                          <div className="col-span-1 flex items-end gap-1 pb-0.5">
                            <button onClick={() => addStep(idx)} title="在下方插入"
                              className="p-1 rounded hover:bg-gray-800 text-gray-600 hover:text-purple-400 transition-colors opacity-0 group-hover:opacity-100">
                              <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                                <path strokeLinecap="round" d="M12 4v16m8-8H4" />
                              </svg>
                            </button>
                            <button onClick={() => removeStep(idx)} title="删除步骤"
                              className="p-1 rounded hover:bg-red-600/10 text-gray-600 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100">
                              <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                                <path strokeLinecap="round" d="M5 7h14M9 7V5h6v2M9 7v10h6V7" />
                              </svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}

                    {/* Add step button */}
                    <button onClick={() => addStep()}
                      className="w-full py-3 border-2 border-dashed border-gray-800 rounded-xl text-xs text-gray-600 hover:text-purple-400 hover:border-purple-500/30 transition-colors mt-2">
                      + 添加步骤
                    </button>

                    {/* Flow visualization */}
                    {editSteps.length > 1 && (
                      <div className="mt-4 pt-4 border-t border-gray-800">
                        <span className="text-[10px] text-gray-600 block mb-2">流程预览</span>
                        <div className="flex items-center gap-2 flex-wrap">
                          {editSteps.map((s, i) => (
                            <span key={i} className="flex items-center gap-1">
                              {i > 0 && <span className="text-gray-700 text-xs">→</span>}
                              <span className="px-2 py-1 rounded-md bg-gray-800 text-xs border border-gray-700">
                                <span className="text-gray-600">{s.agent_name || '?'}</span>
                                {s.display_name && <span className="text-gray-400 ml-1">{s.display_name}</span>}
                              </span>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </>
              ) : (
                /* Empty state */
                <div className="flex-1 flex items-center justify-center">
                  <div className="text-center space-y-3">
                    <span className="text-4xl opacity-30">🔗</span>
                    <p className="text-gray-500 text-sm">选择左侧流水线或创建新流水线</p>
                    <button onClick={handleNew} className="px-4 py-2 bg-purple-600 hover:bg-purple-500 rounded-lg text-sm font-medium transition-colors">
                      新建流水线
                    </button>
                  </div>
                </div>
              )}
            </main>
          </>
        )}

        {/* ===== 智能体管理标签页 ===== */}
        {activeTab === 'agents' && (
          <main className="flex-1 overflow-y-auto p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-300">自定义智能体</h2>
              <button onClick={() => { setEditingAgent(null); setAgentCreatorOpen(true); }}
                className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 rounded-lg text-xs font-medium transition-colors">
                + 新建智能体
              </button>
            </div>

            {customAgents.length === 0 ? (
              <div className="text-center py-16 text-gray-600">
                <span className="text-3xl block mb-2 opacity-30">🤖</span>
                <p className="text-sm">暂无自定义智能体</p>
                <p className="text-xs mt-1">点击上方按钮创建第一个智能体</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {customAgents.map(a => (
                  <div key={a.id} className="bg-gray-900 rounded-xl border border-gray-800 p-4 hover:border-gray-700 transition-colors group">
                    <div className="flex items-start justify-between mb-2">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{a.display_name}</span>
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-600/20 text-purple-400">自定义</span>
                        </div>
                        <div className="text-[10px] text-gray-600 font-mono mt-0.5">{a.name}</div>
                      </div>
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button onClick={() => { setEditingAgent(a); setAgentCreatorOpen(true); }}
                          className="p-1 rounded hover:bg-gray-800 text-gray-500 hover:text-purple-400 transition-colors" title="编辑">
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                            <path strokeLinecap="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                          </svg>
                        </button>
                        <button onClick={() => handleDeleteAgent(a.id, a.display_name)}
                          className="p-1 rounded hover:bg-red-600/10 text-gray-500 hover:text-red-400 transition-colors" title="删除">
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                            <path strokeLinecap="round" d="M5 7h14M9 7V5h6v2M9 7v10h6V7" />
                          </svg>
                        </button>
                      </div>
                    </div>
                    {a.description && <p className="text-xs text-gray-500 mb-2 line-clamp-2">{a.description}</p>}
                    {a.tools && a.tools.length > 0 && (
                      <div className="flex flex-wrap gap-1 mb-2">
                        {a.tools.map((t, i) => i < 4 && (
                          <span key={t} className="text-[9px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-400 border border-gray-700/50">
                            {toolMap[t] || t}
                          </span>
                        ))}
                        {a.tools.length > 4 && (
                          <span className="text-[9px] px-1.5 py-0.5 text-gray-600">+{a.tools.length - 4}</span>
                        )}
                      </div>
                    )}
                    <div className="flex items-center gap-3 text-[10px] text-gray-600">
                      {a.model && <span>模型: {a.model}</span>}
                      <span>创建: {a.created_at ? new Date(a.created_at).toLocaleDateString('zh-CN') : '-'}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </main>
        )}
      </div>

      {/* Agent Creator Modal */}
      <AgentCreatorModal
        open={agentCreatorOpen}
        onClose={() => setAgentCreatorOpen(false)}
        agent={editingAgent}
        onSave={loadAgents}
      />
    </div>
  );
}
