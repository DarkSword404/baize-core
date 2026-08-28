import { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { createSession, listAgents, listManualPipelines, getModelConfig } from '../api/client';
import type { ManualPipelineBrief } from '../api/client';
import { agentNameCN, toolNameCN, agentDescCN } from '../i18n/translations';
import type { JSX } from 'react';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (sessionId: string) => void;
}

type OrchestrationMode = 'single' | 'pipeline' | 'swarm';

const MODE_LABELS: Record<OrchestrationMode, { label: string; desc: string; icon: string }> = {
  single:   { label: '单智能体',   desc: '选择一个智能体独立执行任务',           icon: '🤖' },
  pipeline: { label: '人工流水线', desc: '选择一条人工流水线，带确认节点的安全编排', icon: '🔶' },
  swarm:    { label: 'Swarm 协作', desc: '红队集群协作，智能体间动态 Handoff',       icon: '🪄' },
};

export function CreateSessionModal({ open, onClose, onCreated }: Props): JSX.Element | null {
  const { agents, setAgents, addToast, addSession } = useApp();
  const [selectedAgent, setSelectedAgent] = useState('');
  const [configuredModel, setConfiguredModel] = useState('');
  const [creating, setCreating] = useState(false);
  const [loadingAgents, setLoadingAgents] = useState(false);
  const [browserCollab, setBrowserCollab] = useState(false);

  // Pattern / pipeline selection
  const [mode, setMode] = useState<OrchestrationMode>('single');
  const [pipelines, setPipelines] = useState<ManualPipelineBrief[]>([]);
  const [loadingPipelines, setLoadingPipelines] = useState(false);
  const [selectedPipeline, setSelectedPipeline] = useState('');

  // Separate agents vs patterns for display
  const plainAgents = agents.filter(a => !a.pattern_type && a.name !== 'x-ray');
  const patternAgents = agents.filter(a => a.pattern_type && !a.name.startsWith('vuln_'));

  useEffect(() => {
    if (open) {
      if (agents.length === 0) {
        setLoadingAgents(true);
        listAgents()
          .then(r => setAgents(r.agents))
          .catch(() => {})
          .finally(() => setLoadingAgents(false));
      }
      if (!selectedAgent && agents.length > 0) setSelectedAgent(agents[0].name);
      // Fetch manual pipelines for chat selection
      if (pipelines.length === 0) {
        setLoadingPipelines(true);
        listManualPipelines()
          .then(r => setPipelines(r.pipelines))
          .catch(() => {})
          .finally(() => setLoadingPipelines(false));
      }
      // Load single configured model
      getModelConfig()
        .then(cfg => {
          if (cfg.configured) setConfiguredModel(cfg.model);
        })
        .catch(() => {});
    }
  }, [open]);

  useEffect(() => {
    // Auto-select first manual pipeline when switching to pipeline mode
    if (mode === 'pipeline' && pipelines.length > 0 && !selectedPipeline) {
      setSelectedPipeline(pipelines[0].id);
    }
  }, [mode, pipelines]);

  async function handleCreate() {
    setCreating(true);
    try {
      // swarm 集群本质是后端注册的 agent（pattern_type='swarm'），
      // 必须把选中的集群名传给 agent 字段；pattern 仅流水线模式使用。
      const session = await createSession({
        agent: mode === 'single' || mode === 'swarm' ? (selectedAgent || null) : null,
        model: configuredModel || null,
        stateful: true,
        pattern: mode === 'pipeline' ? (selectedPipeline || null) : null,
        browser_collab: browserCollab,
      });
      addSession(session);
      const patternLabel = mode === 'pipeline' ? ` · 人工流水线` : mode === 'swarm' ? ` · Swarm 协作` : '';
      const agentDisplay = session.agent ? (agentNameCN[session.agent] || session.agent) : '';
      addToast({ type: 'success', title: '会话已创建', message: `智能体: ${agentDisplay}${patternLabel}` });
      // 先关闭创建窗口再切换会话：保证窗口必然关闭，切换过程中的异步
      // 请求失败不会导致创建窗口残留。
      onClose();
      onCreated(session.id);
    } catch (err: any) {
      addToast({ type: 'error', title: '创建失败', message: err.message });
    } finally {
      setCreating(false);
    }
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-md p-6 shadow-2xl animate-slide-up">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold">新建渗透会话</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        {loadingAgents ? (
          <div className="text-center text-gray-500 py-8 text-sm">加载智能体中...</div>
        ) : (
          <div className="space-y-4">
            {/* ── 编排模式选择 ── */}
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-2">编排模式</label>
              <div className="grid grid-cols-3 gap-2">
                {(Object.keys(MODE_LABELS) as OrchestrationMode[]).map(m => (
                  <button
                    key={m}
                    onClick={() => setMode(m)}
                    className={`flex flex-col items-center gap-1 px-2 py-3 rounded-xl text-xs border transition-all ${
                      mode === m
                        ? 'border-purple-500/40 bg-purple-600/10 text-purple-300'
                        : 'border-gray-800 bg-gray-800/40 text-gray-500 hover:border-gray-700 hover:text-gray-300'
                    }`}
                  >
                    <span className="text-lg">{MODE_LABELS[m].icon}</span>
                    <span className="font-medium">{MODE_LABELS[m].label}</span>
                    <span className="text-[10px] leading-tight text-center text-gray-600">{MODE_LABELS[m].desc}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* ── 单智能体选择 ── */}
            {mode === 'single' && (
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">安全智能体</label>
                <div className="grid grid-cols-1 gap-1.5 max-h-48 overflow-y-auto">
                  {plainAgents.map(a => {
                    const cnName = agentNameCN[a.name] || a.name;
                    const cnDesc = agentDescCN[a.name] || a.description;
                    return (
                    <button
                      key={a.name}
                      onClick={() => setSelectedAgent(a.name)}
                      className={`text-left px-3 py-2.5 rounded-lg text-sm transition-all border ${
                        selectedAgent === a.name
                          ? 'border-blue-500/40 bg-blue-600/10 text-blue-300'
                          : 'border-transparent bg-gray-800/50 text-gray-300 hover:bg-gray-800 hover:border-gray-700'
                      }`}
                    >
                      <div className="font-medium text-xs">{cnName}</div>
                      {cnDesc && <div className="text-[11px] text-gray-500 mt-0.5 line-clamp-2">{cnDesc}</div>}
                      {a.tools && a.tools.length > 0 && (
                        <div className="flex gap-1 mt-1 flex-wrap">
                          {a.tools.slice(0, 4).map(t => (
                            <span key={t.name} className="text-[9px] px-1.5 py-0.5 rounded bg-gray-700/50 text-gray-400">{toolNameCN[t.name] || t.name}</span>
                          ))}
                          {a.tools.length > 4 && <span className="text-[9px] text-gray-600">+{a.tools.length - 4}</span>}
                        </div>
                      )}
                    </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* ── 人工流水线选择 ── */}
            {mode === 'pipeline' && (
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">可用人工流水线</label>
                <p className="text-[10px] text-gray-600 mb-2">人工流水线带有人工确认节点，适合需要审批的安全编排场景</p>
                {loadingPipelines ? (
                  <div className="text-xs text-gray-600 py-4 text-center">加载流水线中...</div>
                ) : pipelines.length === 0 ? (
                  <div className="text-xs text-gray-600 py-4 text-center">暂无可用人工流水线</div>
                ) : (
                  <div className="space-y-2">
                    {pipelines.map(p => (
                      <button
                        key={p.id}
                        onClick={() => setSelectedPipeline(p.id)}
                        className={`w-full text-left px-3 py-3 rounded-lg text-sm transition-all border ${
                          selectedPipeline === p.id
                            ? 'border-amber-500/40 bg-amber-600/10 text-amber-300'
                            : 'border-transparent bg-gray-800/50 text-gray-300 hover:bg-gray-800 hover:border-gray-700'
                        }`}
                      >
                        <div className="font-medium text-xs mb-1">{p.name}</div>
                        <div className="text-[11px] text-gray-500 mb-2 line-clamp-2">{p.description}</div>
                        {/* Node visualization */}
                        <div className="flex items-center gap-1 flex-wrap">
                          {p.node_types.map((nt, idx) => (
                            <span key={idx} className="flex items-center gap-1">
                              {idx > 0 && <span className="text-gray-600 text-[10px]">→</span>}
                              <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                                nt === 'confirm' ? 'bg-amber-600/20 text-amber-400' :
                                nt === 'decision' ? 'bg-blue-600/20 text-blue-400' :
                                'bg-gray-700/60 text-gray-300'
                              }`}>
                                {nt === 'agent' ? 'Agent' : nt === 'confirm' ? '确认' : nt === 'decision' ? '决策' : nt === 'parallel' ? '并行' : nt}
                              </span>
                            </span>
                          ))}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* ── Swarm 模式选择 ── */}
            {mode === 'swarm' && (
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Swarm 集群选择</label>
                <div className="text-[10px] text-gray-600 mb-2">选择一个集群，各智能体将自动协作、动态切换执行任务</div>
                <div className="grid grid-cols-1 gap-1.5 max-h-48 overflow-y-auto">
                  {patternAgents.map(a => {
                    const cnName = agentNameCN[a.name] || a.name;
                    const cnDesc = agentDescCN[a.name] || a.description;
                    return (
                    <button
                      key={a.name}
                      onClick={() => setSelectedAgent(a.name)}
                      className={`text-left px-3 py-2.5 rounded-lg text-sm transition-all border ${
                        selectedAgent === a.name
                          ? 'border-blue-500/40 bg-blue-600/10 text-blue-300'
                          : 'border-transparent bg-gray-800/50 text-gray-300 hover:bg-gray-800 hover:border-gray-700'
                      }`}
                    >
                      <div className="font-medium text-xs flex items-center gap-1.5">
                        <span>{cnName}</span>
                        {a.pattern_type && (
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-600/20 text-purple-400">集群协作</span>
                        )}
                      </div>
                      {cnDesc && <div className="text-[11px] text-gray-500 mt-0.5 line-clamp-2">{cnDesc}</div>}
                    </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* ── 模型（单模型，在设置中配置） ── */}
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">模型</label>
              <div className="px-3 py-2.5 bg-gray-800/50 border border-gray-800 rounded-lg text-sm text-gray-400">
                {configuredModel ? (
                  <span className="text-gray-200">{configuredModel}</span>
                ) : (
                  <span className="text-gray-500">未配置（请在 设置 → 模型配置 中填写）</span>
                )}
              </div>
            </div>

            {/* ── 共享浏览器协作开关 ── */}
            <div className="flex items-center justify-between p-3 rounded-xl border border-gray-800 bg-gray-900/60">
              <div>
                <div className="text-xs font-medium text-gray-300">共享浏览器协作</div>
                <div className="text-[10px] text-gray-600 mt-0.5">
                  AI 可在对话中调用共享协作浏览器（登录态持久化，仅本会话生效）
                </div>
              </div>
              <button
                onClick={() => setBrowserCollab(b => !b)}
                className={`relative w-10 h-6 rounded-full transition-colors flex-shrink-0 ${browserCollab ? 'bg-blue-600' : 'bg-gray-700'}`}
                role="switch"
                aria-checked={browserCollab}
              >
                <span
                  className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${browserCollab ? 'translate-x-4' : ''}`}
                />
              </button>
            </div>

            <button
              onClick={handleCreate}
              disabled={
                creating ||
                agents.length === 0 ||
                (mode === 'single' && !selectedAgent) ||
                (mode === 'pipeline' && !selectedPipeline) ||
                (mode === 'swarm' && !selectedAgent)
              }
              className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-xl text-sm font-semibold transition-all active:scale-[0.98] cursor-pointer disabled:cursor-not-allowed"
            >
              {creating ? '创建中...' : '创建会话'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
