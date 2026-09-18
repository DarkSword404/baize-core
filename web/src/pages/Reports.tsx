import { useCallback, useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import {
  listReports,
  listReportTemplates,
  getReport,
  deleteReport,
  downloadReport,
} from '../api/client';
import type { ReportRecord, ReportTemplate } from '../types';
import type { JSX } from 'react';

// ===========================================================================
//  轻量 Markdown 渲染器（报告预览专用：标题/列表/表格/代码块/引用）
// ===========================================================================

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function inlineMd(text: string): string {
  let html = escapeHtml(text);
  // 行内代码
  html = html.replace(/`([^`]+)`/g, '<code class="px-1 py-0.5 rounded bg-gray-800 text-emerald-300 text-[0.85em]">$1</code>');
  // 粗体/斜体
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // 链接（协议白名单）
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, label, url) => {
    const target = String(url).trim();
    if (/^(https?:|mailto:)/i.test(target)) {
      return `<a href="${target}" target="_blank" rel="noopener noreferrer" class="text-blue-400 underline">${label}</a>`;
    }
    return label;
  });
  return html;
}

function renderReportMarkdown(md: string): string {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out: string[] = [];
  let i = 0;
  let inCode = false;
  let codeBuf: string[] = [];
  let listType: '' | 'ul' | 'ol' = '';
  let inTable = false;

  const closeList = () => {
    if (listType) {
      out.push(listType === 'ul' ? '</ul>' : '</ol>');
      listType = '';
    }
  };
  const closeTable = () => {
    if (inTable) {
      out.push('</tbody></table>');
      inTable = false;
    }
  };

  while (i < lines.length) {
    const raw = lines[i];
    const line = raw;

    // 代码块开关
    if (/^```/.test(line.trimStart())) {
      if (inCode) {
        out.push(`<pre class="bg-gray-900 border border-gray-700 rounded-lg p-4 overflow-x-auto text-[13px] leading-relaxed"><code>${codeBuf.join('\n')}</code></pre>`);
        codeBuf = [];
        inCode = false;
      } else {
        closeList();
        closeTable();
        inCode = true;
      }
      i++;
      continue;
    }
    if (inCode) {
      codeBuf.push(escapeHtml(line));
      i++;
      continue;
    }

    // 表格
    if (line.includes('|') && i + 1 < lines.length &&
        /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      closeList();
      const parseRow = (r: string): string[] =>
        r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
      const headers = parseRow(line);
      out.push(
        '<table class="w-full border-collapse my-3 text-sm">' +
        '<thead><tr>' +
        headers.map(h => `<th class="border border-gray-700 bg-gray-800 px-3 py-2 text-left">${inlineMd(h)}</th>`).join('') +
        '</tr></thead><tbody>'
      );
      inTable = true;
      i += 2; // 跳过表头与分隔行
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        const cells = parseRow(lines[i]);
        out.push(
          '<tr>' +
          cells.map(c => `<td class="border border-gray-700 px-3 py-2 align-top">${inlineMd(c)}</td>`).join('') +
          '</tr>'
        );
        i++;
      }
      closeTable();
      continue;
    }

    // 标题
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      closeList();
      closeTable();
      const level = h[1].length;
      const sizes = ['text-2xl font-bold mt-6 mb-3 pb-2 border-b border-gray-700',
        'text-xl font-bold mt-5 mb-2', 'text-lg font-semibold mt-4 mb-2',
        'text-base font-semibold mt-3 mb-1'];
      out.push(`<h${level} class="${sizes[level - 1]}">${inlineMd(h[2])}</h${level}>`);
      i++;
      continue;
    }

    // 水平线
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      closeList();
      closeTable();
      out.push('<hr class="my-4 border-gray-700" />');
      i++;
      continue;
    }

    // 引用
    if (/^\s*>\s?/.test(line)) {
      closeList();
      closeTable();
      const quoteLines: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^\s*>\s?/, ''));
        i++;
      }
      out.push(`<blockquote class="border-l-4 border-blue-500 pl-4 my-2 text-gray-400 italic">${inlineMd(quoteLines.join(' '))}</blockquote>`);
      continue;
    }

    // 无序列表
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) {
      closeTable();
      if (listType !== 'ul') {
        closeList();
        out.push('<ul class="list-disc pl-6 my-2 space-y-1">');
        listType = 'ul';
      }
      out.push(`<li>${inlineMd(ul[1])}</li>`);
      i++;
      continue;
    }

    // 有序列表
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) {
      closeTable();
      if (listType !== 'ol') {
        closeList();
        out.push('<ol class="list-decimal pl-6 my-2 space-y-1">');
        listType = 'ol';
      }
      out.push(`<li>${inlineMd(ol[1])}</li>`);
      i++;
      continue;
    }

    // 空行
    if (!line.trim()) {
      closeList();
      i++;
      continue;
    }

    // 普通段落
    closeList();
    closeTable();
    out.push(`<p class="my-2 leading-relaxed">${inlineMd(line)}</p>`);
    i++;
  }

  closeList();
  closeTable();
  if (inCode) {
    out.push(`<pre class="bg-gray-900 border border-gray-700 rounded-lg p-4 overflow-x-auto"><code>${codeBuf.join('\n')}</code></pre>`);
  }
  return out.join('\n');
}

// ===========================================================================
//  页面组件
// ===========================================================================

type Tab = 'reports' | 'templates';

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function formatDate(ts: string): string {
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return ts;
  }
}

export function Reports(): JSX.Element {
  const { addToast } = useApp();
  const [tab, setTab] = useState<Tab>('reports');
  const [reports, setReports] = useState<ReportRecord[]>([]);
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<ReportRecord | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [expandedTpl, setExpandedTpl] = useState<string | null>(null);

  const loadReports = useCallback(async () => {
    setLoading(true);
    try {
      const r = await listReports();
      setReports(r.reports);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载报告失败', message: err.message });
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  const loadTemplates = useCallback(async () => {
    try {
      const r = await listReportTemplates();
      setTemplates(r.templates);
    } catch (err: any) {
      addToast({ type: 'error', title: '加载模板失败', message: err.message });
    }
  }, [addToast]);

  useEffect(() => {
    loadReports();
    loadTemplates();
  }, [loadReports, loadTemplates]);

  async function handlePreview(id: string) {
    setPreviewLoading(true);
    setPreview(null);
    try {
      const detail = await getReport(id);
      setPreview(detail);
    } catch (err: any) {
      addToast({ type: 'error', title: '预览失败', message: err.message });
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handleDownload(rec: ReportRecord) {
    try {
      await downloadReport(rec.id, rec.title || rec.id);
      addToast({ type: 'success', title: '已开始下载', message: rec.title });
    } catch (err: any) {
      addToast({ type: 'error', title: '下载失败', message: err.message });
    }
  }

  async function handleDelete(rec: ReportRecord) {
    if (!window.confirm(`确定删除报告「${rec.title}」？\n删除后不可恢复。`)) {
      return;
    }
    try {
      await deleteReport(rec.id);
      setReports(prev => prev.filter(r => r.id !== rec.id));
      if (preview?.id === rec.id) setPreview(null);
      addToast({ type: 'info', title: '报告已删除' });
    } catch (err: any) {
      addToast({ type: 'error', title: '删除失败', message: err.message });
    }
  }

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8">
      {/* 标题栏 */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">报告管理</h1>
          <p className="text-sm text-gray-500 mt-1">
            内置报告模板，白泽在对话中生成的报告会自动归档于此
          </p>
        </div>
        <button
          onClick={() => { loadReports(); loadTemplates(); }}
          className="px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-sm text-gray-300 border border-gray-700 transition-colors"
        >
          ↻ 刷新
        </button>
      </div>

      {/* Tab 切换 */}
      <div className="flex gap-1 p-1 rounded-lg bg-gray-900 border border-gray-800 w-fit mb-5">
        {([
          { id: 'reports', label: `报告列表 (${reports.length})` },
          { id: 'templates', label: `报告模板 (${templates.length})` },
        ] as Array<{ id: Tab; label: string }>).map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
              tab === t.id
                ? 'bg-blue-600 text-white'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ── 报告列表 ── */}
      {tab === 'reports' && (
        <div className="rounded-xl border border-gray-800 overflow-hidden">
          {loading ? (
            <div className="p-10 text-center text-gray-500 text-sm">加载中…</div>
          ) : reports.length === 0 ? (
            <div className="p-14 text-center">
              <div className="text-4xl mb-3">📄</div>
              <div className="text-gray-400 font-medium mb-1">暂无报告</div>
              <div className="text-gray-600 text-sm">
                在对话中要求白泽生成报告（如"给一份 XX 漏洞的详细利用报告"），
                报告会自动保存到这里
              </div>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-900 text-gray-400 text-xs uppercase tracking-wider">
                  <th className="px-4 py-3 text-left font-medium">报告标题</th>
                  <th className="px-4 py-3 text-left font-medium w-36">模板</th>
                  <th className="px-4 py-3 text-left font-medium w-20">状态</th>
                  <th className="px-4 py-3 text-left font-medium w-20">大小</th>
                  <th className="px-4 py-3 text-left font-medium w-28">生成时间</th>
                  <th className="px-4 py-3 text-right font-medium w-44">操作</th>
                </tr>
              </thead>
              <tbody>
                {reports.map(rec => (
                  <tr
                    key={rec.id}
                    className="border-t border-gray-800 hover:bg-gray-800/40 transition-colors"
                  >
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-200 break-all">{rec.title}</div>
                      <div className="text-xs text-gray-600 mt-0.5 font-mono">{rec.id}</div>
                    </td>
                    <td className="px-4 py-3">
                      {rec.template_name
                        ? <span className="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 text-xs border border-indigo-500/20">{rec.template_name}</span>
                        : <span className="text-gray-600 text-xs">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-xs ${rec.status === 'done' ? 'text-emerald-400' : 'text-amber-400'}`}>
                        {rec.status === 'done' ? '✓ 已完成' : '✎ 草稿'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-400 text-xs">{formatSize(rec.size)}</td>
                    <td className="px-4 py-3 text-gray-400 text-xs">{formatDate(rec.created_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => handlePreview(rec.id)}
                          className="px-2.5 py-1 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs border border-gray-700"
                        >
                          预览
                        </button>
                        <button
                          onClick={() => handleDownload(rec)}
                          className="px-2.5 py-1 rounded-md bg-blue-600/20 hover:bg-blue-600/30 text-blue-300 text-xs border border-blue-600/30"
                        >
                          下载
                        </button>
                        <button
                          onClick={() => handleDelete(rec)}
                          className="px-2.5 py-1 rounded-md bg-red-600/10 hover:bg-red-600/20 text-red-400 text-xs border border-red-600/20"
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ── 模板列表 ── */}
      {tab === 'templates' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {templates.map(tpl => {
            const expanded = expandedTpl === tpl.id;
            return (
              <div
                key={tpl.id}
                className="rounded-xl border border-gray-800 bg-gray-900/40 overflow-hidden flex flex-col"
              >
                <div className="p-5 flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-lg">
                      {tpl.id === 'vuln_exploit' ? '🎯'
                        : tpl.id === 'pentest_report' ? '🛡️' : '🚩'}
                    </span>
                    <h3 className="font-semibold text-gray-100">{tpl.name}</h3>
                  </div>
                  <p className="text-xs text-gray-500 leading-relaxed mb-3">{tpl.description}</p>
                  <div className="text-xs text-gray-600 mb-2">
                    {tpl.sections.length} 个章节 · id: <code className="text-gray-500">{tpl.id}</code>
                  </div>
                  {expanded && (
                    <ol className="mt-3 space-y-2 border-t border-gray-800 pt-3">
                      {tpl.sections.map((s, idx) => (
                        <li key={s.title} className="text-xs">
                          <div className="text-gray-300 font-medium">
                            {idx + 1}. {s.title}
                          </div>
                          <div className="text-gray-600 mt-0.5 leading-relaxed pl-4">{s.guidance}</div>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
                <button
                  onClick={() => setExpandedTpl(expanded ? null : tpl.id)}
                  className="w-full px-4 py-2.5 text-xs text-gray-400 hover:text-gray-200 bg-gray-800/50 hover:bg-gray-800 border-t border-gray-800 transition-colors"
                >
                  {expanded ? '▲ 收起章节大纲' : '▼ 查看章节大纲'}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* ── 预览模态框 ── */}
      {(previewLoading || preview) && (
        <div
          className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4"
          onClick={() => { setPreview(null); setPreviewLoading(false); }}
        >
          <div
            className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-4xl max-h-[88vh] flex flex-col shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            {/* 模态头部 */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
              <div className="min-w-0">
                <h2 className="font-semibold text-gray-100 truncate">{preview?.title || '加载中…'}</h2>
                {preview?.template_name && (
                  <span className="text-xs text-indigo-300 mt-0.5 inline-block">{preview.template_name}</span>
                )}
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                {preview && (
                  <button
                    onClick={() => handleDownload(preview)}
                    className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm"
                  >
                    下载 .md
                  </button>
                )}
                <button
                  onClick={() => setPreview(null)}
                  className="px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm"
                >
                  关闭
                </button>
              </div>
            </div>
            {/* 模态正文 */}
            <div className="flex-1 overflow-y-auto px-8 py-6">
              {previewLoading ? (
                <div className="text-center text-gray-500 text-sm py-10">加载报告内容…</div>
              ) : (
                <div
                  className="report-preview text-gray-300 text-sm"
                  dangerouslySetInnerHTML={{
                    __html: renderReportMarkdown(preview?.content || '（空报告）'),
                  }}
                />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
