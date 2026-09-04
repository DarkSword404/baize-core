import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useApp } from '../context/AppContext';
import { MemoryGraphView } from '../components/MemoryGraphView';
import {
  getMemoryStats,
  listMemoryExperiences,
  getMemoryExperience,
  createMemoryExperience,
  updateMemoryExperience,
  setMemoryExperienceStatus,
  feedbackMemoryExperience,
  invalidateMemoryExperience,
  consolidateMemoryExperiences,
  listMemoryEpisodes,
  getMemoryEpisode,
  searchMemory,
  getMemoryGraph,
} from '../api/client';
import type {
  MemoryExperience,
  MemoryExperienceStatus,
  MemoryExperienceKind,
  MemoryEpisodeBrief,
  MemoryEpisodeDetail,
  MemoryEpisodeStep,
  MemoryGraphSnapshot,
  MemorySearchResult,
  MemoryStats,
} from '../types';
import type { JSX } from 'react';

const TABS: Array<{ id: 'exp' | 'ep' | 'search' | 'graph'; label: string }> = [
  { id: 'exp', label: '经验' },
  { id: 'ep', label: '任务轨迹' },
  { id: 'search', label: '混合检索' },
  { id: 'graph', label: '知识图谱' },
];

const STATUS_META: Record<string, { label: string; cls: string }> = {
  active: { label: '生效', cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' },
  draft: { label: '草稿', cls: 'bg-gray-500/15 text-gray-400 border-gray-500/30' },
  superseded: { label: '已被取代', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30' },
  invalidated: { label: '已作废', cls: 'bg-red-500/15 text-red-400 border-red-500/30' },
};

const KIND_META: Record<string, { label: string; cls: string }> = {
  method: { label: '方法', cls: 'bg-blue-500/15 text-blue-300 border-blue-500/30' },
  lesson: { label: '教训', cls: 'bg-orange-500/15 text-orange-300 border-orange-500/30' },
  intel: { label: '情报', cls: 'bg-purple-500/15 text-purple-300 border-purple-500/30' },
  generalization: { label: '泛化', cls: 'bg-teal-500/15 text-teal-300 border-teal-500/30' },
};

const EP_STATUS_META: Record<string, { label: string; cls: string }> = {
  success: { label: '成功', cls: 'text-emerald-400' },
  failed: { label: '失败', cls: 'text-red-400' },
  in_progress: { label: '进行中', cls: 'text-amber-400' },
};

function fmt(iso?: string): string {
  if (!iso) return '';
  try {
    const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
    if (Number.isNaN(d.getTime())) return iso.slice(0, 19);
    const p = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  } catch {
    return iso.slice(0, 19);
  }
}

function pct(v?: number): string {
  return `${Math.round((v || 0) * 100)}%`;
}

function badge(cls: string, text: string): JSX.Element {
  return <span className={`px-1.5 py-0.5 rounded border text-[10px] ${cls}`}>{text}</span>;
}

function Panel({ title, extra, children }: { title: string; extra?: ReactNode; children: ReactNode }): JSX.Element {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-gray-300">{title}</h3>
        {extra}
      </div>
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }): JSX.Element {
  return <p className="text-xs text-gray-600 py-2">{text}</p>;
}

export function Experiences(): JSX.Element {
  const { addToast } = useApp();
  const [tab, setTab] = useState<'exp' | 'ep' | 'search' | 'graph'>('exp');

  // ---- 统计 ----
  const [stats, setStats] = useState<MemoryStats | null>(null);

  // ---- 经验 ----
  const [exps, setExps] = useState<MemoryExperience[]>([]);
  const [expLoading, setExpLoading] = useState(false);
  const [expSearch, setExpSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | MemoryExperienceStatus>('all');
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<{ mode: 'new' } | { mode: 'edit'; exp: MemoryExperience } | null>(null);
  const [detail, setDetail] = useState<MemoryExperience | null>(null);
  const [merging, setMerging] = useState(false);

  // ---- 任务轨迹 ----
  const [eps, setEps] = useState<MemoryEpisodeBrief[]>([]);
  const [epLoading, setEpLoading] = useState(false);
  const [epSearch, setEpSearch] = useState('');
  const [epSession, setEpSession] = useState('');
  const [epDetail, setEpDetail] = useState<MemoryEpisodeDetail | null>(null);
  const [epDetailLoading, setEpDetailLoading] = useState(false);

  // ---- 检索 ----
  const [q, setQ] = useState('');
  const [includeEp, setIncludeEp] = useState(false);
  const [result, setResult] = useState<MemorySearchResult | null>(null);
  const [searching, setSearching] = useState(false);

  // ---- 图谱 ----
  const [graph, setGraph] = useState<MemoryGraphSnapshot | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphLoaded, setGraphLoaded] = useState(false);

  async function loadStats() {
    try {
      setStats(await getMemoryStats());
    } catch (err: any) {
      addToast({ type: 'error', title: '统计加载失败', message: err.message });
    }
  }

  async function loadExps() {
    setExpLoading(true);
    try {
      const r = await listMemoryExperiences({ include_superseded: true });
      setExps(r.experiences);
      setSelected([]);
    } catch (err: any) {
      addToast({ type: 'error', title: '经验加载失败', message: err.message });
    } finally {
      setExpLoading(false);
    }
  }

  async function loadEps(sessionId?: string) {
    setEpLoading(true);
    try {
      const sid = ((sessionId ?? epSession) || '').trim();
      const r = await listMemoryEpisodes({ limit: 200, session_id: sid || undefined });
      setEps(r.episodes);
    } catch (err: any) {
      addToast({ type: 'error', title: '轨迹加载失败', message: err.message });
    } finally {
      setEpLoading(false);
    }
  }

  async function loadGraph() {
    setGraphLoading(true);
    try {
      setGraph(await getMemoryGraph());
      setGraphLoaded(true);
    } catch (err: any) {
      addToast({ type: 'error', title: '图谱加载失败', message: err.message });
    } finally {
      setGraphLoading(false);
    }
  }

  useEffect(() => {
    loadStats();
    loadExps();
  }, []);

  useEffect(() => {
    if (tab === 'ep' && eps.length === 0) loadEps();
    if (tab === 'graph' && !graphLoaded) loadGraph();
  }, [tab]);

  const filtered = useMemo(() => {
    const kw = expSearch.trim().toLowerCase();
    return exps.filter(e => {
      if (statusFilter !== 'all' && e.status !== statusFilter) return false;
      if (!kw) return true;
      return (
        e.title.toLowerCase().includes(kw) ||
        e.content.toLowerCase().includes(kw) ||
        (e.tags || []).join(' ').toLowerCase().includes(kw)
      );
    });
  }, [exps, expSearch, statusFilter]);

  const filteredEps = useMemo(() => {
    const kw = epSearch.trim().toLowerCase();
    if (!kw) return eps;
    return eps.filter(e =>
      (e.task || '').toLowerCase().includes(kw) ||
      (e.target || '').toLowerCase().includes(kw) ||
      (e.summary || '').toLowerCase().includes(kw) ||
      (e.result_text || '').toLowerCase().includes(kw));
  }, [eps, epSearch]);

  function errToast(title: string, err: any) {
    addToast({ type: 'error', title, message: err?.message || String(err) });
  }

  async function openDetail(exp: MemoryExperience) {
    try {
      const r = await getMemoryExperience(exp.id);
      setDetail(r.experience);
    } catch {
      setDetail(exp);
    }
  }

  async function openEpisode(id: string) {
    if (!id) return;
    setEpDetailLoading(true);
    try {
      const r = await getMemoryEpisode(id);
      setEpDetail(r.episode);
    } catch (err: any) {
      errToast('轨迹详情加载失败', err);
    } finally {
      setEpDetailLoading(false);
    }
  }

  async function changeStatus(exp: MemoryExperience, status: MemoryExperienceStatus, label: string) {
    if (!window.confirm(`确定将经验「${exp.title}」${label}？`)) return;
    try {
      await setMemoryExperienceStatus(exp.id, { status, note: `人工操作：${label}` });
      addToast({ type: 'success', title: `已${label}` });
      await Promise.all([loadExps(), loadStats()]);
    } catch (err: any) {
      errToast('状态更新失败', err);
    }
  }

  async function doInvalidate(exp: MemoryExperience) {
    if (!window.confirm(`作废经验「${exp.title}」？（记忆不做物理删除，保留谱系与审计）`)) return;
    try {
      await invalidateMemoryExperience(exp.id);
      addToast({ type: 'info', title: '经验已作废' });
      await Promise.all([loadExps(), loadStats()]);
    } catch (err: any) {
      errToast('作废失败', err);
    }
  }

  async function doFeedback(exp: MemoryExperience, useful: boolean) {
    try {
      await feedbackMemoryExperience(exp.id, {
        useful,
        note: useful ? '人工标记有用' : '人工标记无用',
      });
      addToast({ type: 'success', title: useful ? '已标记为有用' : '已标记为无用' });
      await loadExps();
    } catch (err: any) {
      errToast('评分失败', err);
    }
  }

  async function doConsolidate(autoCommit: boolean) {
    if (selected.length < 2) return;
    const tip = autoCommit
      ? `将合并选中的 ${selected.length} 条经验为一条泛化经验，并立即把它们标记为「已被取代」。确定继续？`
      : `将合并选中的 ${selected.length} 条经验为一条泛化经验草稿（旧条目暫不取代，可先复核）。确定继续？`;
    if (!window.confirm(tip)) return;
    setMerging(true);
    try {
      const r = await consolidateMemoryExperiences(selected, autoCommit);
      addToast({
        type: 'success',
        title: autoCommit ? '合并完成并已取代旧条目' : '已生成泛化草稿',
        message: `新经验：${String((r.result || {}).title || '')}`.trim(),
      });
      await Promise.all([loadExps(), loadStats()]);
    } catch (err: any) {
      errToast('合并失败', err);
    } finally {
      setMerging(false);
    }
  }

  async function doSearch() {
    const kw = q.trim();
    if (!kw) return;
    setSearching(true);
    try {
      setResult(await searchMemory(kw, includeEp));
    } catch (err: any) {
      errToast('检索失败', err);
    } finally {
      setSearching(false);
    }
  }

  function refresh() {
    loadStats();
    if (tab === 'exp') loadExps();
    if (tab === 'ep') loadEps();
    if (tab === 'graph') loadGraph();
    if (tab === 'search' && q.trim()) doSearch();
  }

  function toggleSelect(id: string) {
    setSelected(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]));
  }

  const statCards: Array<{ label: string; value: number }> = stats
    ? [
        { label: '经验', value: stats.experiences },
        { label: '生效', value: stats.experiences_active },
        { label: '草稿', value: stats.experiences_draft },
        { label: '任务轨迹', value: stats.episodes },
        { label: '语义事实', value: stats.facts },
        { label: '实体', value: stats.entities },
        { label: '关系', value: stats.relations },
      ]
    : [];

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8">
      <div className="flex items-start justify-between mb-6 gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">记忆库</h1>
          <p className="text-sm text-gray-500 mt-1">
            Agent 长期记忆：自然语言经验 + 任务轨迹（Episode）+ 语义事实 + 时间知识图谱，全部可证据回溯与人工演进
          </p>
        </div>
        <button
          onClick={refresh}
          className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl transition-colors shrink-0"
        >
          刷新
        </button>
      </div>

      {/* 统计条 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 mb-6">
        {statCards.map(c => (
          <div key={c.label} className="rounded-xl border border-gray-800 bg-gray-900/40 px-3 py-2.5">
            <p className="text-[11px] text-gray-500">{c.label}</p>
            <p className="text-lg font-semibold text-gray-200 tabular-nums">{c.value}</p>
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-gray-800 mb-5">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-sm border-b-2 transition-colors ${
              tab === t.id
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ============ 经验 ============ */}
      {tab === 'exp' && (
        <div>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <input
              value={expSearch}
              onChange={e => setExpSearch(e.target.value)}
              placeholder="搜索标题 / 正文 / 标签…"
              className="w-64 px-3 py-2 bg-gray-900 border border-gray-800 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500"
            />
            <div className="flex items-center gap-1">
              {(['all', 'active', 'draft', 'superseded', 'invalidated'] as const).map(s => (
                <button
                  key={s}
                  onClick={() => setStatusFilter(s)}
                  className={`px-2.5 py-1.5 text-xs rounded-lg border transition-colors ${
                    statusFilter === s
                      ? 'border-blue-500/40 bg-blue-500/10 text-blue-300'
                      : 'border-gray-800 text-gray-500 hover:text-gray-300'
                  }`}
                >
                  {s === 'all' ? '全部' : STATUS_META[s]?.label || s}
                </button>
              ))}
            </div>
            <div className="flex-1" />
            <button
              onClick={() => setEditing({ mode: 'new' })}
              className="px-3 py-2 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
            >
              + 新增经验
            </button>
          </div>

          {selected.length >= 2 && (
            <div className="mb-3 flex items-center gap-2 px-3 py-2 rounded-lg border border-blue-600/30 bg-blue-600/5 text-xs">
              <span className="text-blue-300">已选 {selected.length} 条</span>
              <button
                onClick={() => doConsolidate(false)}
                disabled={merging}
                className="px-2.5 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
              >
                {merging ? '合并中…' : '合并为泛化草稿'}
              </button>
              <button
                onClick={() => doConsolidate(true)}
                disabled={merging}
                className="px-2.5 py-1 rounded border border-amber-500/40 text-amber-300 hover:bg-amber-500/10 disabled:opacity-50"
              >
                合并并取代旧条目
              </button>
              <button onClick={() => setSelected([])} className="text-gray-500 hover:text-gray-300">
                取消选择
              </button>
            </div>
          )}

          {expLoading && <p className="text-sm text-gray-500 py-6">加载中…</p>}
          {!expLoading && filtered.length === 0 && (
            <div className="rounded-xl border border-dashed border-gray-800 p-8 text-center text-sm text-gray-500">
              暂无经验记录。Agent 每回合结束会自动沉淀，也可点击「新增经验」手工补充。
            </div>
          )}

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {filtered.map(e => (
              <div key={e.id} className="rounded-xl border border-gray-800 bg-gray-900/40 p-4 hover:border-gray-700 transition-colors">
                <div className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    checked={selected.includes(e.id)}
                    onChange={() => toggleSelect(e.id)}
                    className="mt-1 accent-blue-500"
                    title="选择以合并"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="text-sm font-medium text-gray-200 truncate">{e.title}</h3>
                      {badge(STATUS_META[e.status]?.cls || 'bg-gray-500/15 text-gray-400 border-gray-500/30', STATUS_META[e.status]?.label || e.status)}
                      {badge(KIND_META[e.kind]?.cls || 'bg-gray-500/15 text-gray-400 border-gray-500/30', KIND_META[e.kind]?.label || e.kind)}
                    </div>
                    <p className="mt-1.5 text-xs text-gray-400 line-clamp-3 whitespace-pre-wrap">{e.content}</p>
                    {!!(e.tags || []).length && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {e.tags.slice(0, 6).map((t, i) => (
                          <span key={i} className="px-1.5 py-0.5 rounded bg-gray-800 text-[10px] text-gray-400">#{t}</span>
                        ))}
                      </div>
                    )}
                    <div className="mt-2 flex items-center gap-3 text-[11px] text-gray-600 flex-wrap">
                      <span>置信度 {pct(e.confidence)}</span>
                      <span>重要度 {e.importance}</span>
                      <span>{e.scope === 'global' ? '全局' : e.scope}</span>
                      {e.agent_key && <span>来源 {e.agent_key}</span>}
                      <span>证据 {e.evidence?.length || 0}</span>
                      <span>命中 {e.hit_count || 0}</span>
                      <span>有用 {e.useful_count || 0}</span>
                      {!!e.noise_count && <span className="text-red-400/80">无用 {e.noise_count}</span>}
                      <span>{fmt(e.updated_at || e.created_at)}</span>
                      {e.replaced_by && <span className="text-amber-500">被 {e.replaced_by} 取代</span>}
                    </div>
                    <div className="mt-3 flex items-center gap-2 flex-wrap text-xs">
                      <button onClick={() => openDetail(e)} className="px-2 py-1 rounded border border-gray-700 text-gray-400 hover:text-gray-200">
                        详情
                      </button>
                      <button onClick={() => setEditing({ mode: 'edit', exp: e })} className="px-2 py-1 rounded border border-gray-700 text-gray-400 hover:text-gray-200">
                        编辑
                      </button>
                      <button
                        onClick={() => doFeedback(e, true)}
                        title="标记有用（提升后续检索权重）"
                        className="px-2 py-1 rounded border border-emerald-600/30 text-emerald-400/90 hover:bg-emerald-600/10"
                      >
                        👍
                      </button>
                      <button
                        onClick={() => doFeedback(e, false)}
                        title="标记无用（连续无用且从未有用会自动降级为草稿）"
                        className="px-2 py-1 rounded border border-red-600/30 text-red-400/90 hover:bg-red-600/10"
                      >
                        👎
                      </button>
                      {e.status === 'draft' && (
                        <button onClick={() => changeStatus(e, 'active', '启用')} className="px-2 py-1 rounded border border-emerald-600/40 text-emerald-400 hover:bg-emerald-600/10">
                          启用
                        </button>
                      )}
                      {e.status === 'active' && (
                        <button onClick={() => changeStatus(e, 'draft', '转为草稿')} className="px-2 py-1 rounded border border-gray-700 text-gray-400 hover:text-gray-200">
                          转草稿
                        </button>
                      )}
                      {(e.status === 'superseded' || e.status === 'invalidated') && (
                        <button onClick={() => changeStatus(e, 'draft', '恢复为草稿')} className="px-2 py-1 rounded border border-gray-700 text-gray-400 hover:text-gray-200">
                          恢复
                        </button>
                      )}
                      {e.status !== 'invalidated' && (
                        <button onClick={() => doInvalidate(e)} className="px-2 py-1 rounded border border-red-600/40 text-red-400 hover:bg-red-600/10">
                          作废
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ============ 任务轨迹 ============ */}
      {tab === 'ep' && (
        <div>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <input
              value={epSearch}
              onChange={e => setEpSearch(e.target.value)}
              placeholder="搜索任务 / 目标 / 结论…"
              className="w-72 px-3 py-2 bg-gray-900 border border-gray-800 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500"
            />
            <input
              value={epSession}
              onChange={e => setEpSession(e.target.value)}
              placeholder="按会话 ID 过滤（可留空）"
              className="w-64 px-3 py-2 bg-gray-900 border border-gray-800 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500"
            />
            <button
              onClick={() => loadEps(epSession)}
              className="px-3 py-2 text-xs rounded-lg border border-gray-700 text-gray-300 hover:bg-gray-800 transition-colors"
            >
              过滤
            </button>
            {epSession && (
              <button
                onClick={() => { setEpSession(''); loadEps(''); }}
                className="text-xs text-gray-500 hover:text-gray-300"
              >
                清除
              </button>
            )}
            <span className="text-xs text-gray-600">点击行查看完整执行轨迹（Agent 动作 / 工具调用 / 参数 / 输出 / 结论）</span>
          </div>
          {epLoading && <p className="text-sm text-gray-500 py-6">加载中…</p>}
          {!epLoading && filteredEps.length === 0 && (
            <div className="rounded-xl border border-dashed border-gray-800 p-8 text-center text-sm text-gray-500">
              暂无任务轨迹。每一次 Agent 任务都会固化为一个 Episode。
            </div>
          )}
          <div className="space-y-2">
            {filteredEps.map(ep => (
              <button
                key={ep.id}
                onClick={() => openEpisode(ep.id)}
                className="w-full text-left rounded-xl border border-gray-800 bg-gray-900/40 px-4 py-3 hover:border-blue-600/40 transition-colors"
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`text-xs ${EP_STATUS_META[ep.status]?.cls || 'text-gray-400'}`}>
                    ● {EP_STATUS_META[ep.status]?.label || ep.status}
                  </span>
                  <span className="text-sm text-gray-200 truncate">{ep.task || '(无任务描述)'}</span>
                  {ep.target && <span className="text-[11px] text-gray-500">目标 {ep.target}</span>}
                </div>
                <div className="mt-1 flex items-center gap-3 text-[11px] text-gray-600 flex-wrap">
                  <span>{fmt(ep.start_at || ep.created_at)}</span>
                  {ep.agent_key && <span>{ep.agent_key}</span>}
                  <span>会话 {ep.session_id ? `${ep.session_id.slice(0, 10)}…` : '—'}</span>
                  <span>步骤 {ep.steps}</span>
                  <span>消息 {ep.messages_count}</span>
                  {!!(ep.entity_keys || []).length && <span>实体 {ep.entity_keys.length}</span>}
                </div>
                {(ep.summary || ep.result_text || ep.error) && (
                  <p className="mt-1 text-xs text-gray-500 line-clamp-2">{ep.error || ep.summary || ep.result_text}</p>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ============ 混合检索 ============ */}
      {tab === 'search' && (
        <div>
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <input
              value={q}
              onChange={e => setQ(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && doSearch()}
              placeholder="输入检索内容，例如：redis 未授权 提权"
              className="flex-1 min-w-[240px] px-3 py-2 bg-gray-900 border border-gray-800 rounded-lg text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500"
            />
            <label className="flex items-center gap-1.5 text-xs text-gray-500">
              <input type="checkbox" checked={includeEp} onChange={e => setIncludeEp(e.target.checked)} className="accent-blue-500" />
              包含任务轨迹
            </label>
            <button
              onClick={doSearch}
              disabled={searching || !q.trim()}
              className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg transition-colors"
            >
              {searching ? '检索中…' : '检索'}
            </button>
          </div>

          {!result && <div className="rounded-xl border border-dashed border-gray-800 p-8 text-center text-sm text-gray-500">输入关键词开始检索（本地混合召回，不调用模型）</div>}

          {result && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <Panel title={`相关经验（${result.experiences.length}）`}>
                {result.experiences.length === 0 ? <Empty text="无匹配经验" /> : (
                  <div className="space-y-2">
                    {result.experiences.map(e => (
                      <div key={e.id} className="rounded-lg border border-gray-800 p-3">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm text-gray-200">{e.title}</span>
                          {badge(STATUS_META[e.status]?.cls || '', STATUS_META[e.status]?.label || e.status)}
                          {typeof e.score === 'number' && <span className="text-[10px] text-gray-500">score {e.score.toFixed(2)}</span>}
                        </div>
                        <p className="mt-1 text-xs text-gray-400 whitespace-pre-wrap">{e.content}</p>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>

              <Panel title={`语义事实（${result.facts.length}）`}>
                {result.facts.length === 0 ? <Empty text="无匹配事实" /> : (
                  <ul className="space-y-1.5">
                    {result.facts.map(f => (
                      <li key={f.id} className="text-xs text-gray-300">
                        <span className="text-gray-500">[{f.rel_type}]</span> {f.statement}
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>

              <Panel title={`命中实体（${result.entities.length}）`}>
                {result.entities.length === 0 ? <Empty text="无命中实体" /> : (
                  <div className="flex flex-wrap gap-1.5">
                    {result.entities.map(en => (
                      <span key={en.key} className="px-2 py-0.5 rounded bg-gray-800 text-[11px] text-gray-300">
                        {en.label} <span className="text-gray-600">· {en.kind}</span>
                      </span>
                    ))}
                  </div>
                )}
              </Panel>

              <Panel title={`图邻域（1 跳，${result.neighbors?.relations?.length || 0} 条关系）`}>
                {!result.neighbors || result.neighbors.relations.length === 0 ? <Empty text="无邻域关系" /> : (
                  <ul className="space-y-1">
                    {result.neighbors.relations.slice(0, 30).map(r => (
                      <li key={r.id} className="text-[11px] text-gray-400 font-mono">
                        {r.source} <span className="text-violet-400">—{r.type}→</span> {r.target}
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>

              {includeEp && (
                <Panel title={`相关任务轨迹（${result.episodes.length}）`}>
                  {result.episodes.length === 0 ? <Empty text="无匹配轨迹" /> : (
                    <div className="space-y-2">
                      {result.episodes.map(ep => (
                        <button key={ep.id} onClick={() => openEpisode(ep.id)} className="w-full text-left rounded-lg border border-gray-800 p-2.5 hover:border-blue-600/40">
                          <span className="text-xs text-gray-300">{ep.task}</span>
                          <span className="ml-2 text-[10px] text-gray-600">{fmt(ep.start_at || ep.created_at)}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </Panel>
              )}
            </div>
          )}
        </div>
      )}

      {/* ============ 知识图谱 ============ */}
      {tab === 'graph' && (
        <div>
          {graphLoading && <p className="text-sm text-gray-500 py-6">加载中…</p>}
          {!graphLoading && graph && (
            <>
              <MemoryGraphView snapshot={graph} />
              <div className="mt-3 flex items-center gap-3 text-[11px] text-gray-600 flex-wrap">
                <span>节点 {graph.nodes.length}</span>
                <span>边 {graph.edges.length}</span>
                <span>
                  经验节点 {graph.nodes.filter(n => n.kind === 'experience').length}
                </span>
                <span>实体节点 {graph.nodes.filter(n => n.kind === 'entity').length}</span>
              </div>
            </>
          )}
          {!graphLoading && !graph && (
            <div className="rounded-xl border border-dashed border-gray-800 p-8 text-center text-sm text-gray-500">暂无图谱数据</div>
          )}
        </div>
      )}

      {/* 经验详情 */}
      {detail && (
        <ExperienceDetailModal
          exp={detail}
          exps={exps}
          onClose={() => setDetail(null)}
          onOpenEpisode={openEpisode}
        />
      )}

      {/* 轨迹详情 */}
      {(epDetail || epDetailLoading) && (
        <Modal title="任务轨迹详情（Episode）" onClose={() => setEpDetail(null)} wide>
          {epDetailLoading && !epDetail && <p className="text-sm text-gray-500 py-6">加载中…</p>}
          {epDetail && <EpisodeDetailView ep={epDetail} />}
        </Modal>
      )}

      {/* 新增/编辑经验 */}
      {editing && (
        <ExperienceEditor
          initial={editing.mode === 'edit' ? editing.exp : null}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await Promise.all([loadExps(), loadStats()]);
          }}
        />
      )}
    </div>
  );
}

/* ============================ 子组件 ============================ */

function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }): JSX.Element {
  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div
        className={`bg-gray-900 border border-gray-800 rounded-2xl w-full ${wide ? 'max-w-4xl' : 'max-w-2xl'} max-h-[85vh] flex flex-col shadow-2xl`}
        onClick={e => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-200">{title}</h3>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400">✕</button>
        </div>
        <div className="px-6 py-4 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}

function ExperienceDetailModal({
  exp,
  exps,
  onClose,
  onOpenEpisode,
}: {
  exp: MemoryExperience;
  exps: MemoryExperience[];
  onClose: () => void;
  onOpenEpisode: (id: string) => void;
}): JSX.Element {
  const titleOf = (id: string) => exps.find(x => x.id === id)?.title || id;
  return (
    <Modal title="经验详情" onClose={onClose} wide>
      <div className="space-y-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="text-base font-medium text-gray-100">{exp.title}</h4>
            {badge(STATUS_META[exp.status]?.cls || '', STATUS_META[exp.status]?.label || exp.status)}
            {badge(KIND_META[exp.kind]?.cls || '', KIND_META[exp.kind]?.label || exp.kind)}
          </div>
          <div className="mt-2 flex items-center gap-3 text-[11px] text-gray-500 flex-wrap">
            <span>置信度 {pct(exp.confidence)}</span>
            <span>重要度 {exp.importance}</span>
            <span>作用域 {exp.scope}</span>
            <span>来源 {exp.source}</span>
            <span>创建 {fmt(exp.created_at)}</span>
            <span>更新 {fmt(exp.updated_at)}</span>
          </div>
        </div>

        <div>
          <p className="text-xs text-gray-500 mb-1">经验正文</p>
          <p className="text-sm text-gray-300 whitespace-pre-wrap bg-gray-950/60 border border-gray-800 rounded-lg p-3">{exp.content}</p>
        </div>

        {!!(exp.tags || []).length && (
          <div className="flex flex-wrap gap-1.5">
            {exp.tags.map((t, i) => (
              <span key={i} className="px-2 py-0.5 rounded bg-gray-800 text-[11px] text-gray-300">#{t}</span>
            ))}
          </div>
        )}

        {(!!exp.supersedes?.length || exp.replaced_by) && (
          <div className="text-xs text-gray-400 space-y-1">
            <p className="text-gray-500">谱系</p>
            {!!exp.supersedes?.length && (
              <p>取代了：{exp.supersedes.map(id => (
                <button key={id} onClick={() => onOpenEpisode('')} className="text-blue-400 hover:underline mr-2">{titleOf(id)}</button>
              ))}</p>
            )}
            {exp.replaced_by && <p className="text-amber-400">已被「{titleOf(exp.replaced_by)}」取代</p>}
          </div>
        )}

        <div>
          <p className="text-xs text-gray-500 mb-1.5">证据链（可回溯到原始执行轨迹片段）</p>
          {!exp.evidence?.length ? <Empty text="无证据引用" /> : (
            <ul className="space-y-1.5">
              {exp.evidence.map((ev, i) => (
                <li key={i} className="rounded-lg border border-gray-800 bg-gray-950/60 p-2.5">
                  <div className="flex items-center gap-2 text-[10px] text-gray-500 flex-wrap">
                    <span className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-300">{ev.kind}</span>
                    <span className="font-mono">{ev.ref}</span>
                    <span>{fmt(ev.at)}</span>
                    {ev.episode_id && (
                      <button onClick={() => onOpenEpisode(ev.episode_id)} className="text-blue-400 hover:underline">
                        查看轨迹 {ev.episode_id.slice(0, 8)}…
                      </button>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-gray-300 whitespace-pre-wrap">{ev.excerpt}</p>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <p className="text-xs text-gray-500 mb-1.5">演进历史</p>
          {!exp.history?.length ? <Empty text="暂无历史" /> : (
            <ul className="space-y-1">
              {exp.history.map((h, i) => (
                <li key={i} className="text-[11px] text-gray-400">
                  <span className="text-gray-300">{h.action}</span> · {h.actor} · {fmt(h.at)}
                  {!!h.note && <span className="text-gray-600"> · {String(h.note)}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Modal>
  );
}

function EpisodeDetailView({ ep }: { ep: MemoryEpisodeDetail }): JSX.Element {
  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`text-xs ${EP_STATUS_META[ep.status]?.cls || 'text-gray-400'}`}>
            ● {EP_STATUS_META[ep.status]?.label || ep.status}
          </span>
          <h4 className="text-sm text-gray-200">{ep.task || '(无任务描述)'}</h4>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-gray-500">
          <span>目标：{ep.target || '—'}</span>
          <span>智能体：{ep.agent_key || '—'}</span>
          <span>开始：{fmt(ep.start_at)}</span>
          <span>结束：{fmt(ep.end_at)}</span>
          <span>会话：{ep.session_id || '—'}</span>
          <span>实体：{(ep.entity_keys || []).length}</span>
        </div>
      </div>

      {ep.error && (
        <div className="rounded-lg border border-red-600/30 bg-red-600/5 p-3 text-xs text-red-300">{ep.error}</div>
      )}
      {ep.result_text && (
        <div>
          <p className="text-xs text-gray-500 mb-1">结论</p>
          <p className="text-xs text-gray-300 whitespace-pre-wrap bg-gray-950/60 border border-gray-800 rounded-lg p-3">{ep.result_text}</p>
        </div>
      )}

      <div>
        <p className="text-xs text-gray-500 mb-1.5">执行轨迹（{ep.steps?.length || 0} 步）</p>
        {!ep.steps?.length ? <Empty text="无步骤记录" /> : (
          <ol className="space-y-2">
            {ep.steps.map((s, i) => <StepRow key={i} s={s} />)}
          </ol>
        )}
      </div>
    </div>
  );
}

function StepRow({ s }: { s: MemoryEpisodeStep }): JSX.Element {
  const isTool = s.type === 'tool_call';
  return (
    <li className="rounded-lg border border-gray-800 bg-gray-950/50 p-2.5">
      <div className="flex items-center gap-2 text-[11px] flex-wrap">
        <span className={`px-1.5 py-0.5 rounded ${isTool ? 'bg-blue-500/15 text-blue-300' : 'bg-gray-800 text-gray-400'}`}>
          {isTool ? '工具调用' : s.type === 'decision' ? '决策' : '观察'}
        </span>
        {isTool && <span className="font-mono text-gray-200">{s.name}</span>}
        {isTool && s.status && <span className="text-gray-500">{s.status}</span>}
        <span className="text-gray-600">{fmt(s.ts)}</span>
      </div>
      {isTool ? (
        <>
          {!!s.arguments && (
            <pre className="mt-1.5 text-[11px] text-gray-400 bg-gray-900 rounded p-2 overflow-x-auto whitespace-pre-wrap">{s.arguments}</pre>
          )}
          {!!s.output && (
            <pre className="mt-1.5 text-[11px] text-gray-300 bg-gray-900 rounded p-2 overflow-auto max-h-56 whitespace-pre-wrap">{s.output}</pre>
          )}
        </>
      ) : (
        <p className="mt-1.5 text-xs text-gray-300 whitespace-pre-wrap">{s.text}</p>
      )}
    </li>
  );
}

function ExperienceEditor({
  initial,
  onClose,
  onSaved,
}: {
  initial: MemoryExperience | null;
  onClose: () => void;
  onSaved: () => void;
}): JSX.Element {
  const { addToast } = useApp();
  const [title, setTitle] = useState(initial?.title || '');
  const [content, setContent] = useState(initial?.content || '');
  const [tagsText, setTagsText] = useState((initial?.tags || []).join(', '));
  const [kind, setKind] = useState<string>(initial?.kind || 'method');
  const [scope, setScope] = useState(initial?.scope || 'global');
  const [importance, setImportance] = useState(initial?.importance ?? 3);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!title.trim() || !content.trim()) {
      addToast({ type: 'error', title: '标题与正文不能为空' });
      return;
    }
    setSaving(true);
    try {
      const tags = tagsText.split(/[,，]/).map(t => t.trim()).filter(Boolean);
      if (initial) {
        await updateMemoryExperience(initial.id, {
          title: title.trim(),
          content: content.trim(),
          tags,
          kind: kind as MemoryExperienceKind,
          importance,
          note: note.trim() || '人工修订',
        });
        addToast({ type: 'success', title: '经验已修订' });
      } else {
        await createMemoryExperience({
          title: title.trim(),
          content: content.trim(),
          tags,
          kind: kind as MemoryExperienceKind,
          scope: scope.trim() || 'global',
          importance,
        });
        addToast({ type: 'success', title: '经验已新增', message: '状态为生效（active）' });
      }
      onSaved();
    } catch (err: any) {
      addToast({ type: 'error', title: '保存失败', message: err.message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={initial ? '编辑经验（REVISE，留痕）' : '新增经验'} onClose={onClose}>
      <div className="space-y-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1.5">标题</label>
          <input
            value={title}
            onChange={e => setTitle(e.target.value)}
            className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1.5">经验正文（自然语言，Agent 检索后自行参考）</label>
          <textarea
            value={content}
            onChange={e => setContent(e.target.value)}
            rows={8}
            className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500 resize-y"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">标签（逗号分隔）</label>
            <input
              value={tagsText}
              onChange={e => setTagsText(e.target.value)}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">类型</label>
            <select
              value={kind}
              onChange={e => setKind(e.target.value)}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500"
            >
              <option value="method">方法（可复用套路）</option>
              <option value="lesson">教训（避坑）</option>
              <option value="intel">情报（目标事实）</option>
            </select>
          </div>
        </div>
        {!initial && (
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">作用域（global 或 agent:&lt;key&gt;）</label>
            <input
              value={scope}
              onChange={e => setScope(e.target.value)}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500"
            />
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">重要度（0-5）</label>
            <select
              value={importance}
              onChange={e => setImportance(Number(e.target.value))}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500"
            >
              {[0, 1, 2, 3, 4, 5].map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          {initial && (
            <div>
              <label className="block text-xs text-gray-500 mb-1.5">修订理由（写入历史）</label>
              <input
                value={note}
                onChange={e => setNote(e.target.value)}
                placeholder="例如：补充绕过条件"
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 outline-none focus:border-blue-500"
              />
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <button onClick={onClose} className="px-4 py-2 text-xs text-gray-400 hover:text-gray-200">取消</button>
          <button
            onClick={save}
            disabled={saving}
            className="px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg"
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
