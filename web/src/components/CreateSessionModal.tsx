import { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import {
  createSession,
  getModelConfig,
  bindContainer,
  listContainers,
} from '../api/client';
import type { ContainerInfo, TaskType } from '../types';
import { TASK_TYPE_OPTIONS } from '../types';
import type { JSX } from 'react';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (sessionId: string) => void;
}

const TASK_TYPE_ICONS: Record<TaskType, string> = {
  general: '💬',
  pentest: '🛡️',
  ctf: '🚩',
  forensics: '🔍',
};

export function CreateSessionModal({ open, onClose, onCreated }: Props): JSX.Element | null {
  const { addToast, addSession } = useApp();
  const [configuredModel, setConfiguredModel] = useState('');
  const [creating, setCreating] = useState(false);
  // 共享浏览器协作：默认开启
  const [browserCollab, setBrowserCollab] = useState(true);
  // 任务类型（用户选择，非空时后端跳过 LLM 自动分类）
  const [taskType, setTaskType] = useState<TaskType>('pentest');
  // 可用容器列表（池中 available 状态）
  const [availableContainers, setAvailableContainers] = useState<ContainerInfo[]>([]);
  // 用户选择的容器名（空字符串=不绑定，本地运行）
  const [selectedContainer, setSelectedContainer] = useState<string>('');

  useEffect(() => {
    if (open) {
      getModelConfig()
        .then(cfg => {
          if (cfg.configured) setConfiguredModel(cfg.model);
        })
        .catch(() => {});
      // 拉取可用容器列表（session_id 为空且 status=active）
      listContainers()
        .then(r => {
          const avail = r.containers.filter(c => c.status === 'active' && !c.session_id);
          setAvailableContainers(avail);
          // 重置选择
          setSelectedContainer('');
        })
        .catch(() => setAvailableContainers([]));
    }
  }, [open]);

  async function handleCreate() {
    setCreating(true);
    try {
      // 默认协作模式：黑板驱动的多 agent 自主协作
      const session = await createSession({
        model: configuredModel || null,
        stateful: true,
        browser_collab: browserCollab,
        scope: 'TASK',
        goal: '用户在对话中描述任务，按任务性质协作完成',
        task_type: taskType,
      });
      addSession(session);

      // 按用户选择决定是否绑定容器
      if (selectedContainer) {
        try {
          await bindContainer(session.id, selectedContainer);
          addToast({
            type: 'success',
            title: '新任务已创建',
            message: `已绑定容器 ${selectedContainer}`,
          });
        } catch (err: any) {
          // 任务创建成功但容器绑定失败：保留任务，仅提示容器错误
          addToast({
            type: 'error',
            title: '容器绑定失败',
            message: err.message || '任务已创建，可在对话中手动绑定容器',
          });
        }
      } else {
        addToast({
          type: 'success',
          title: '新任务已创建',
          message: '默认本地运行，可按需在对话中绑定容器',
        });
      }

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
      <div className="relative bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-lg p-6 shadow-2xl animate-slide-up max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 className="text-lg font-semibold">新建任务</h2>
            <p className="text-[11px] text-gray-500 mt-0.5">
              选择任务类型 · 黑板驱动 · 多 agent 自主协作
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <div className="space-y-4">
          {/* 任务类型选择（卡片式，用户明确选择，避免 LLM 分类错误） */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-2">任务类型</label>
            <div className="grid grid-cols-2 gap-2">
              {TASK_TYPE_OPTIONS.map(opt => {
                const selected = taskType === opt.value;
                return (
                  <button
                    key={opt.value}
                    onClick={() => setTaskType(opt.value)}
                    disabled={creating}
                    className={`p-3 rounded-xl border text-left transition-all disabled:opacity-50 ${
                      selected
                        ? 'border-blue-600/50 bg-blue-600/10 ring-1 ring-blue-500/30'
                        : 'border-gray-800 bg-gray-900/60 hover:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-base">{TASK_TYPE_ICONS[opt.value]}</span>
                      <span className={`text-xs font-semibold ${selected ? 'text-blue-300' : 'text-gray-300'}`}>
                        {opt.label}
                      </span>
                    </div>
                    <div className="text-[10px] text-gray-500 leading-tight">{opt.desc}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* 说明 */}
          <div className="px-3 py-3 rounded-xl border border-gray-800 bg-gray-900/60">
            <div className="text-xs text-gray-400 leading-relaxed">
              创建空对话后，在输入框用自然语言描述任务即可。例如：
              <span className="text-gray-300"> "研判这条告警是否真实攻击"</span>、
              <span className="text-gray-300">"对 1.2.3.4 做授权渗透，仅限 80 端口"</span>、
              <span className="text-gray-300">"帮我解这道 misc 题"</span>。
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

          {/* 绑定容器下拉框（从可用容器列表中选一个） */}
          <div className="p-3 rounded-xl border border-gray-800 bg-gray-900/60">
            <div className="flex items-center justify-between mb-2">
              <div>
                <div className="text-xs font-medium text-gray-300">绑定容器</div>
                <div className="text-[10px] text-gray-600 mt-0.5">
                  复杂任务（如渗透/CTF）建议选一个；简单任务可不选，本地运行
                </div>
              </div>
            </div>
            <select
              value={selectedContainer}
              onChange={(e) => setSelectedContainer(e.target.value)}
              disabled={availableContainers.length === 0 || creating}
              className={`w-full px-3 py-2 bg-gray-800/50 border border-gray-800 rounded-lg text-sm transition-colors focus:outline-none focus:border-emerald-600/50 ${
                availableContainers.length === 0
                  ? 'text-gray-600 cursor-not-allowed'
                  : 'text-gray-200 cursor-pointer'
              }`}
            >
              <option value="">不绑定（本地运行）</option>
              {availableContainers.map(c => {
                const label = c.display_name && c.display_name !== c.container_name
                  ? `${c.display_name}（${c.container_name}）`
                  : c.container_name;
                return (
                  <option key={c.container_name} value={c.container_name}>
                    {label} · {c.image || '默认镜像'}
                  </option>
                );
              })}
            </select>
            {availableContainers.length === 0 && (
              <div className="mt-2 text-[10px] text-amber-500/80">
                无可用容器 · 可前往「容器管理」页面创建独立容器
              </div>
            )}
          </div>

          <button
            onClick={handleCreate}
            disabled={creating}
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-xl text-sm font-semibold transition-all active:scale-[0.98] cursor-pointer disabled:cursor-not-allowed"
          >
            {creating ? '创建中...' : '新建任务'}
          </button>
        </div>
      </div>
    </div>
  );
}
