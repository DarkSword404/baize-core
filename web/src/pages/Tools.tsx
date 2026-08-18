import { useEffect, useState, useRef } from 'react';
import { useApp } from '../context/AppContext';
import {
  listAvailableTools,
  listCustomTools,
  deleteCustomTool,
  toggleCustomTool,
  createSession,
  streamMessage,
  type ToolInfo,
  type CustomTool,
} from '../api/client';
import type { ReasoningStep } from '../api/client';
import type { JSX } from 'react';

const CATEGORY_CN: Record<string, string> = {
  general: '通用',
  web: 'Web',
  network: '网络',
  recon: '侦察',
  crack: '爆破',
  forensic: '取证',
  security: '安全',
  custom: '自定义',
};

export function Tools(): JSX.Element {
  const { addToast } = useApp();
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<string>('all');
  const [builtinTools, setBuiltinTools] = useState<ToolInfo[]>([]);
  const [customTools, setCustomTools] = useState<CustomTool[]>([]);
  const [selected, setSelected] = useState<CustomTool | ToolInfo | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // 创建工具 (Tool Builder)
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createRequirement, setCreateRequirement] = useState('');
  const [createStreaming, setCreateStreaming] = useState(false);
  const [createDone, setCreateDone] = useState(false);
  const [createLog, setCreateLog] = useState<Array<{ role: string; content: string }>>([]);
  const streamCtrlRef = useRef<AbortController | null>(null);

  async function loadTools() {
    setLoading(true);
    try {
      const [builtin, custom] = await Promise.all([listAvailableTools(), listCustomTools()]);
      setBuiltinTools(builtin.tools);
      setCustomTools(custom.tools);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载失败', message: err.message });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadTools();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 合并：内置工具 + 自定义工具（自定义覆盖内置同名）
  const allTools: Array<{ name: string; description: string; category: string; is_custom: boolean; enabled: boolean; record?: CustomTool }> = [];
  const seen = new Set<string>();
  for (const c of customTools) {
    allTools.push({
      name: c.name,
      description: c.description || c.display_name || '',
      category: c.category || 'custom',
      is_custom: true,
      enabled: c.enabled,
      record: c,
    });
    seen.add(c.name);
  }
  for (const b of builtinTools) {
    if (seen.has(b.name)) continue;
    allTools.push({
      name: b.name,
      description: b.description || '',
      category: b.category || 'general',
      is_custom: false,
      enabled: true,
    });
  }

  // 分类统计
  const categories = ['all', ...Array.from(new Set(allTools.map(t => t.category)))];
  const customCount = customTools.length;

  const filtered = allTools.filter(t => {
    if (category !== 'all' && t.category !== category) return false;
    if (!search) return true;
    const s = search.toLowerCase();
    return t.name.toLowerCase().includes(s) || t.description.toLowerCase().includes(s);
  });

  async function handleDelete(id: string, name: string) {
    if (!confirm(`确定要删除自定义工具「${name}」吗？此操作不可撤销。`)) return;
    try {
      await deleteCustomTool(id);
      addToast({ type: 'success', title: '删除成功', message: `已删除自定义工具「${name}」` });
      setSelected(null);
      loadTools();
    } catch (err: any) {
      addToast({ type: 'error', title: '删除失败', message: err.message });
    }
  }

  async function handleToggle(record: CustomTool, enabled: boolean) {
    try {
      await toggleCustomTool(record.id, enabled);
      addToast({ type: 'success', title: enabled ? '已启用' : '已停用', message: `自定义工具「${record.name}」${enabled ? '已启用' : '已停用'}` });
      loadTools();
    } catch (err: any) {
      addToast({ type: 'error', title: '操作失败', message: err.message });
    }
  }

  async function handleCreateSubmit() {
    if (!createRequirement.trim()) {
      addToast({ type: 'warning', title: '请填写需求', message: '描述你想要创建什么样的工具' });
      return;
    }
    setCreateStreaming(true);
    setCreateLog([{ role: 'user', content: createRequirement }]);

    try {
      const session = await createSession({
        agent: 'tool_builder',
        model: undefined,
      });

      let prompt = createRequirement;
      if (createName.trim()) {
        prompt = `请帮我创建一个名为 "${createName.trim()}" 的新工具。\n\n具体需求如下：\n${createRequirement}\n\n请按照你的标准流程：先分析需求，使用 list_available_tools 检查避免重名，编写 handler 函数代码，使用 test_custom_tool 进行测试，最后使用 save_custom_tool 保存工具。`;
      } else {
        prompt = `请帮我创建一个新工具。\n\n具体需求如下：\n${createRequirement}\n\n请按照你的标准流程：先分析需求，使用 list_available_tools 检查避免重名，编写 handler 函数代码，使用 test_custom_tool 进行测试，最后使用 save_custom_tool 保存工具。`;
      }

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
          loadTools();
          addToast({ type: 'success', title: '工具创建完成', message: 'Tool Builder 已完成创建，请刷新列表查看新工具' });
        },
        // onError
        (err: Error) => {
          setCreateStreaming(false);
          addToast({ type: 'error', title: '创建失败', message: err.message || 'Tool Builder 返回错误' });
        },
        // onPrompt
        undefined,
        // onStep (tool calls)
        (step: ReasoningStep) => {
          if (step.type === 'tool_call' && step.tool) {
            setCreateLog(prev => [...prev, {
              role: 'tool',
              content: `🔧 调用工具: ${step.tool}`,
            }]);
          } else if (step.type === 'tool_output' && step.output) {
            const outputStr = typeof step.output === 'string' ? step.output : JSON.stringify(step.output);
            const preview = outputStr.length > 300 ? outputStr.substring(0, 300) + '...' : outputStr;
            setCreateLog(prev => [...prev, { role: 'tool', content: `📤 ${preview}` }]);
          }
        },
      );
      streamCtrlRef.current = controller;
    } catch (err: any) {
      setCreateStreaming(false);
      addToast({ type: 'error', title: '启动失败', message: err.message || '无法连接到 Tool Builder' });
    }
  }

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">工具管理</h1>
          <p className="text-sm text-gray-500 mt-1">
            共 {allTools.length} 个可用工具 · 其中自定义 {customCount} 个
          </p>
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
            + 新建工具
          </button>
          <button onClick={loadTools} className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl transition-colors">
            刷新
          </button>
        </div>
      </div>

      {/* Search + Category filter */}
      <div className="mb-6 flex flex-col md:flex-row gap-3">
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="搜索工具名称或描述..."
          className="w-full max-w-md px-4 py-2.5 bg-gray-900 border border-gray-800 rounded-xl text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none"
        />
        <div className="flex flex-wrap gap-1.5">
          {categories.map(cat => (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              className={`px-3 py-1.5 text-xs rounded-lg transition-colors border ${
                category === cat
                  ? 'bg-blue-600/10 text-blue-400 border-blue-600/30'
                  : 'bg-gray-900 text-gray-400 border-gray-800 hover:border-gray-700'
              }`}
            >
              {cat === 'all' ? '全部' : (CATEGORY_CN[cat] || cat)}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20 text-gray-600">
          {search || category !== 'all' ? '未找到匹配的工具' : '暂无可用工具'}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map(t => (
            <div
              key={t.name}
              className={`relative text-left p-5 rounded-2xl border transition-all hover:shadow-xl ${
                selected?.name === t.name
                  ? 'border-blue-500/40 bg-blue-600/5 shadow-lg shadow-blue-600/5'
                  : 'border-gray-800 bg-gray-900 hover:border-gray-700'
              }`}
            >
              <button
                onClick={() => {
                  if (selected?.name === t.name) { setSelected(null); return; }
                  setSelected(t);
                  if (t.is_custom && t.record && !t.record.code) {
                    setDetailLoading(true);
                    // record 已含 code，无需额外请求
                    setDetailLoading(false);
                  }
                }}
                className="w-full text-left"
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="w-10 h-10 bg-gray-800 rounded-xl flex items-center justify-center text-lg">
                    {t.is_custom ? '🧰' : '🔧'}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                      t.is_custom ? 'bg-amber-600/10 text-amber-400' : 'bg-blue-600/10 text-blue-400'
                    }`}>
                      {t.is_custom ? '自定义' : '预置'}
                    </span>
                    {t.is_custom && (
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                        t.enabled ? 'bg-emerald-600/10 text-emerald-400' : 'bg-gray-700 text-gray-400'
                      }`}>
                        {t.enabled ? '启用' : '停用'}
                      </span>
                    )}
                  </div>
                </div>
                <h3 className="text-sm font-semibold mb-1 font-mono">{t.name}</h3>
                <p className="text-xs text-gray-500 line-clamp-2 mb-3">{t.description || '无描述'}</p>
                <div className="flex flex-wrap gap-1">
                  <span className="text-[10px] px-2 py-0.5 bg-gray-800 rounded-md text-gray-500">
                    {CATEGORY_CN[t.category] || t.category}
                  </span>
                </div>
              </button>
              {t.is_custom && t.record && (
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-gray-800">
                  <button
                    onClick={(e) => { e.stopPropagation(); handleToggle(t.record!, !t.enabled); }}
                    className={`px-2.5 py-1 text-xs rounded-lg transition-colors ${
                      t.enabled
                        ? 'bg-gray-800 text-gray-400 hover:bg-red-600/10 hover:text-red-400'
                        : 'bg-emerald-600/10 text-emerald-400 hover:bg-emerald-600/20'
                    }`}
                  >
                    {t.enabled ? '停用' : '启用'}
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(t.record!.id, t.name); }}
                    className="ml-auto px-2.5 py-1 text-xs rounded-lg bg-red-600/10 text-red-400 hover:bg-red-600/20 transition-colors"
                  >
                    删除
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Tool Builder 创建工具弹窗 */}
      {showCreate && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60" onClick={() => { if (!createStreaming) setShowCreate(false); }}>
          <div className="w-full max-w-xl mx-4 bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl p-6 max-h-[85vh] flex flex-col" onClick={e => e.stopPropagation()}>
            {/* Header */}
            <div className="flex items-center justify-between mb-4 shrink-0">
              <div>
                <h2 className="text-lg font-semibold flex items-center gap-2">
                  <span className="text-blue-400">🧰</span>
                  {createStreaming ? 'Tool Builder 正在创建...' : createDone ? '工具创建完成' : 'AI 创建工具'}
                </h2>
                <p className="text-xs text-gray-500 mt-0.5">
                  {createStreaming
                    ? 'Tool Builder 正在分析需求、编写代码、测试并保存工具'
                    : createDone
                    ? 'Tool Builder 已完成工具生成，请刷新列表查看'
                    : '描述你的需求，Tool Builder 将自动生成完整的工具代码并保存'}
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
                      工具名称 <span className="text-gray-600">(可选，不填则让 AI 命名)</span>
                    </label>
                    <input
                      type="text"
                      value={createName}
                      onChange={e => setCreateName(e.target.value)}
                      placeholder="如 subdomain_bruteforce"
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
                      placeholder={`请描述你想要创建的工具，Tool Builder 将自动生成完整的 Python 代码、测试并保存。

例如：
"我需要一个子域名枚举工具，输入目标域名，使用字典进行暴力枚举，返回存在的子域名及解析的 IP 列表"

你可以指定：
• 工具用途 — 要做什么
• 输入参数 — 需要哪些参数（目标、超时、选项...）
• 输出格式 — 返回文本、结构化数据等
• 依赖工具 — 依赖哪些系统命令或 Python 库`}
                      rows={7}
                      className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none resize-none leading-relaxed"
                      onKeyDown={e => {
                        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                          handleCreateSubmit();
                        }
                      }}
                    />
                    <p className="text-[10px] text-gray-600 mt-1">提示：Ctrl+Enter 快速发送 | Tool Builder 将调用 test_custom_tool 测试并 save_custom_tool 自动保存</p>
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
                      <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                    </svg>
                    发送给 Tool Builder
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
                      <span>正在连接 Tool Builder...</span>
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
                        <div className="text-[10px] text-green-500/50 mb-0.5">▹ Tool Builder</div>
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
                        onClick={() => { loadTools(); addToast({ type: 'info', title: '已刷新', message: '工具列表已更新' }); }}
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
      {selected && (
        <div className="fixed inset-y-0 right-0 z-30 w-80 bg-gray-900 border-l border-gray-800 shadow-2xl animate-slide-right overflow-y-auto">
          <div className="p-5">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold font-mono">{selected.name}</h2>
                {selected.is_custom ? (
                  <span className="text-[10px] px-2 py-0.5 rounded-full font-medium bg-amber-600/10 text-amber-400">自定义</span>
                ) : (
                  <span className="text-[10px] px-2 py-0.5 rounded-full font-medium bg-blue-600/10 text-blue-400">预置</span>
                )}
              </div>
              <button onClick={() => setSelected(null)} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">
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
                    <span className="text-gray-500">来源</span>
                    <span className="font-medium">{selected.is_custom ? '自定义' : '预置'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">分类</span>
                    <span className="font-medium">{CATEGORY_CN[selected.category] || selected.category}</span>
                  </div>
                  {selected.is_custom && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">状态</span>
                      <span className={`font-medium ${selected.enabled ? 'text-emerald-400' : 'text-gray-400'}`}>
                        {selected.enabled ? '已启用' : '已停用'}
                      </span>
                    </div>
                  )}
                </div>
              </div>

              <div>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">描述</h3>
                <p className="text-sm text-gray-400">{selected.description || '无描述'}</p>
              </div>

              {selected.is_custom && selected.record && (
                <>
                  <div>
                    <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">参数 Schema</h3>
                    {selected.record.parameters ? (
                      <pre className="text-xs text-gray-400 bg-gray-800/60 rounded-lg p-3 whitespace-pre-wrap font-mono leading-relaxed max-h-48 overflow-y-auto">
                        {JSON.stringify(selected.record.parameters, null, 2)}
                      </pre>
                    ) : (
                      <p className="text-sm text-gray-600">自动从 handler 签名推导</p>
                    )}
                  </div>
                  <div>
                    <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 flex items-center gap-2">
                      工具代码
                      {detailLoading && <span className="w-3 h-3 border-2 border-gray-600 border-t-blue-400 rounded-full animate-spin" />}
                    </h3>
                    <pre className="text-xs text-gray-400 bg-gray-800/60 rounded-lg p-3 whitespace-pre-wrap font-mono leading-relaxed max-h-64 overflow-y-auto">
                      {selected.record.code || '无代码'}
                    </pre>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleToggle(selected.record!, !selected.enabled)}
                      className={`flex-1 px-4 py-2.5 rounded-xl text-sm transition-colors ${
                        selected.enabled
                          ? 'bg-gray-800 hover:bg-gray-700 text-gray-300'
                          : 'bg-emerald-600/10 hover:bg-emerald-600/20 border border-emerald-600/20 text-emerald-400'
                      }`}
                    >
                      {selected.enabled ? '停用此工具' : '启用此工具'}
                    </button>
                    <button
                      onClick={() => handleDelete(selected.record!.id, selected.name)}
                      className="flex-1 px-4 py-2.5 bg-red-600/10 hover:bg-red-600/20 border border-red-600/20 rounded-xl text-sm text-red-400 hover:text-red-300 transition-colors"
                    >
                      删除
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
