import { useEffect, useMemo, useState } from 'react';
import { useApp } from '../context/AppContext';
import {
  listExperiences,
  createExperience,
  updateExperience,
  deleteExperience,
  getEmbeddingConfig,
  saveEmbeddingConfig,
  reindexExperiences,
} from '../api/client';
import type { ExperienceItem, EmbeddingConfigData } from '../types';
import type { JSX } from 'react';

const SCOPE_FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'global', label: '全局经验' },
  { id: 'agent', label: '智能体专属' },
];

export function Experiences(): JSX.Element {
  const { addToast } = useApp();
  const [items, setItems] = useState<ExperienceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [scopeFilter, setScopeFilter] = useState('all');
  const [editing, setEditing] = useState<ExperienceItem | 'new' | null>(null);
  const [embedOpen, setEmbedOpen] = useState(false);
  const [embedCfg, setEmbedCfg] = useState<EmbeddingConfigData>({
    provider: 'none', base_url: '', api_key: '', model: '', dimensions: 0,
  });
  const [reindexing, setReindexing] = useState(false);

  useEffect(() => { load(); }, []);

  async function load() {
    setLoading(true);
    try {
      const r = await listExperiences({ include_disabled: true });
      setItems(r.experiences);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载失败', message: err.message });
    } finally {
      setLoading(false);
    }
  }

  async function openEmbedConfig() {
    try {
      const r = await getEmbeddingConfig();
      setEmbedCfg(r.config);
      setEmbedOpen(true);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载配置失败', message: err.message });
    }
  }

  async function saveEmbed() {
    try {
      await saveEmbeddingConfig(embedCfg);
      setEmbedOpen(false);
      addToast({ type: 'success', title: 'embedding 配置已保存' });
    } catch (err: any) {
      addToast({ type: 'error', title: '保存失败', message: err.message });
    }
  }

  async function handleReindex() {
    setReindexing(true);
    try {
      const r = await reindexExperiences();
      addToast({ type: 'success', title: '索引重建完成', message: `已处理 ${r.indexed} 条` });
    } catch (err: any) {
      addToast({ type: 'error', title: '重建失败', message: err.message });
    } finally {
      setReindexing(false);
    }
  }

  async function handleDelete(item: ExperienceItem) {
    if (!window.confirm(`确定删除经验「${item.title}」？此操作不可恢复。`)) return;
    try {
      await deleteExperience(item.id);
      setItems(items.filter(i => i.id !== item.id));
      addToast({ type: 'info', title: '经验已删除' });
    } catch (err: any) {
      addToast({ type: 'error', title: '删除失败', message: err.message });
    }
  }

  async function handleToggleEnabled(item: ExperienceItem) {
    try {
      const r = await updateExperience(item.id, { enabled: !item.enabled });
      setItems(items.map(i => i.id === item.id ? r.experience : i));
    } catch (err: any) {
      addToast({ type: 'error', title: '更新失败', message: err.message });
    }
  }

  const filtered = useMemo(() => {
    return items.filter(i => {
      if (scopeFilter === 'global' && i.scope !== 'global') return false;
      if (scopeFilter === 'agent' && i.scope === 'global') return false;
      if (!search) return true;
      const q = search.toLowerCase();
      return i.title.toLowerCase().includes(q) ||
        i.content.toLowerCase().includes(q) ||
        i.tags.some(t => t.toLowerCase().includes(q));
    });
  }, [items, scopeFilter, search]);

  function scopeLabel(scope: string): string {
    return scope === 'global' ? '全局' : scope.replace(/^agent:/, '智能体: ');
  }

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">经验库</h1>
          <p className="text-sm text-gray-500 mt-1">共 {items.length} 条经验 · 自动沉淀渗透测试复盘，跨会话复用</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={openEmbedConfig}
            className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl transition-colors"
          >
            embedding 配置
          </button>
          <button
            onClick={() => setEditing('new')}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-xl transition-colors"
          >
            + 新建经验
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-6">
        <div className="flex bg-gray-900 border border-gray-800 rounded-xl p-1">
          {SCOPE_FILTERS.map(f => (
            <button
              key={f.id}
              onClick={() => setScopeFilter(f.id)}
              className={`px-4 py-1.5 text-sm rounded-lg transition-colors ${
                scopeFilter === f.id ? 'bg-blue-600/20 text-blue-400' : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="搜索标题、内容或标签..."
          className="flex-1 max-w-md px-4 py-2 bg-gray-900 border border-gray-800 rounded-xl text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none"
        />
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20 text-gray-600">
          {search || scopeFilter !== 'all' ? '未找到匹配的经验' :
            '暂无经验。完成一次渗透测试复盘后，经验会自动沉淀到这里。'}
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {filtered.map(item => (
            <div key={item.id} className={`rounded-2xl border p-5 transition-colors ${
              item.enabled ? 'border-gray-800 bg-gray-900/40 hover:border-gray-700' : 'border-gray-800/60 bg-gray-900/20 opacity-60'
            }`}>
              <div className="flex items-start justify-between gap-3 mb-2">
                <h3 className="font-semibold text-gray-200 leading-snug">{item.title}</h3>
                <span className={`flex-shrink-0 px-2 py-0.5 text-[10px] rounded-full border ${
                  item.scope === 'global'
                    ? 'bg-blue-600/10 text-blue-400 border-blue-600/20'
                    : 'bg-violet-600/10 text-violet-400 border-violet-600/20'
                }`}>{scopeLabel(item.scope)}</span>
              </div>
              <p className="text-sm text-gray-400 leading-relaxed whitespace-pre-line line-clamp-4">{item.content}</p>
              {item.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-3">
                  {item.tags.map(t => (
                    <span key={t} className="px-2 py-0.5 text-[11px] bg-gray-800 text-gray-400 rounded-full">{t}</span>
                  ))}
                </div>
              )}
              <div className="flex items-center justify-between mt-4 pt-3 border-t border-gray-800/60">
                <div className="flex items-center gap-3 text-[11px] text-gray-600">
                  <span>命中 {item.hit_count} 次</span>
                  {item.source_agent && <span>来源: {item.source_agent}</span>}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleToggleEnabled(item)}
                    className={`px-2.5 py-1 text-[11px] rounded-lg border transition-colors ${
                      item.enabled
                        ? 'bg-emerald-600/10 text-emerald-400 border-emerald-600/20 hover:bg-emerald-600/20'
                        : 'bg-gray-800 text-gray-500 border-gray-700 hover:bg-gray-700'
                    }`}
                    title={item.enabled ? '点击停用' : '点击启用'}
                  >
                    {item.enabled ? '已启用' : '已停用'}
                  </button>
                  <button
                    onClick={() => setEditing(item)}
                    className="px-2.5 py-1 text-[11px] bg-gray-800 hover:bg-gray-700 text-gray-400 rounded-lg border border-gray-700 transition-colors"
                  >
                    编辑
                  </button>
                  <button
                    onClick={() => handleDelete(item)}
                    className="px-2.5 py-1 text-[11px] bg-red-600/10 hover:bg-red-600/20 text-red-400 rounded-lg border border-red-600/20 transition-colors"
                  >
                    删除
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Editor Modal */}
      {editing !== null && (
        <ExperienceEditor
          item={editing === 'new' ? null : editing}
          agents={Array.from(new Set(items.map(i => i.source_agent).filter(Boolean)))}
          onClose={() => setEditing(null)}
          onSaved={(item) => {
            setItems(prev => {
              const idx = prev.findIndex(i => i.id === item.id);
              if (idx === -1) return [item, ...prev];
              const next = [...prev];
              next[idx] = item;
              return next;
            });
            setEditing(null);
          }}
        />
      )}

      {/* Embedding Config Modal */}
      {embedOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-lg bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl p-6">
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-lg font-semibold">embedding 向量配置</h2>
              <button onClick={() => setEmbedOpen(false)} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Provider</label>
                <div className="grid grid-cols-3 gap-2">
                  {([
                    { id: 'none', label: '关键词模式', desc: '零依赖，默认' },
                    { id: 'openai', label: '云端向量', desc: 'OpenAI 兼容 API' },
                    { id: 'local', label: '本地模型', desc: 'fastembed ONNX' },
                  ] as const).map(opt => (
                    <button
                      key={opt.id}
                      onClick={() => setEmbedCfg({ ...embedCfg, provider: opt.id })}
                      className={`rounded-xl border p-3 text-left transition-colors ${
                        embedCfg.provider === opt.id
                          ? 'border-blue-500 bg-blue-600/10'
                          : 'border-gray-800 hover:border-gray-700'
                      }`}
                    >
                      <div className="text-sm font-medium">{opt.label}</div>
                      <div className="text-[11px] text-gray-500 mt-0.5">{opt.desc}</div>
                    </button>
                  ))}
                </div>
              </div>

              {embedCfg.provider === 'openai' && (
                <div className="space-y-3 animate-fade-in">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1.5">Base URL（OpenAI / 硅基流动 / 智谱等兼容端点）</label>
                    <input
                      type="text"
                      value={embedCfg.base_url}
                      onChange={e => setEmbedCfg({ ...embedCfg, base_url: e.target.value })}
                      placeholder="https://api.siliconflow.cn/v1"
                      className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1.5">API Key</label>
                    <input
                      type="password"
                      value={embedCfg.api_key}
                      onChange={e => setEmbedCfg({ ...embedCfg, api_key: e.target.value })}
                      placeholder="sk-..."
                      className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1.5">模型（如 text-embedding-3-small / BAAI/bge-m3）</label>
                    <input
                      type="text"
                      value={embedCfg.model}
                      onChange={e => setEmbedCfg({ ...embedCfg, model: e.target.value })}
                      placeholder="text-embedding-3-small"
                      className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500"
                    />
                  </div>
                  <p className="text-[11px] text-gray-600">留空 base_url 时将自动复用模型配置中的 LLM 端点（若该端点支持 embedding）。</p>
                </div>
              )}

              {embedCfg.provider === 'local' && (
                <div className="space-y-3 animate-fade-in">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1.5">本地模型（默认 BAAI/bge-small-zh-v1.5）</label>
                    <input
                      type="text"
                      value={embedCfg.model}
                      onChange={e => setEmbedCfg({ ...embedCfg, model: e.target.value })}
                      placeholder="BAAI/bge-small-zh-v1.5"
                      className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500"
                    />
                  </div>
                  <p className="text-[11px] text-gray-600">需安装依赖：<code className="text-gray-400">pip install fastembed</code>。首次使用会自动下载模型（约 100MB）。</p>
                </div>
              )}

              <button
                onClick={handleReindex}
                disabled={reindexing}
                className="w-full px-4 py-2.5 text-sm bg-gray-800 hover:bg-gray-700 disabled:opacity-50 border border-gray-700 rounded-xl transition-colors"
              >
                {reindexing ? '重建中...' : '为现有经验重建向量索引'}
              </button>
            </div>

            <div className="flex justify-end gap-2 mt-6">
              <button
                onClick={() => setEmbedOpen(false)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 rounded-lg"
              >
                取消
              </button>
              <button
                onClick={saveEmbed}
                className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-xl transition-colors"
              >
                保存配置
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ExperienceEditor({ item, agents, onClose, onSaved }: {
  item: ExperienceItem | null;
  agents: string[];
  onClose: () => void;
  onSaved: (item: ExperienceItem) => void;
}): JSX.Element {
  const { addToast } = useApp();
  const [title, setTitle] = useState(item?.title ?? '');
  const [content, setContent] = useState(item?.content ?? '');
  const [tagsText, setTagsText] = useState(item?.tags.join(', ') ?? '');
  const [scope, setScope] = useState(item?.scope ?? 'global');
  const [importance, setImportance] = useState(item?.importance ?? 0);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!title.trim()) { addToast({ type: 'warning', title: '请填写标题' }); return; }
    if (!content.trim()) { addToast({ type: 'warning', title: '请填写内容' }); return; }
    setSaving(true);
    try {
      const tags = tagsText.split(/[,，]/).map(t => t.trim()).filter(Boolean);
      const payload = {
        title: title.trim(),
        content: content.trim(),
        scope,
        tags,
        importance,
      };
      const r = item
        ? await updateExperience(item.id, payload)
        : await createExperience({ ...payload, source_agent: scope.startsWith('agent:') ? scope.slice(6) : '' });
      addToast({ type: 'success', title: item ? '经验已更新' : '经验已创建' });
      onSaved(r.experience);
    } catch (err: any) {
      addToast({ type: 'error', title: '保存失败', message: err.message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-2xl bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl p-6 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold">{item ? '编辑经验' : '新建经验'}</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">标题</label>
            <input
              type="text"
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="如：WordPress 打点套路"
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">复盘内容（教训 + 可复用步骤 + 适用条件）</label>
            <textarea
              value={content}
              onChange={e => setContent(e.target.value)}
              rows={6}
              placeholder="总结踩过的坑、有效的攻击路径、适用指纹/版本条件..."
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500 resize-y"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">标签（逗号分隔）</label>
            <input
              type="text"
              value={tagsText}
              onChange={e => setTagsText(e.target.value)}
              placeholder="wordpress, wpscan, 指纹识别"
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-gray-500 mb-1.5">作用域</label>
              <select
                value={scope}
                onChange={e => setScope(e.target.value)}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500"
              >
                <option value="global">全局经验（所有智能体可用）</option>
                {agents.map(a => (
                  <option key={a} value={`agent:${a}`}>智能体专属: {a}</option>
                ))}
                {!agents.includes(scope.replace(/^agent:/, '')) && scope.startsWith('agent:') && (
                  <option value={scope}>智能体专属: {scope.slice(6)}</option>
                )}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1.5">重要程度</label>
              <div className="flex gap-1 pt-1">
                {[0, 1, 2, 3, 4, 5].map(n => (
                  <button
                    key={n}
                    onClick={() => setImportance(n)}
                    className={`w-8 h-8 rounded-lg text-sm transition-colors ${
                      n <= importance ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-500 hover:bg-gray-700'
                    }`}
                    title={`重要度 ${n}`}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 rounded-lg">
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-xl transition-colors disabled:opacity-50"
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}
