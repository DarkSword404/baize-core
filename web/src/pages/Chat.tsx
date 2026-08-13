import { useState, useEffect, useRef } from 'react';
import { useApp } from '../context/AppContext';
import { listSessions, getSession, deleteSession, streamMessage, cancelSession, listAgents, respondToPrompt, uploadAttachment, deleteAttachment, refineSessionExperience, createExperience } from '../api/client';
import type { PromptRequest, ReasoningStep, AttachmentInfo, ExperienceSignal } from '../api/client';
import { ChatMessage } from '../components/ChatMessage';
import { CreateSessionModal } from '../components/CreateSessionModal';
import type { ChatMessage as ChatMessageType, IntermediateData } from '../types';
import type { JSX } from 'react';

function genId() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }

/** 从各种 content 格式中提取纯文本（兼容 OpenAI Responses API content blocks） */
function extractContentText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((block: any) => {
        if (typeof block === 'string') return block;
        if (block?.text && typeof block.text === 'string') return block.text;
        if (block?.content) return extractContentText(block.content);
        return '';
      })
      .join('\n');
  }
  if (content && typeof content === 'object') {
    const obj = content as Record<string, unknown>;
    if (obj.text && typeof obj.text === 'string') return obj.text;
    if (obj.content) return extractContentText(obj.content);
  }
  return String(content ?? '');
}

/** 格式化工具参数（尝试 pretty-print JSON） */
function formatArguments(args: unknown): string {
  if (!args) return '';
  if (typeof args === 'string') {
    try { const p = JSON.parse(args); return JSON.stringify(p, null, 2); } catch { return args; }
  }
  if (typeof args === 'object') return JSON.stringify(args, null, 2);
  return String(args);
}

/** 将 history 中的任意条目转换为 ChatMessage，中间产物标记为 intermediate */
function buildChatMessage(h: any, fallbackTimestamp: string): ChatMessageType | null {
  const ts = h.timestamp || fallbackTimestamp;
  // 中间产物由后端以 type 字段标识（role 为 "intermediate" 或历史遗留的 "assistant"），
  // 必须先于 role 判断，否则会被当成 content 为空的普通消息渲染。
  const itemType = h.type || '';

  // 工具调用
  if (itemType === 'function_call') {
    const toolName = h.name || '工具';
    const args = formatArguments(h.arguments);
    return {
      id: genId(),
      role: 'tool',
      content: '',
      timestamp: ts,
      intermediates: [{
        itemType: 'function_call',
        label: `${toolName}`,
        detail: args || '(无参数)',
      }],
    };
  }

  // 工具输出
  if (itemType === 'function_call_output') {
    const raw = extractContentText(h.output);
    return {
      id: genId(),
      role: 'tool',
      content: '',
      timestamp: ts,
      intermediates: [{
        itemType: 'function_call_output',
        label: raw ? raw.slice(0, 80) + (raw.length > 80 ? '...' : '') : '(无输出)',
        detail: raw || '(无输出)',
      }],
    };
  }

  // 推理过程
  if (itemType.startsWith('reasoning')) {
    const summary = Array.isArray(h.summary)
      ? h.summary.map((s: any) => s.text || '').filter(Boolean).join('\n')
      : '';
    return {
      id: genId(),
      role: 'tool',
      content: '',
      timestamp: ts,
      intermediates: [{
        itemType: 'reasoning',
        label: summary ? summary.slice(0, 80) + (summary.length > 80 ? '...' : '') : '推理步骤',
        detail: summary || extractContentText(h.content) || '(无详情)',
      }],
    };
  }

  // 其他带 type 的中间产物（handoff 等）
  if (itemType) {
    const detail = extractContentText(h.content) || JSON.stringify(h, null, 2);
    return {
      id: genId(),
      role: 'tool',
      content: '',
      timestamp: ts,
      intermediates: [{
        itemType: 'handoff',
        label: `步骤 (${itemType})`,
        detail,
      }],
    };
  }

  // ── 正常对话消息（无 type 字段：用户提问 / 助手最终回复） ──
  if (h.role === 'user' || h.role === 'assistant') {
    return {
      id: genId(),
      role: h.role,
      content: extractContentText(h.content),
      timestamp: ts,
    };
  }

  // 其它未知角色（system 等）：按普通消息兜底展示
  if (h.role) {
    return {
      id: genId(),
      role: h.role,
      content: extractContentText(h.content),
      timestamp: ts,
    };
  }
  return null;
}

export function Chat(): JSX.Element {
  const {
    sessions, setSessions, activeSessionId, setActiveSessionId,
    messages, setMessages, isStreaming, setIsStreaming,
    addToast, setAgents, removeSession,
  } = useApp();

  const [input, setInput] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  // 是否跟随滚动到底部：用户主动上滚阅读思考/工具过程时关闭，回到底部附近时恢复
  const autoScrollRef = useRef(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // 待发送附件（用户选择后先上传到会话，随下一条消息发送）
  const [pendingFiles, setPendingFiles] = useState<AttachmentInfo[]>([]);
  const [uploading, setUploading] = useState(false);

  // Web interactive prompt
  const [pendingPrompt, setPendingPrompt] = useState<PromptRequest | null>(null);
  const [promptValue, setPromptValue] = useState('');
  const promptInputRef = useRef<HTMLInputElement>(null);

  // 长期记忆：经验提炼
  const [experienceSignal, setExperienceSignal] = useState<ExperienceSignal | null>(null);
  const [refining, setRefining] = useState(false);
  const [refineError, setRefineError] = useState('');
  const [refineDraft, setRefineDraft] = useState<{ sessionId: string; title: string; content: string; tags: string; scope: string } | null>(null);

  // Reasoning timeline panel
  const [reasoningPanelOpen, setReasoningPanelOpen] = useState(false);

  // Multi-agent tracking
  const [currentAgent, setCurrentAgent] = useState<string | null>(null);
  const [pipelinePhases, setPipelinePhases] = useState<Array<{ phase: number; agent: string; name: string }>>([]);

  // Collect all intermediate steps from all messages for the timeline
  const allSteps = messages.flatMap((m, mi) =>
    (m.intermediates || []).map((s, si) => ({ ...s, _msgIdx: mi, _stepIdx: si, _ts: m.timestamp }))
  );

  // Load sessions, agents on mount; auto-restore last active session
  useEffect(() => {
    Promise.all([
      listSessions().then(r => {
        setSessions(r.sessions);
        // Auto-restore last active session from localStorage
        const savedSessionId = localStorage.getItem('baize_active_session');
        if (savedSessionId && r.sessions.some(s => s.id === savedSessionId)) {
          setActiveSessionId(savedSessionId);
          // Load messages for the restored session
          getSession(savedSessionId).then(detail => {
            const msgs: ChatMessageType[] = detail.history
              .map((h: any) => buildChatMessage(h, detail.created_at))
              .filter(Boolean) as ChatMessageType[];
            setMessages(msgs);
          }).catch(() => {});
        }
      }).catch(() => {}),
      // 后端 /api/v1/agents 已统一返回内置 + 自定义智能体
      listAgents().then(r => {
        setAgents(r.agents);
      }).catch(() => {}),
    ]);
  }, []);

  // Persist activeSessionId to localStorage
  useEffect(() => {
    if (activeSessionId) {
      localStorage.setItem('baize_active_session', activeSessionId);
    }
  }, [activeSessionId]);

  // Scroll to bottom (仅在用户处于底部附近时跟随，避免打断阅读思考内容)
  useEffect(() => {
    if (autoScrollRef.current) {
      chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  // 监听消息容器滚动：用户上滚时暂停自动跟随，回到底部附近时恢复
  function handleMessagesScroll() {
    const el = messagesContainerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) {
      autoScrollRef.current = true;
    } else {
      autoScrollRef.current = false;
    }
  }

  const activeSession = sessions.find(s => s.id === activeSessionId);

  async function handleSelectSession(id: string) {
    setActiveSessionId(id);
    // Reset multi-agent tracking on session switch
    setCurrentAgent(null);
    setPipelinePhases([]);
    try {
      const detail = await getSession(id);
      // 保留全部条目：user/assistant 正常渲染，中间产物（tool_call/tool_output等）折叠展示
      const msgs: ChatMessageType[] = detail.history
        .map((h: any) => buildChatMessage(h, detail.created_at))
        .filter(Boolean) as ChatMessageType[];
      setMessages(msgs);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载会话失败', message: err.message });
    }
  }

  async function handleDeleteSession(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    const confirmed = window.confirm(
      '删除会话将同时清理该会话上传的附件与解压文件（不可恢复），且不保留该会话提炼的经验。确定删除？'
    );
    if (!confirmed) return;
    try {
      await deleteSession(id);
      removeSession(id);
      addToast({ type: 'info', title: '会话已删除' });
    } catch (err: any) {
      addToast({ type: 'error', title: '删除失败', message: err.message });
    }
  }

  /** 长期记忆：用户手动提炼经验（或确认 SSE 信号卡片） */
  async function handleRefineExperience(scope: string = 'auto') {
    if (!activeSessionId) return;
    // 后端 SSE 流不产生 agent_switched/handoff 事件，currentAgent 恒为 null，
    // 需用会话自身的 agent 兜底，否则按钮点击会静默失效。
    const agent = currentAgent || activeSession?.agent || 'default';
    setRefining(true);
    setRefineError('');
    try {
      const r = await refineSessionExperience(activeSessionId, {
        session_id: activeSessionId,
        agent,
        scope,
      });
      setRefineDraft({
        sessionId: activeSessionId,
        title: r.candidate.title,
        content: r.candidate.content,
        tags: (r.candidate.tags || []).join(', '),
        scope: r.candidate.scope || 'auto',
      });
    } catch (err: any) {
      setRefineError(err.message || '提炼失败，请检查模型配置');
    } finally {
      setRefining(false);
    }
  }

  /** 确认提炼结果并入库 */
  async function handleSaveRefined() {
    if (!refineDraft) return;
    try {
      await createExperience({
        title: refineDraft.title,
        content: refineDraft.content,
        scope: refineDraft.scope,
        tags: refineDraft.tags.split(',').map(t => t.trim()).filter(Boolean),
        source_session_id: refineDraft.sessionId,
      });
      setRefineDraft(null);
      setExperienceSignal(null);
      addToast({ type: 'success', title: '经验已入库', message: '可在「经验库」页面管理与查看' });
    } catch (err: any) {
      addToast({ type: 'error', title: '保存失败', message: err.message });
    }
  }

  async function handleRefreshSessions() {
    try {
      const r = await listSessions();
      setSessions(r.sessions);
    } catch {}
  }

  /** 通用：发送消息并启动 SSE 流 */
  function startInference(content: string, assistantId: string, attachmentIds?: string[]) {
    abortRef.current = streamMessage(
      activeSessionId!,
      { input: content, attachments: attachmentIds && attachmentIds.length ? attachmentIds : undefined },
      (text) => {
        setMessages(prev => prev.map((m: ChatMessageType) =>
          m.id === assistantId ? { ...m, content: m.content + text } : m
        ));
      },
      () => {
        setMessages(prev => prev.map((m: ChatMessageType) =>
          m.id === assistantId ? { ...m, isStreaming: false } : m
        ));
        setIsStreaming(false);
        handleRefreshSessions();
      },
      (err) => {
        setMessages(prev => prev.map((m: ChatMessageType) =>
          m.id === assistantId
            ? { ...m, content: m.content + `\n\n⚠️ 错误: ${err.message}`, isStreaming: false }
            : m
        ));
        setIsStreaming(false);
        addToast({ type: 'error', title: '推理错误', message: err.message });
      },
      (prompt) => {
        setPendingPrompt(prompt);
        setPromptValue('');
        setTimeout(() => promptInputRef.current?.focus(), 100);
      },
      (step: ReasoningStep) => {
        let intermediate: IntermediateData | null = null;
        if (step.type === 'reasoning' && step.text) {
          // 累积模型实时思考内容到 assistant 消息的一个 reasoning 中间产物
          setMessages(prev => prev.map((m: ChatMessageType) => {
            if (m.id !== assistantId) return m;
            const steps = m.intermediates || [];
            const existing = steps.findIndex(s => s.itemType === 'reasoning');
            if (existing >= 0) {
              const updated = [...steps];
              updated[existing] = { ...updated[existing], detail: updated[existing].detail + step.text! };
              return { ...m, intermediates: updated };
            }
            return {
              ...m,
              intermediates: [...steps, {
                itemType: 'reasoning' as const,
                label: '💭 思考中…',
                detail: step.text!,
              }],
            };
          }));
          return;
        }
        if (step.type === 'tool_call') {
          const argsStr = typeof step.arguments === 'string'
            ? step.arguments
            : JSON.stringify(step.arguments || {}, null, 2);
          intermediate = {
            itemType: 'function_call',
            label: step.tool || 'unknown',
            detail: argsStr,
          };
        } else if (step.type === 'tool_output') {
          const outStr = typeof step.output === 'string'
            ? step.output
            : JSON.stringify(step.output || '', null, 2);
          intermediate = {
            itemType: 'function_call_output',
            label: 'output',
            detail: outStr,
          };
        } else if (step.type === 'handoff' || step.type === 'agent_switched') {
          intermediate = {
            itemType: 'handoff',
            label: step.type === 'handoff'
              ? `${step.from_agent || '?'} → ${step.to_agent || '?'}`
              : step.agent || 'switched',
            detail: step.type === 'handoff'
              ? `Handoff from ${step.from_agent || '?'} to ${step.to_agent || '?'}`
              : `Agent switched to ${step.agent || 'unknown'}`,
          };
          // Track current agent
          if (step.type === 'agent_switched' && step.agent) {
            setCurrentAgent(step.agent);
          } else if (step.type === 'handoff' && step.to_agent) {
            setCurrentAgent(step.to_agent);
          }
        } else if (step.type === 'pipeline_step') {
          // Sequential pipeline phase start
          intermediate = {
            itemType: 'handoff',
            label: `🔗 阶段${step.phase}/${step.total}: ${step.phase_name || step.agent || ''}`,
            detail: `Sequential pipeline phase ${step.phase}/${step.total}: Agent "${step.agent}" — ${step.phase_name || ''}`,
          };
          if (step.agent) setCurrentAgent(step.agent);
          if (step.phase != null && step.agent) {
            setPipelinePhases((prev: Array<{phase: number; agent: string; name: string}>) => {
              if (prev.some(p => p.phase === step.phase)) return prev;
              return [...prev, { phase: step.phase!, agent: step.agent!, name: step.phase_name || `阶段 ${step.phase}` }];
            });
          }
        } else if (step.type === 'pipeline_phase_complete') {
          intermediate = {
            itemType: 'function_call_output',
            label: `✅ 阶段${step.phase}完成: ${step.agent || ''}`,
            detail: step.message ? `✓ ${step.message}` : 'Phase completed',
          };
        } else if (step.type === 'message') {
          if (step.text) {
            setMessages(prev => prev.map((m: ChatMessageType) =>
              m.id === assistantId && !m.content ? { ...m, content: step.text! } : m
            ));
          }
          return;
        } else if (step.type === 'error') {
          // Error step — show inline in intermediates
          intermediate = {
            itemType: 'function_call_output',
            label: `错误: ${step.error?.slice(0, 60) || '未知错误'}`,
            detail: step.error || '',
          };
        }
        if (intermediate) {
          setMessages(prev => prev.map((m: ChatMessageType) =>
            m.id === assistantId ? {
              ...m,
              intermediates: [...(m.intermediates || []), intermediate!],
            } : m
          ));
        }
      },
      (signal: ExperienceSignal) => {
        setExperienceSignal(signal);
        addToast({ type: 'info', title: '检测到可提炼经验', message: signal.reasons.join('；') });
      }
    );
  }

  async function handleIntervene() {
    // User is injecting instructions mid-stream — interrupt + continue
    const content = input.trim();
    if (!content || !activeSessionId) return;

    console.log('[Chat] Intervening:', { sessionId: activeSessionId, content: content.substring(0, 40) });

    // 1. Abort current stream + cancel backend task
    abortRef.current?.abort();
    try { await cancelSession(activeSessionId); } catch {}

    // 2. Mark current assistant message as intervened
    setMessages(prev => {
      const updated = [...prev];
      for (let i = updated.length - 1; i >= 0; i--) {
        if (updated[i].role === 'assistant' && updated[i].isStreaming) {
          // Keep accumulated content but mark interrupted
          updated[i] = {
            ...updated[i],
            isStreaming: false,
            content: updated[i].content
              ? updated[i].content + '\n\n--- ⏸️ 用户介入，当前任务已中断 ---'
              : '(已中断，未生成内容)',
          };
          break;
        }
      }
      return updated;
    });

    // 3. Add user intervention message
    const interventionMsg: ChatMessageType = {
      id: genId(),
      role: 'user',
      content: `[介入指令] ${content}`,
      timestamp: new Date().toISOString(),
    };
    setMessages(prev => [...prev, interventionMsg]);
    setInput('');

    // 4. Brief pause to let backend cleanup, then restart
    await new Promise(r => setTimeout(r, 500));

    // 5. Start new assistant stream
    const assistantId = genId();
    const assistantMsg: ChatMessageType = {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      isStreaming: true,
    };
    setMessages(prev => [...prev, assistantMsg]);
    setIsStreaming(true);
    startInference(content, assistantId);
  }

  async function handleSend() {
    const content = input.trim();
    if (!content || !activeSessionId) return;

    // ═══ 流式中途介入：输入框可用，发送 = 中断当前流并重新推理 ═══
    if (isStreaming) {
      await handleIntervene();
      return;
    }

    console.log('[Chat] handleSend:', { sessionId: activeSessionId, content: content.substring(0, 40) });
    // 新消息发送时恢复自动跟随到底部
    autoScrollRef.current = true;

    // 上传待发送的附件，收集 file_id
    const attachIds: string[] = [];
    if (pendingFiles.length) {
      setUploading(true);
      for (const pf of pendingFiles) {
        // pendingFiles 中已含上传后的 AttachmentInfo（file_id），直接用其 id
        if (pf.file_id) attachIds.push(pf.file_id);
      }
      setUploading(false);
    }

    const userMsg: ChatMessageType = {
      id: genId(),
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
      attachments: pendingFiles.map(f => f.filename),
    };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setPendingFiles([]);

    const assistantId = genId();
    const assistantMsg: ChatMessageType = {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      isStreaming: true,
    };
    setMessages(prev => [...prev, assistantMsg]);
    setIsStreaming(true);

    startInference(content, assistantId, attachIds);
  }

  /** 选择附件文件：先上传到会话，成功后将附件信息加入待发送列表 */
  async function handlePickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (!files.length || !activeSessionId) return;
    setUploading(true);
    const ok: AttachmentInfo[] = [];
    for (const f of files) {
      try {
        const att = await uploadAttachment(activeSessionId, f);
        ok.push(att);
      } catch (err: any) {
        addToast({ type: 'error', title: `上传失败: ${f.name}`, message: err.message });
      }
    }
    setPendingFiles(prev => [...prev, ...ok]);
    setUploading(false);
    if (ok.length) addToast({ type: 'success', title: '附件已上传', message: `已添加 ${ok.length} 个附件` });
  }

  function handleRemovePendingFile(fileId: string) {
    setPendingFiles(prev => prev.filter(f => f.file_id !== fileId));
    if (activeSessionId) {
      deleteAttachment(activeSessionId, fileId).catch(() => {});
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  async function handleCancel() {
    if (!activeSessionId) return;
    abortRef.current?.abort();
    try { await cancelSession(activeSessionId); } catch {}
    setMessages(prev => prev.map(m => m.isStreaming ? { ...m, isStreaming: false } : m));
    setIsStreaming(false);
  }

  async function handlePromptSubmit() {
    if (!pendingPrompt || !activeSessionId) return;
    const response = promptValue.trim();
    if (pendingPrompt.is_password && !response) return; // require non-empty for password
    console.log('[Chat] Submitting prompt:', pendingPrompt.prompt_id, 'value:', response);
    setPendingPrompt(null);
    setPromptValue('');
    // 用户留空提交视为拒绝（不再依赖选项里是否含 Cancel）
    const rejected = !response;
    try {
      await respondToPrompt(activeSessionId, pendingPrompt.prompt_id, response, rejected);
    } catch (err: any) {
      addToast({ type: 'error', title: '提交失败', message: err.message });
    }
  }

  function handlePromptCancel() {
    if (!pendingPrompt || !activeSessionId) return;
    console.log('[Chat] Rejecting prompt:', pendingPrompt.prompt_id);
    respondToPrompt(activeSessionId, pendingPrompt.prompt_id, '', true).catch(() => {});
    setPendingPrompt(null);
    setPromptValue('');
  }

  return (
    <div className="h-full flex">
      {/* Session Sidebar */}
      <div className={`${sidebarOpen ? 'w-64 min-w-[256px]' : 'w-0 min-w-0'} border-r border-gray-800 bg-gray-900/50 flex flex-col transition-all duration-200 overflow-hidden`}>
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-400">会话记录</h2>
          <div className="flex gap-1">
            <button onClick={handleRefreshSessions} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-500 hover:text-gray-300 transition-colors" title="刷新">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
            <button onClick={() => setShowCreateModal(true)} className="p-1.5 rounded-lg hover:bg-gray-800 text-blue-400 hover:text-blue-300 transition-colors" title="新建会话">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" d="M12 4v16m8-8H4" />
              </svg>
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {sessions.length === 0 ? (
            <p className="text-xs text-gray-600 text-center py-8 px-4">暂无会话，点击 + 创建</p>
          ) : (
            sessions.map(s => (
              <div
                key={s.id}
                onClick={() => handleSelectSession(s.id)}
                className={`group flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer text-sm mb-0.5 transition-all ${
                  s.id === activeSessionId
                    ? 'bg-blue-600/10 border border-blue-600/20 text-blue-300'
                    : 'hover:bg-gray-800/60 text-gray-300 border border-transparent'
                }`}
              >
                <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${s.id === activeSessionId ? 'bg-blue-400' : 'bg-gray-600'}`} />
                <div className="flex-1 min-w-0">
                  <div className="font-medium truncate text-xs">{s.agent}</div>
                  <div className="text-[10px] text-gray-600 truncate">{s.model} · {s.history_length}条</div>
                </div>
                <button
                  onClick={e => handleDeleteSession(s.id, e)}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded text-gray-600 hover:text-red-400 hover:bg-red-400/10 transition-all flex-shrink-0"
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
                  </svg>
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Chat Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Chat Header */}
        <div className="px-5 py-3 border-b border-gray-800 flex items-center gap-3 bg-gray-900/30">
          <button onClick={() => setSidebarOpen(!sidebarOpen)} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-500 transition-colors">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" d={sidebarOpen ? 'M11 19l-7-7 7-7m8 14l-7-7 7-7' : 'M13 5l7 7-7 7M5 5l7 7-7 7'} />
            </svg>
          </button>
          {activeSession ? (
            <>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold truncate flex items-center gap-1.5">
                  {activeSession.agent}
                  {activeSession.pattern && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-600/20 text-purple-400 flex-shrink-0">
                      {activeSession.pattern === 'vuln_to_report' ? '流水线' : activeSession.pattern}
                    </span>
                  )}
                  {currentAgent && isStreaming && currentAgent !== activeSession.agent && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-600/20 text-green-400 flex-shrink-0 animate-pulse">
                      {currentAgent}
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-gray-600 flex items-center gap-1">
                  {/* Single model display (read-only, configured in Settings) */}
                  <span className="px-1.5 py-0.5 rounded border border-transparent text-gray-400">
                    {activeSession.model || '未配置模型'}
                  </span>
                  <span>· {activeSession.history_length} 条消息</span>
                  {activeSession.pattern && activeSession.agent_stack && activeSession.agent_stack.length > 1 && (
                    <span className="text-purple-500">
                      {' · '}{activeSession.agent_stack.join(' → ')}
                    </span>
                  )}
                </div>
                {/* Pipeline phase indicator */}
                {pipelinePhases.length > 0 && (
                  <div className="flex items-center gap-1 mt-1 overflow-x-auto">
                    {pipelinePhases.map((p, i) => (
                      <span key={i} className={`text-[10px] px-1.5 py-0.5 rounded flex items-center gap-0.5 flex-shrink-0 ${
                        p.agent === currentAgent ? 'bg-green-600/20 text-green-400' : 'bg-gray-800 text-gray-500'
                      }`}>
                        {p.agent === currentAgent && <span className="w-1 h-1 rounded-full bg-green-400" />}
                        {p.name}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              {isStreaming && (
                <button onClick={handleCancel} className="px-3 py-1.5 bg-red-600/10 hover:bg-red-600/20 text-red-400 text-xs rounded-lg border border-red-600/20 transition-colors">
                  停止生成
                </button>
              )}
              <button
                onClick={() => setReasoningPanelOpen(!reasoningPanelOpen)}
                className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                  reasoningPanelOpen
                    ? 'bg-purple-600/10 border-purple-600/20 text-purple-400'
                    : 'bg-gray-800/50 border-gray-700 text-gray-500 hover:text-gray-300 hover:border-gray-600'
                }`}
                title="推理过程时间线"
              >
                {allSteps.length > 0 ? `推理 (${allSteps.length})` : '推理'}
              </button>
            </>
          ) : (
            <div className="flex-1 text-sm text-gray-600">
              选择一个会话或创建新的渗透会话开始对话
            </div>
          )}
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-xs font-medium transition-colors flex-shrink-0"
          >
            + 新建会话
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-5 py-4" ref={messagesContainerRef} onScroll={handleMessagesScroll}>
          {!activeSessionId ? (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className="w-16 h-16 bg-gray-800 rounded-2xl flex items-center justify-center mb-4">
                <svg className="w-8 h-8 text-gray-600" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold mb-1">白泽对话渗透</h3>
              <p className="text-sm text-gray-600 max-w-md">
                选择一个智能体，在对话中引导它执行安全渗透任务。所有AI操作将被完整记录。
              </p>
              <button
                onClick={() => setShowCreateModal(true)}
                className="mt-6 px-6 py-2.5 bg-blue-600 hover:bg-blue-500 rounded-xl text-sm font-semibold transition-all"
              >
                创建你的第一个渗透会话
              </button>
            </div>
          ) : messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className="w-12 h-12 bg-gray-800 rounded-full flex items-center justify-center mb-3">
                <svg className="w-6 h-6 text-gray-500" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
                </svg>
              </div>
              <p className="text-sm text-gray-500">输入你的渗透指令开始对话</p>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto">
              {messages.map(msg => (
                <ChatMessage key={msg.id} msg={msg} />
              ))}
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        {activeSessionId && (
          <div className="px-5 py-4 border-t border-gray-800 bg-gray-900/30">
            {isStreaming && (
              <div className="max-w-3xl mx-auto mb-2 flex items-center gap-2">
                <div className="flex items-center gap-1.5 bg-amber-600/10 border border-amber-600/20 rounded-lg px-2.5 py-1">
                  <div className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                  <span className="text-[11px] text-amber-400/80">AI 正在执行，输入指令可中途介入调整</span>
                </div>
              </div>
            )}
            {/* 待发送附件列表 */}
            {pendingFiles.length > 0 && (
              <div className="max-w-3xl mx-auto mb-2 flex flex-wrap gap-1.5">
                {pendingFiles.map(f => (
                  <span key={f.file_id} className="inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-lg bg-blue-600/20 border border-blue-600/30 text-blue-100">
                    <span>{['🖼️','💻','📦','📄','📎'][['image','code','archive','document','other'].indexOf(f.file_type) >= 0 ? ['image','code','archive','document','other'].indexOf(f.file_type) : 4]}</span>
                    <span className="max-w-[160px] truncate">{f.filename}</span>
                    <button onClick={() => handleRemovePendingFile(f.file_id)} className="ml-1 text-blue-300 hover:text-red-400" title="移除">✕</button>
                  </span>
                ))}
              </div>
            )}
            {/* 长期记忆：手动提炼入口 */}
            {!isStreaming && activeSession && (
              <button
                onClick={() => handleRefineExperience('auto')}
                disabled={refining}
                className="max-w-3xl mx-auto w-full mb-3 px-4 py-2.5 text-xs bg-gray-900/60 border border-dashed border-gray-700 hover:border-blue-600/40 hover:bg-blue-600/5 text-gray-500 hover:text-blue-400 rounded-xl transition-colors disabled:opacity-50"
              >
                {refining ? '⏳ 正在提炼本次会话经验...' : '✨ 提炼本会话经验（沉淀为可复用经验）'}
              </button>
            )}
            {/* 长期记忆：自动检测到可提炼经验 */}
            {experienceSignal && !refineDraft && !refining && (
              <div className="max-w-3xl mx-auto mb-3 rounded-xl border border-blue-600/30 bg-blue-600/5 p-4">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-blue-400">🧠</span>
                  <span className="text-sm font-medium text-blue-300">检测到可沉淀的经验</span>
                </div>
                <ul className="text-xs text-gray-400 space-y-1 mb-3">
                  {experienceSignal.reasons.map((r, i) => (
                    <li key={i}>· {r}</li>
                  ))}
                </ul>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleRefineExperience('auto')}
                    className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
                  >
                    提炼为经验
                  </button>
                  <button
                    onClick={() => setExperienceSignal(null)}
                    className="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded-lg transition-colors"
                  >
                    忽略
                  </button>
                </div>
              </div>
            )}
            <div className="max-w-3xl mx-auto flex gap-3 items-end">
              {/* 附件上传按钮 */}
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={isStreaming || uploading}
                className="h-12 w-12 flex items-center justify-center rounded-xl bg-gray-800 border border-gray-700 hover:border-blue-500 hover:bg-gray-700 text-gray-400 hover:text-blue-400 transition-colors flex-shrink-0 disabled:opacity-40"
                title="上传附件（图片/代码/压缩包/文档）"
              >
                {uploading ? (
                  <svg className="w-5 h-5 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6m0 0v6m0-6h6m-6 0H6" /></svg>
                ) : (
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
                  </svg>
                )}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={handlePickFiles}
              />
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={false}
                placeholder={isStreaming
                  ? '输入介入指令，引导 AI 调整方向... (Enter 发送)'
                  : '输入渗透指令... (Enter 发送，Shift+Enter 换行)'}
                rows={1}
                className={`flex-1 px-4 py-3 rounded-xl text-sm text-gray-100 placeholder-gray-600 resize-none outline-none transition-colors ${
                  isStreaming
                    ? 'bg-gray-800/60 border border-amber-600/30 focus:border-amber-500'
                    : 'bg-gray-800 border border-gray-700 focus:border-blue-500'
                }`}
                style={{ minHeight: '48px', maxHeight: '160px' }}
                onInput={e => {
                  const el = e.target as HTMLTextAreaElement;
                  el.style.height = 'auto';
                  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
                }}
              />
              {isStreaming ? (
                <>
                  <button
                    onClick={handleCancel}
                    className="h-12 px-3 flex items-center gap-1.5 bg-red-600/10 hover:bg-red-600/20 text-red-400 text-xs rounded-xl border border-red-600/20 transition-colors flex-shrink-0"
                    title="停止生成"
                  >
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1" /></svg>
                    <span className="hidden sm:inline">停止</span>
                  </button>
                  <button
                    onClick={handleSend}
                    disabled={!input.trim()}
                    className="h-12 px-3 flex items-center gap-1.5 bg-amber-600 hover:bg-amber-500 disabled:bg-gray-700 disabled:text-gray-600 text-white text-xs rounded-xl transition-all flex-shrink-0 active:scale-95"
                    title="介入并发送新指令"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                    </svg>
                    <span className="hidden sm:inline">介入</span>
                  </button>
                </>
              ) : (
                <button
                  onClick={handleSend}
                  disabled={!input.trim()}
                  className="h-12 w-12 flex items-center justify-center bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-600 rounded-xl transition-all flex-shrink-0 active:scale-95"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ═══ 推理过程时间线面板 ═══ */}
      {activeSessionId && (
        <div className={`${reasoningPanelOpen ? 'w-80 min-w-[320px]' : 'w-0 min-w-0'} border-l border-gray-800 bg-gray-900/30 flex flex-col transition-all duration-200 overflow-hidden`}>
          <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-purple-400" />
              <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">推理时间线</h2>
              {allSteps.length > 0 && (
                <span className="text-[10px] bg-gray-800 px-1.5 py-0.5 rounded text-gray-500">{allSteps.length}步</span>
              )}
            </div>
            <button onClick={() => setReasoningPanelOpen(false)} className="p-1 rounded hover:bg-gray-800 text-gray-600 hover:text-gray-400 transition-colors">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" d="M18 6L6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div
            className="flex-1 overflow-y-auto px-3 py-2"
            ref={(el) => {
              if (el && reasoningPanelOpen) {
                // 仅在面板本就接近底部时跟随最新推理步骤，避免打断阅读
                if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) {
                  el.scrollTop = el.scrollHeight;
                }
              }
            }}
          >
            {allSteps.length === 0 ? (
              <p className="text-xs text-gray-600 text-center py-12">
                {isStreaming ? '等待推理步骤...' : '暂无推理记录'}
              </p>
            ) : (
              <div className="relative pl-5 border-l border-gray-800 ml-1.5 space-y-0.5">
                {/* Group steps by agent when in pipeline mode */}
                {(() => {
                  // Build agent color palette
                  const agentColors: Record<string, { bg: string; border: string; text: string; dotBg: string; dotBorder: string }> = {};
                  const palette = [
                    { bg: 'bg-blue-500/5', border: 'border-blue-500/10', text: 'text-blue-400', dotBg: 'bg-blue-500/30', dotBorder: 'border-blue-500' },
                    { bg: 'bg-green-500/5', border: 'border-green-500/10', text: 'text-green-400', dotBg: 'bg-green-500/30', dotBorder: 'border-green-500' },
                    { bg: 'bg-amber-500/5', border: 'border-amber-500/10', text: 'text-amber-400', dotBg: 'bg-amber-500/30', dotBorder: 'border-amber-500' },
                    { bg: 'bg-purple-500/5', border: 'border-purple-500/10', text: 'text-purple-400', dotBg: 'bg-purple-500/30', dotBorder: 'border-purple-500' },
                    { bg: 'bg-rose-500/5', border: 'border-rose-500/10', text: 'text-rose-400', dotBg: 'bg-rose-500/30', dotBorder: 'border-rose-500' },
                    { bg: 'bg-cyan-500/5', border: 'border-cyan-500/10', text: 'text-cyan-400', dotBg: 'bg-cyan-500/30', dotBorder: 'border-cyan-500' },
                  ];

                  const getAgentColor = (agent: string | undefined) => {
                    if (!agent) return palette[0];
                    if (!agentColors[agent]) {
                      const idx = Object.keys(agentColors).length % palette.length;
                      agentColors[agent] = palette[idx];
                    }
                    return agentColors[agent];
                  };

                  let lastAgent: string | undefined;

                  return allSteps.map((step, idx) => {
                    const isToolCall = step.itemType === 'function_call';
                    const isToolOutput = step.itemType === 'function_call_output';
                    const isHandoff = step.itemType === 'handoff';
                    const isLast = idx === allSteps.length - 1;

                    // Use agent-based coloring when available
                    const stepAgent: string | undefined = (step as any).phase_agent;
                    const isNewAgent = stepAgent && stepAgent !== lastAgent;
                    if (stepAgent) lastAgent = stepAgent;

                    const c = getAgentColor(stepAgent || (step as any).agent);
                    const colorClasses = `${c.bg} ${c.border}`;
                    const iconChar = isToolCall ? '🔧' : isToolOutput ? '📤' : isHandoff ? '🔄' : '•';

                    return (
                      <div key={idx}>
                        {/* Agent group header */}
                        {isNewAgent && stepAgent && (
                          <div className="flex items-center gap-1.5 py-1 -ml-5">
                            <span className={`w-2.5 h-2.5 rounded-full border-2 ${c.dotBorder}`} />
                            <span className={`text-[10px] font-semibold ${c.text} uppercase tracking-wide`}>{stepAgent}</span>
                            {(step as any).phase_name && (
                              <span className="text-[10px] text-gray-600">· {(step as any).phase_name}</span>
                            )}
                          </div>
                        )}
                        <div
                          className={`relative -ml-5 pl-5 py-1.5 rounded-r-lg border-l-0 ${colorClasses} ${isLast && isStreaming ? 'animate-pulse' : ''}`}
                        >
                          <div className={`absolute left-0 top-2 -translate-x-[5px] w-2.5 h-2.5 rounded-full border-2 ${c.dotBg} ${c.dotBorder}`} />
                          <div className="flex items-start gap-2">
                            <span className="text-[11px] flex-shrink-0 mt-px">{iconChar}</span>
                            <div className="min-w-0 flex-1">
                              <div className={`text-[11px] font-medium truncate ${c.text}`}>
                                {step.label}
                              </div>
                              {step.detail && (
                                <div className="text-[10px] text-gray-600 mt-0.5 break-all line-clamp-2" title={step.detail}>
                                  {step.detail.slice(0, 100)}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  });
                })()}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 长期记忆：经验候选确认浮层 */}
      {refineDraft && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center">
          <div className="bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-2xl mx-4 shadow-2xl shadow-black/40 overflow-hidden max-h-[85vh] flex flex-col">
            <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-200">📝 经验候选（可编辑后保存）</h3>
              <button onClick={() => setRefineDraft(null)} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>
            <div className="px-6 py-4 overflow-y-auto space-y-4">
              <div>
                <label className="block text-xs text-gray-500 mb-1.5">标题</label>
                <input
                  type="text"
                  value={refineDraft.title}
                  onChange={e => setRefineDraft({ ...refineDraft, title: e.target.value })}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1.5">复盘内容</label>
                <textarea
                  value={refineDraft.content}
                  onChange={e => setRefineDraft({ ...refineDraft, content: e.target.value })}
                  rows={8}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500 resize-y"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1.5">标签（逗号分隔）</label>
                <input
                  type="text"
                  value={refineDraft.tags}
                  onChange={e => setRefineDraft({ ...refineDraft, tags: e.target.value })}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1.5">作用域</label>
                <select
                  value={refineDraft.scope}
                  onChange={e => setRefineDraft({ ...refineDraft, scope: e.target.value })}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500"
                >
                  <option value="global">全局经验（所有智能体可用）</option>
                  <option value={`agent:${activeSession?.agent || currentAgent || 'default'}`}>
                    智能体专属: {activeSession?.agent || currentAgent || 'default'}
                  </option>
                </select>
              </div>
              {refineError && <p className="text-xs text-red-400">{refineError}</p>}
            </div>
            <div className="px-6 py-3 border-t border-gray-800 flex justify-end gap-2">
              <button
                onClick={() => setRefineDraft(null)}
                className="px-4 py-2 text-xs text-gray-400 hover:text-gray-200 rounded-lg"
              >
                放弃
              </button>
              <button
                onClick={handleSaveRefined}
                className="px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
              >
                保存到经验库
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Interactive Prompt Dialog */}
      {pendingPrompt && activeSessionId && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-lg mx-4 shadow-2xl shadow-black/40 overflow-hidden">
            {/* Header */}
            <div className="px-6 py-4 border-b border-gray-800 flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-blue-600/10 flex items-center justify-center flex-shrink-0">
                <svg className="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-gray-200">{pendingPrompt.title}</h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  {pendingPrompt.prompt_type === 'sudo_password' ? '需要密码继续执行命令' : '需要确认才能继续'}
                </p>
              </div>
            </div>

            {/* Body */}
            <div className="px-6 py-4">
              <pre className="text-xs text-gray-400 bg-gray-950 rounded-lg p-3 mb-4 max-h-32 overflow-y-auto font-mono leading-relaxed whitespace-pre-wrap break-all">
                {pendingPrompt.message}
              </pre>
              {pendingPrompt.is_password ? (
                <div>
                  <label className="block text-xs text-gray-500 mb-1.5">输入密码</label>
                  <input
                    ref={promptInputRef}
                    type="password"
                    value={promptValue}
                    onChange={e => setPromptValue(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handlePromptSubmit()}
                    placeholder="sudo 密码..."
                    className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500 transition-colors"
                  />
                </div>
              ) : (
                <p className="text-sm text-gray-400">
                  {pendingPrompt.options.length > 0
                    ? `可选: ${pendingPrompt.options.join(' / ')}`
                    : '请输入你的回复'}
                </p>
              )}
            </div>

            {/* Footer */}
            <div className="px-6 py-3 border-t border-gray-800 flex justify-end gap-2">
              <button
                onClick={handlePromptCancel}
                className="px-4 py-2 rounded-lg text-xs font-medium text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors"
              >
                {pendingPrompt.options.includes('Cancel') ? '取消' : '取消'}
              </button>
              <button
                onClick={handlePromptSubmit}
                disabled={pendingPrompt.is_password && !promptValue.trim()}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-600 rounded-lg text-xs font-medium transition-colors"
              >
                {pendingPrompt.options.includes('Submit') ? '提交' : pendingPrompt.options.includes('Allow') ? '允许' : '确认'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Modal */}
      <CreateSessionModal
        open={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onCreated={(id) => {
          handleSelectSession(id);
        }}
      />
    </div>
  );
}
