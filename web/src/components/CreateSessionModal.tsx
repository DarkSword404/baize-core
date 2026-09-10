import { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { createSession, getModelConfig } from '../api/client';
import type { JSX } from 'react';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (sessionId: string) => void;
}

export function CreateSessionModal({ open, onClose, onCreated }: Props): JSX.Element | null {
  const { addToast, addSession } = useApp();
  const [configuredModel, setConfiguredModel] = useState('');
  const [creating, setCreating] = useState(false);
  // 共享浏览器协作：默认开启
  const [browserCollab, setBrowserCollab] = useState(true);

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
    setCreating(true);
    try {
      // 默认协作模式：黑板驱动的多 agent 自主协作（通用安全助手）
      // - 对话不使用任何预置流水线模板，由后端黑板 + 动态 agent 自动编排
      // - 浏览器协作默认开启
      // - 不在此处预填任务/目标：具体任务由用户在对话中用自然语言描述，
      //   黑板会据首条消息建立 origin/goal，reason 按任务性质派发对应临时 agent
      const session = await createSession({
        model: configuredModel || null,
        stateful: true,
        browser_collab: browserCollab,
        scope: 'TASK',
        goal: '通用安全助手：用户在对话中描述任务，按任务性质协作完成',
      });
      addSession(session);
      addToast({ type: 'success', title: '新对话已创建' });
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
              安全助手 · 黑板驱动 · 多 agent 自主协作
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <div className="space-y-4">
          {/* 说明 */}
          <div className="px-3 py-3 rounded-xl border border-gray-800 bg-gray-900/60">
            <div className="text-xs text-gray-400 leading-relaxed">
              创建空对话后，在输入框用自然语言描述任务即可。例如：
              <span className="text-gray-300"> "研判这条告警是否真实攻击"</span>、
              <span className="text-gray-300">"对 1.2.3.4 做授权渗透，仅限 80 端口的 web 站点"</span>。
              复杂约束（目标范围、排除项等）直接写在任务里更准确。
            </div>
          </div>

          {/* 模型 */}
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

          {/* 共享浏览器协作开关（默认开启） */}
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
            disabled={creating}
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-xl text-sm font-semibold transition-all active:scale-[0.98] cursor-pointer disabled:cursor-not-allowed"
          >
            {creating ? '创建中...' : '新建对话'}
          </button>
        </div>
      </div>
    </div>
  );
}
