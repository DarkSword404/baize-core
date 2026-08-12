import { useEffect, useState, useRef } from 'react';
import { useApp } from '../context/AppContext';
import { listAgents, deleteCustomAgent, deleteBuiltinAgent, createSession, streamMessage, getAgentDetail } from '../api/client';
import type { ReasoningStep } from '../api/client';
import type { AgentMetadata } from '../types';
import { agentDescCN, toolDescCN } from '../i18n/translations';
import type { JSX } from 'react';

export function Agents(): JSX.Element {
  const { agents, setAgents, addToast } = useApp();
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedAgent, setSelectedAgent] = useState<AgentMetadata | null>(null);

  // 创建智能体 (Agent Builder)
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createRequirement, setCreateRequirement] = useState('');
  const [createStreaming, setCreateStreaming] = useState(false);
  const [createDone, setCreateDone] = useState(false);
  const [createLog, setCreateLog] = useState<Array<{ role: string; content: string }>>([]);
  const streamCtrlRef = useRef<AbortController | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    loadAgents();
  }, []);

  async function loadAgents() {
    setLoading(true);
    try {
      // 后端 /api/v1/agents 已统一返回内置 + 自定义，无需手动合并
      const r = await listAgents();
      setAgents(r.agents);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载失败', message: err.message });
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(id: string, name: string, isBuiltin: boolean) {
    if (!confirm(`确定要删除智能体「${name}」吗？${isBuiltin ? '内置智能体可在设置中恢复。' : '此操作不可撤销。'}`)) return;
    try {
      if (isBuiltin) {
        await deleteBuiltinAgent(name);
      } else {
        await deleteCustomAgent(id);
      }
      addToast({ type: 'success', title: '删除成功', message: `已删除智能体「${name}」` });
      setSelectedAgent(null);
      loadAgents();
    } catch (err: any) {
      addToast({ type: 'error', title: '删除失败', message: err.message });
    }
  }

  async function handleCreateSubmit() {
    if (!createRequirement.trim()) {
      addToast({ type: 'warning', title: '请填写需求', message: '描述你想要创建什么样的智能体' });
      return;
    }
    setCreateStreaming(true);
    setCreateLog([{ role: 'user', content: createRequirement }]);

    try {
      // 1. Create session with agent_builder
      const session = await createSession({
        agent: 'agent_builder',
        model: undefined,
      });

      // 2. Build the prompt with structured requirements
      let prompt = createRequirement;
      if (createName.trim()) {
        prompt = `请帮我创建一个名为 "${createName.trim()}" 的安全智能体。\n\n具体需求如下：\n${createRequirement}\n\n请按照你的标准流程：先分析需求，使用 generate_system_prompt 生成系统提示词，使用 list_available_tools 选择合适的工具，最后使用 save_agent_file 保存智能体文件。`;
      } else {
        prompt = `请帮我创建一个安全智能体。\n\n具体需求如下：\n${createRequirement}\n\n请按照你的标准流程：先分析需求，使用 generate_system_prompt 生成系统提示词，使用 list_available_tools 选择合适的工具，最后使用 save_agent_file 保存智能体文件。`;
      }

      // 3. Stream the response
      let fullText = '';
      const controller = streamMessage(
        session.id,
        { input: prompt },
        // onChunk
        (chunk: string) => {
          fullText += chunk;
          setCreateLog(prev => {
            const last = prev[prev.length - 1];
            if (last && last.role === 'assistant') {
              return [...prev.slice(0, -1), { ...last, content: fullText }];
            }
            return [...prev, { role: 'assistant', content: fullText }];
          });
        },
        // onDone
        () => {
          setCreateStreaming(false);
          setCreateDone(true);
          loadAgents();
          addToast({ type: 'success', title: '智能体创建完成', message: 'Agent Builder 已完成创建，请刷新列表查看新智能体' });
        },
        // onError
        (err: Error) => {
          setCreateStreaming(false);
          addToast({ type: 'error', title: '创建失败', message: err.message || 'Agent Builder 返回错误' });
        },
        // onPrompt 未使用
        undefined,
        // onStep (tool calls)
        (step: ReasoningStep) => {
          if (step.type === 'tool_call' && step.tool) {
            const toolName = step.tool;
            setCreateLog(prev => [...prev, {
              role: 'tool',
              content: `🔧 调用工具: ${toolName}`,
            }]);
          } else if (step.type === 'tool_output' && step.output) {
            const outputStr = typeof step.output === 'string'
              ? step.output
              : JSON.stringify(step.output);
            const preview = outputStr.length > 300 ? outputStr.substring(0, 300) + '...' : outputStr;
            setCreateLog(prev => [...prev, {
              role: 'tool',
              content: `📤 ${preview}`,
            }]);
          }
        },
      );
      streamCtrlRef.current = controller;
    } catch (err: any) {
      setCreateStreaming(false);
      addToast({ type: 'error', title: '启动失败', message: err.message || '无法连接到 Agent Builder' });
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
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              setCreateName('');
              setCreateRequirement('');
              setCreateStreaming(false);
              setCreateDone(false);
              setCreateLog([]);
              setShowCreate(true);
            }}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 rounded-xl transition-colors font-medium"
          >
            + 新建智能体
          </button>
          <button onClick={loadAgents} className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl transition-colors">
            刷新
          </button>
        </div>
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
              key={a.id || a.name}
              className={`relative text-left p-5 rounded-2xl border transition-all hover:shadow-xl ${
                selectedAgent?.name === a.name
                  ? 'border-blue-500/40 bg-blue-600/5 shadow-lg shadow-blue-600/5'
                  : 'border-gray-800 bg-gray-900 hover:border-gray-700'
              }`}
            >
              <button
                onClick={async () => {
                  if (selectedAgent?.name === a.name) { setSelectedAgent(null); return; }
                  setSelectedAgent(a);
                  if (!a.is_custom) {
                    setDetailLoading(true);
                    try {
                      const detail = await getAgentDetail(a.name);
                      setSelectedAgent({ ...a, instructions: detail.instructions, source: 'builtin' as const });
                    } catch { /* keep basic info */ }
                    setDetailLoading(false);
                  }
                }}
                className="w-full text-left"
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="w-10 h-10 bg-gray-800 rounded-xl flex items-center justify-center text-lg">
                    {a.is_custom ? '🧪' : (a.type === 'pattern' ? '🔍' : '🤖')}
                  </div>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                    a.is_custom ? 'bg-amber-600/10 text-amber-400' :
                    a.type === 'pattern' ? 'bg-purple-600/10 text-purple-400' : 'bg-blue-600/10 text-blue-400'
                  }`}>
                    {a.is_custom ? '自定义' : (a.type === 'pattern' ? '模式' : '预置')}
                  </span>
                </div>
                <h3 className="text-sm font-semibold mb-1">{a.name}</h3>
                <p className="text-xs text-gray-500 line-clamp-2 mb-3">{agentDescCN[a.name] || a.description || '无描述'}</p>
                {a.tools && a.tools.length > 0 && (
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
              <button
                onClick={(e) => { e.stopPropagation(); handleDelete(a.id!, a.name, !a.is_custom); }}
                className="absolute bottom-3 right-3 p-1.5 rounded-lg bg-red-600/10 hover:bg-red-600/20 text-red-400 hover:text-red-300 transition-colors"
                title="删除"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Agent Builder 创建智能体弹窗 */}
      {showCreate && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60" onClick={() => { if (!createStreaming) setShowCreate(false); }}>
          <div className="w-full max-w-xl mx-4 bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl p-6 max-h-[85vh] flex flex-col" onClick={e => e.stopPropagation()}>
            {/* Header */}
            <div className="flex items-center justify-between mb-4 shrink-0">
              <div>
                <h2 className="text-lg font-semibold flex items-center gap-2">
                  <span className="text-blue-400">🤖</span>
                  {createStreaming ? 'Agent Builder 正在创建...' : createDone ? '智能体创建完成' : 'AI 创建智能体'}
                </h2>
                <p className="text-xs text-gray-500 mt-0.5">
                  {createStreaming
                    ? 'Agent Builder 正在分析需求、生成提示词并保存智能体'
                    : createDone
                    ? 'Agent Builder 已完成智能体生成，请刷新列表查看'
                    : '描述你的需求，Agent Builder 将自动创建完整的智能体'}
                </p>
              </div>
              <button
                onClick={() => { if (!createStreaming) setShowCreate(false); }}
                disabled={createStreaming}
                className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 disabled:opacity-30 shrink-0"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
              </button>
            </div>

            {/* Input form (before sending) */}
            {!createDone && !createStreaming && (
              <>
                <div className="space-y-4">
                  <div>
                    <label className="block text-xs font-semibold text-gray-400 mb-1.5">
                      智能体名称 <span className="text-gray-600">(可选，不填则让 AI 命名)</span>
                    </label>
                    <input
                      type="text"
                      value={createName}
                      onChange={e => setCreateName(e.target.value)}
                      placeholder="如 sql-injection-scanner"
                      className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-400 mb-1.5">
                      需求描述 <span className="text-red-400">*</span>
                    </label>
                    <textarea
                      value={createRequirement}
                      onChange={e => setCreateRequirement(e.target.value)}
                      placeholder={`请描述你想要创建的智能体，Agent Builder 将自动为你生成完整的系统提示词、选定工具并保存。

例如：
"我需要一个 SQL 注入检测智能体，能够自动扫描 Web 应用中的 SQL 注入漏洞，支持 GET/POST 参数测试，使用 sqlmap 作为核心工具，输出结构化的漏洞报告"

你可以指定：
• 用途与职责 — 智能体要做什么
• 专长领域 — 安全领域 (Web/Mobile/Cloud/Network...)
• 所需工具 — 需要哪些工具支持
• 输出风格 — 报告格式、详细程度等`}
                      rows={7}
                      className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none resize-none leading-relaxed"
                      onKeyDown={e => {
                        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                          handleCreateSubmit();
                        }
                      }}
                    />
                    <p className="text-[10px] text-gray-600 mt-1">提示：Ctrl+Enter 快速发送 | Agent Builder 将调用 save_agent_file 自动保存</p>
                  </div>
                </div>
                <div className="flex items-center gap-3 mt-5 shrink-0">
                  <button onClick={() => setShowCreate(false)} className="flex-1 px-4 py-2.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-sm transition-colors">
                    取消
                  </button>
                  <button
                    onClick={handleCreateSubmit}
                    disabled={!createRequirement.trim()}
                    className="flex-1 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-800 disabled:text-gray-600 rounded-xl text-sm font-medium transition-colors flex items-center justify-center gap-2"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z"/>
                    </svg>
                    发送给 Agent Builder
                  </button>
                </div>
              </>
            )}

            {/* Streaming log / Result view */}
            {(createStreaming || createDone) && (
              <>
                <div className="flex-1 overflow-y-auto mb-4 min-h-[240px] max-h-[55vh] bg-gray-950 rounded-xl p-4 space-y-3 font-mono text-xs leading-relaxed">
                  {createLog.length === 0 && createStreaming && (
                    <div className="flex items-center gap-3 text-gray-500 py-8 justify-center">
                      <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                      <span>正在连接 Agent Builder...</span>
                    </div>
                  )}
                  {createLog.map((entry, i) => (
                    <div key={i} className={
                      entry.role === 'user' ? 'text-blue-400 bg-blue-500/5 -mx-2 px-2 py-1.5 rounded-lg' :
                      entry.role === 'tool' ? 'text-amber-400/80' :
                      'text-gray-300'
                    }>
                      {entry.role === 'user' && <div className="text-[10px] text-blue-500/50 mb-0.5">▸ 你</div>}
                      {entry.role === 'tool' && <div className="text-[10px] text-amber-500/50 mb-0.5">▹ 工具</div>}
                      {entry.role === 'assistant' && createLog.findIndex(l => l.role === 'assistant') === i && (
                        <div className="text-[10px] text-green-500/50 mb-0.5">▹ Agent Builder</div>
                      )}
                      <span className="whitespace-pre-wrap">{entry.content}</span>
                    </div>
                  ))}
                  {createStreaming && (
                    <span className="inline-block w-2 h-4 bg-blue-400 animate-pulse ml-0.5 align-middle" />
                  )}
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  {createStreaming ? (
                    <button
                      onClick={() => { streamCtrlRef.current?.abort(); setCreateStreaming(false); }}
                      className="flex-1 px-4 py-2.5 bg-red-600/10 hover:bg-red-600/20 border border-red-600/20 rounded-xl text-sm text-red-400 transition-colors"
                    >
                      取消创建
                    </button>
                  ) : (
                    <>
                      <button
                        onClick={() => setShowCreate(false)}
                        className="flex-1 px-4 py-2.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-sm transition-colors"
                      >
                        关闭
                      </button>
                      <button
                        onClick={() => {
                          setCreateName('');
                          setCreateRequirement('');
                          setCreateLog([]);
                          setCreateDone(false);
                          setCreateStreaming(false);
                        }}
                        className="flex-1 px-4 py-2.5 bg-green-600 hover:bg-green-700 rounded-xl text-sm font-medium transition-colors"
                      >
                        再建一个
                      </button>
                      <button
                        onClick={() => { loadAgents(); addToast({ type: 'info', title: '已刷新', message: '智能体列表已更新' }); }}
                        className="flex-1 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 rounded-xl text-sm font-medium transition-colors"
                      >
                        刷新列表
                      </button>
                    </>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Detail Drawer */}
      {selectedAgent && (
        <div className="fixed inset-y-0 right-0 z-30 w-80 bg-gray-900 border-l border-gray-800 shadow-2xl animate-slide-right overflow-y-auto">
          <div className="p-5">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold">{selectedAgent.name}</h2>
                {selectedAgent.is_custom ? (
                  <span className="text-[10px] px-2 py-0.5 rounded-full font-medium bg-amber-600/10 text-amber-400">自定义</span>
                ) : (
                  <span className="text-[10px] px-2 py-0.5 rounded-full font-medium bg-blue-600/10 text-blue-400">预置</span>
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

              {!selectedAgent.is_custom && (
                <div>
                  <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 flex items-center gap-2">
                    系统指令
                    {detailLoading && <span className="w-3 h-3 border-2 border-gray-600 border-t-blue-400 rounded-full animate-spin" />}
                  </h3>
                  {selectedAgent.instructions ? (
                    <div className="max-h-48 overflow-y-auto">
                      <pre className="text-xs text-gray-400 bg-gray-800/60 rounded-lg p-3 whitespace-pre-wrap font-mono leading-relaxed">{selectedAgent.instructions}</pre>
                    </div>
                  ) : (
                    <p className="text-sm text-gray-600">{detailLoading ? '加载中...' : '无系统指令'}</p>
                  )}
                </div>
              )}

              <div>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
                  工具列表 ({selectedAgent.tools?.length ?? 0})
                </h3>
                {!selectedAgent.tools || selectedAgent.tools.length === 0 ? (
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

            <div className="mt-6 pt-4 border-t border-gray-800">
                <button
                  onClick={() => handleDelete(selectedAgent.id!, selectedAgent.name, !selectedAgent.is_custom)}
                  className="w-full px-4 py-2.5 bg-red-600/10 hover:bg-red-600/20 border border-red-600/20 rounded-xl text-sm text-red-400 hover:text-red-300 transition-colors flex items-center justify-center gap-2 cursor-pointer"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                  删除此智能体
                </button>
              </div>
          </div>
        </div>
      )}
    </div>
  );
}
