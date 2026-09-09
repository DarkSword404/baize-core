import { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { createSession, getModelConfig } from '../api/client';
import type { JSX } from 'react';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (sessionId: string) => void;
}

// 任务预设：覆盖多场景，用户可一键填入
const TASK_PRESETS: Array<{ label: string; value: string }> = [
  { label: '告警研判', value: '研判以下告警是否为真实攻击，给出定性结论与处置建议' },
  { label: 'CTF 解题', value: '分析这道 CTF 题目并给出解题思路与 flag' },
  { label: '渗透测试', value: '对授权目标完成一次渗透测试，发现并验证可利用漏洞' },
  { label: '应急响应', value: '针对当前安全事件执行取证分析与攻击链还原' },
];

export function CreateSessionModal({ open, onClose, onCreated }: Props): JSX.Element | null {
  const { addToast, addSession } = useApp();
  // 任务/问题描述（必填，黑板 origin 据此建立，reason 据此派发对应专项 agent）
  const [task, setTask] = useState('');
  // 目标范围（可选，渗透/扫描类任务填写）
  const [scope, setScope] = useState('');
  const [configuredModel, setConfiguredModel] = useState('');
  const [creating, setCreating] = useState(false);
  const [browserCollab, setBrowserCollab] = useState(false);

  useEffect(() => {
    if (open) {
      getModelConfig()
        .then(cfg => {
          if (cfg.configured) setConfiguredModel(cfg.model);
        })
        .catch(() => {});
    }
  }, [open]);

  async function handleCreate() {
    if (!task.trim()) {
      addToast({ type: 'error', title: '信息不完整', message: '请填写任务/问题描述' });
      return;
    }
    setCreating(true);
    try {
      // 默认协作模式：黑板驱动的多 agent 自主协作（通用安全助手）
      // - pattern = security_assistant：通用多场景（告警/CTF/运营/渗透/取证）
      // - scope（黑板 origin）= 任务描述；goal = 完成该任务
      //   黑板据此初始化 origin/goal/根 intent，reason 按任务性质派发专项 agent
      // - context.scope：可选的目标范围（渗透/扫描类），透传给 agent prompt
      const goalText = scope.trim()
        ? `完成上述任务${scope.trim() ? `（目标范围: ${scope.trim()}）` : ''}`
        : '完成上述任务';
      const session = await createSession({
        model: configuredModel || null,
        stateful: true,
        browser_collab: browserCollab,
        pattern: 'security_assistant',
        scope: task.trim(),      // 黑板 origin = 任务描述
        goal: goalText,         // 黑板 goal = 完成任务
      });
      addSession(session);
      addToast({
        type: 'success',
        title: '协作会话已创建',
        message: `任务：${task.trim().slice(0, 40)}${task.trim().length > 40 ? '...' : ''}`,
      });
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
          <div>
            <h2 className="text-lg font-semibold">新建对话</h2>
            <p className="text-[11px] text-gray-500 mt-0.5">
              安全助手 · 黑板驱动 · 多 agent 自主协作 · 覆盖告警/CTF/运营/渗透/取证
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <div className="space-y-4">
          {/* ── 任务/问题描述（必填，黑板 origin 据此建立） ── */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">
              任务 / 问题 <span className="text-red-400">*</span>
            </label>
            <textarea
              value={task}
              onChange={e => setTask(e.target.value)}
              placeholder="如：研判这条告警 / 解这道CTF / 对目标做渗透 / 分析这个事件..."
              rows={3}
              className="w-full px-3 py-2.5 bg-gray-800/50 border border-gray-800 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500/40 focus:bg-gray-800 resize-none"
            />
            <p className="text-[10px] text-gray-600 mt-1">黑板据此建立 origin 节点，reason 按任务性质自动派发对应专项 agent</p>
            <div className="flex flex-wrap gap-1.5 mt-2">
              {TASK_PRESETS.map(p => (
                <button
                  key={p.label}
                  onClick={() => setTask(p.value)}
                  className="text-[10px] px-2 py-1 rounded-md bg-gray-800/60 text-gray-400 hover:bg-purple-600/20 hover:text-purple-300 border border-gray-800 hover:border-purple-600/30 transition-all"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {/* ── 目标范围（可选，渗透/扫描类任务填写） ── */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">
              目标范围 <span className="text-gray-600">（可选）</span>
            </label>
            <input
              type="text"
              value={scope}
              onChange={e => setScope(e.target.value)}
              placeholder="如 10.0.0.5 / example.com / 告警原文 / CTF附件（渗透/扫描类填写）"
              className="w-full px-3 py-2.5 bg-gray-800/50 border border-gray-800 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500/40 focus:bg-gray-800"
            />
          </div>

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
            disabled={creating || !task.trim()}
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-xl text-sm font-semibold transition-all active:scale-[0.98] cursor-pointer disabled:cursor-not-allowed"
          >
            {creating ? '创建中...' : '开始对话'}
          </button>
        </div>
      </div>
    </div>
  );
}
