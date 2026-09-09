import { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { createSession, getModelConfig } from '../api/client';
import type { JSX } from 'react';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (sessionId: string) => void;
}

// 常见 goal 预设，用户可一键填入（仅协作模式显示）
const GOAL_PRESETS: Array<{ label: string; value: string }> = [
  { label: '拿到 shell', value: '拿到目标 shell 权限' },
  { label: '拿到 flag', value: '拿到目标 flag' },
  { label: '漏洞验证', value: '发现并验证所有可利用漏洞，给出 PoC' },
  { label: '完整渗透', value: '完成侦察 → 渗透 → 后利用全流程，输出报告' },
];

export function CreateSessionModal({ open, onClose, onCreated }: Props): JSX.Element | null {
  const { addToast, addSession } = useApp();
  // 协作模式开关：默认关闭 → 通用对话（CTF/告警研判/任意问答）
  // 开启 → 需填 scope+goal，走黑板驱动的多 agent 协作 pipeline
  const [collabMode, setCollabMode] = useState(false);
  const [scope, setScope] = useState('');
  const [goal, setGoal] = useState('拿到目标 shell 权限');
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
    // 协作模式必须填 scope+goal；普通对话无此约束
    if (collabMode && (!scope.trim() || !goal.trim())) {
      addToast({ type: 'error', title: '信息不完整', message: '协作模式需填写目标范围和成功条件' });
      return;
    }
    setCreating(true);
    try {
      const req: Parameters<typeof createSession>[0] = {
        model: configuredModel || null,
        stateful: true,
        browser_collab: browserCollab,
      };
      let toastMsg = '通用对话会话已创建';
      if (collabMode) {
        // 协作模式：传 pattern + scope/goal，后端初始化黑板（origin/goal 节点），
        // stream_message 据 session.pattern 走 pentest_collab pipeline
        req.pattern = 'pentest_collab';
        req.scope = scope.trim();
        req.goal = goal.trim();
        toastMsg = `协作会话已创建：目标 ${scope.trim()} · ${goal.trim()}`;
      }
      const session = await createSession(req);
      addSession(session);
      addToast({ type: 'success', title: '会话已创建', message: toastMsg });
      onClose();
      onCreated(session.id);
    } catch (err: any) {
      addToast({ type: 'error', title: '创建失败', message: err.message });
    } finally {
      setCreating(false);
    }
  }

  if (!open) return null;

  // 协作模式下 scope/goal 才必填
  const collabIncomplete = collabMode && (!scope.trim() || !goal.trim());

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-md p-6 shadow-2xl animate-slide-up">
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 className="text-lg font-semibold">新建对话</h2>
            <p className="text-[11px] text-gray-500 mt-0.5">
              {collabMode ? '协作模式 · 黑板驱动 · 多智能体自主搜索' : '通用对话 · 渗透 / CTF / 告警研判 / 任意问答'}
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <div className="space-y-4">
          {/* ── 协作模式开关 ── */}
          <div className="flex items-center justify-between p-3 rounded-xl border border-gray-800 bg-gray-900/60">
            <div>
              <div className="text-xs font-medium text-gray-300">协作模式（多 agent 黑板调度）</div>
              <div className="text-[10px] text-gray-600 mt-0.5">
                {collabMode
                  ? '需填写目标范围 + 成功条件，黑板据此动态派发专项 agent'
                  : '关闭即为通用对话，可直接提问（CTF / 告警研判 / 渗透问答等）'}
              </div>
            </div>
            <button
              onClick={() => setCollabMode(c => !c)}
              className={`relative w-10 h-6 rounded-full transition-colors flex-shrink-0 ${collabMode ? 'bg-purple-600' : 'bg-gray-700'}`}
              role="switch"
              aria-checked={collabMode}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${collabMode ? 'translate-x-4' : ''}`}
              />
            </button>
          </div>

          {/* ── 协作模式才显示的目标范围 / 成功条件 ── */}
          {collabMode && (
            <>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">
                  目标范围 <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={scope}
                  onChange={e => setScope(e.target.value)}
                  placeholder="如 10.0.0.5 / example.com / 192.168.1.0/24"
                  className="w-full px-3 py-2.5 bg-gray-800/50 border border-gray-800 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500/40 focus:bg-gray-800"
                />
                <p className="text-[10px] text-gray-600 mt-1">授权范围内的 IP / 域名 / 网段，黑板据此建立 origin 节点</p>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">
                  成功条件 <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={goal}
                  onChange={e => setGoal(e.target.value)}
                  placeholder="如 拿到 shell / 拿到 flag / 验证所有可利用漏洞"
                  className="w-full px-3 py-2.5 bg-gray-800/50 border border-gray-800 rounded-lg text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500/40 focus:bg-gray-800"
                />
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {GOAL_PRESETS.map(p => (
                    <button
                      key={p.label}
                      onClick={() => setGoal(p.value)}
                      className="text-[10px] px-2 py-1 rounded-md bg-gray-800/60 text-gray-400 hover:bg-purple-600/20 hover:text-purple-300 border border-gray-800 hover:border-purple-600/30 transition-all"
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>
            </>
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
            disabled={creating || collabIncomplete}
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-xl text-sm font-semibold transition-all active:scale-[0.98] cursor-pointer disabled:cursor-not-allowed"
          >
            {creating ? '创建中...' : collabMode ? '启动协作' : '开始对话'}
          </button>
        </div>
      </div>
    </div>
  );
}
