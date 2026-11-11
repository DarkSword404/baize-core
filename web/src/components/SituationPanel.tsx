import { useEffect, useState } from 'react';
import { getASMP } from '../api/client';
import type { ASMPHypothesis, ASMPResponse, ASMPUnknown } from '../types';
import type { JSX } from 'react';

interface Props {
  sessionId: string | null;
  open: boolean;
  onClose: () => void;
}

const NODE_LABEL: Record<string, string> = {
  host: '主机',
  web_application: 'Web 应用',
  api_service: 'API 服务',
  authentication_service: '认证服务',
  identity: '身份主体',
  database: '数据库',
  network_service: '网络服务',
  file_storage: '文件存储',
  cloud_resource: '云资源',
  security_control: '安全控制',
  data_asset: '数据资产',
};

export function SituationPanel({ sessionId, open, onClose }: Props): JSX.Element | null {
  const [data, setData] = useState<ASMPResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !sessionId) return;
    let cancelled = false;
    const load = async () => {
      try {
        const res = await getASMP(sessionId);
        if (!cancelled) {
          setData(res);
          setError('');
        }
      } catch (err: any) {
        if (!cancelled) setError(err?.message || '态势模型加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    setLoading(true);
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [open, sessionId]);

  if (!open) return null;

  const model = data?.asmp;
  const nodes = model?.nodes || [];
  const byType = new Map<string, typeof nodes>();
  for (const node of nodes) {
    if (!byType.has(node.type_id)) byType.set(node.type_id, []);
    byType.get(node.type_id)!.push(node);
  }
  const groupedHypotheses = new Map<string, ASMPHypothesis[]>();
  for (const h of model?.hypotheses || []) {
    const key = `${h.title}|${h.category}`;
    if (!groupedHypotheses.has(key)) groupedHypotheses.set(key, []);
    groupedHypotheses.get(key)!.push(h);
  }
  const groupedUnknowns = new Map<string, ASMPUnknown[]>();
  for (const u of model?.unknowns || []) {
    if (!groupedUnknowns.has(u.attribute)) groupedUnknowns.set(u.attribute, []);
    groupedUnknowns.get(u.attribute)!.push(u);
  }

  return (
    <div className="w-96 min-w-[384px] border-l border-gray-800 bg-gray-900/40 flex flex-col h-full">
      <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
        <div>
          <h2 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">攻击态势</h2>
          <p className="text-[10px] text-gray-600 mt-0.5">ASMP · 环境模型 / 威胁假设</p>
        </div>
        <div className="flex items-center gap-2">
          {loading && <span className="text-[10px] text-cyan-400 animate-pulse">同步中</span>}
          <button onClick={onClose} aria-label="关闭态势面板" className="p-1 rounded hover:bg-gray-800 text-gray-600 hover:text-gray-300">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {error && <div className="text-xs text-red-400 border border-red-500/20 bg-red-600/10 rounded-lg p-2">{error}</div>}
        {!data && !error && <div className="text-xs text-gray-600">加载中…</div>}
        {data && model && (
          <>
            <div className="text-[10px] text-gray-500 flex flex-wrap gap-2">
              <span>{model.nodes.length} 节点</span>
              <span>·</span>
              <span>{model.relations.length} 关系</span>
              <span>·</span>
              <span>{model.hypotheses.length} 假设</span>
              <span>·</span>
              <span>{model.unknowns.length} 未知</span>
              <span>·</span>
              <span>{model.tasks.length} 任务</span>
            </div>

            <pre className="text-[10px] leading-relaxed text-gray-400 whitespace-pre-wrap break-words font-mono bg-gray-950/40 border border-gray-800 rounded-lg p-3 max-h-72 overflow-auto">
              {data.brief}
            </pre>

            {[...byType.entries()].map(([type, rows]) => (
              <div key={type}>
                <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                  {NODE_LABEL[type] || type} · {rows.length}
                </div>
                <div className="space-y-1">
                  {rows.slice(0, 8).map(node => (
                    <div key={node.id} className="rounded-lg border border-gray-800 bg-gray-900/50 px-2.5 py-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-gray-300 truncate flex-1">{node.name}</span>
                        <span className="text-[9px] px-1 py-0.5 rounded bg-gray-800 text-gray-500">{node.status}</span>
                      </div>
                      {node.summary && <div className="text-[10px] text-gray-600 mt-0.5 line-clamp-2 break-words">{node.summary}</div>}
                    </div>
                  ))}
                </div>
              </div>
            ))}

            {groupedHypotheses.size > 0 && (
              <div>
                <div className="text-[10px] font-semibold text-amber-500/80 uppercase tracking-wider mb-1.5">
                  威胁假设 · {groupedHypotheses.size} 类 / {model.hypotheses.length} 个
                </div>
                <div className="space-y-1">
                  {[...groupedHypotheses.entries()].slice(0, 10).map(([key, hs]) => {
                    const [title, category] = key.split('|');
                    const maxConf = Math.max(...hs.map(h => Number(h.confidence || 0)));
                    return (
                      <div key={key} className="rounded-lg border border-amber-800/30 bg-amber-900/10 px-2.5 py-1.5">
                        <div className="flex items-center gap-1.5">
                          <span className="text-[10px] text-amber-200/80 truncate flex-1">{title}</span>
                          <span className="text-[9px] text-amber-400">{hs.length} 目标</span>
                        </div>
                        <div className="text-[9px] text-gray-600 mt-0.5">
                          {category} · max conf={maxConf.toFixed(2)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {groupedUnknowns.size > 0 && (
              <div>
                <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                  未知项 · {groupedUnknowns.size} 类 / {model.unknowns.length} 个
                </div>
                <div className="space-y-1">
                  {[...groupedUnknowns.entries()].slice(0, 10).map(([attribute, rows]) => (
                    <div key={attribute} className="text-[10px] text-gray-500">
                      <span className="text-gray-400">{attribute}</span> · {rows.length} 个目标 · {rows[0]?.reason}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {model.tasks.length > 0 && (
              <div>
                <div className="text-[10px] font-semibold text-cyan-500/80 uppercase tracking-wider mb-1.5">
                  探索任务建议 · {model.tasks.length}
                </div>
                <div className="space-y-1">
                  {model.tasks.slice(0, 8).map(t => (
                    <div key={t.id} className="rounded-lg border border-cyan-800/30 bg-cyan-900/10 px-2.5 py-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-cyan-200/80 truncate flex-1">{t.objective}</span>
                        <span className="text-[9px] text-cyan-400">{Number(t.priority || 0).toFixed(2)}</span>
                      </div>
                      <div className="text-[9px] text-gray-600 mt-0.5">{t.status}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
