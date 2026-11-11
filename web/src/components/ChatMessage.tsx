import { useState, useRef, useEffect, useMemo, memo } from 'react';
import type { ChatMessage as ChatMessageType, IntermediateData } from '../types';
import { buildToolActivities, type ToolActivity } from '../lib/toolActivity';
import type { JSX } from 'react';

/**
 * 单条思考详情参与 HTML 渲染的字符上限。
 * 根因：CTF/渗透长任务（十余分钟）中 reasoning 经 SSE 持续 append，
 * 单块 detail 实测可达 400KB+；展开状态下每次增量都触发全量正则扫描
 * 与 DOM 全量替换，O(n²) 复杂度最终撑崩页面。
 * 只渲染尾部窗口（最新推理最有价值），头部完整内容已由后端持久化，
 * 用户可在任务结束后的历史记录中回看。
 */
const MAX_DETAIL_RENDER_CHARS = 12000;

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return ts;
  }
}

const roleConfig: Record<string, { label: string; bg: string; text: string; border: string }> = {
  user: { label: '你', bg: 'bg-blue-600/10', text: 'text-blue-400', border: 'border-blue-600/20' },
  assistant: { label: '智脑', bg: 'bg-emerald-600/10', text: 'text-emerald-400', border: 'border-emerald-600/20' },
  system: { label: '系统', bg: 'bg-amber-600/10', text: 'text-amber-400', border: 'border-amber-600/20' },
  tool: { label: '工具', bg: 'bg-gray-600/10', text: 'text-gray-400', border: 'border-gray-600/20' },
};

/** 中间产物类型的视觉配置 */
const intermediateConfig: Record<string, { icon: string; border: string; bg: string }> = {
  function_call:        { icon: '⚡', border: 'border-blue-500/20',   bg: 'bg-blue-600/5' },
  function_call_output: { icon: '📋', border: 'border-gray-500/20',  bg: 'bg-gray-600/5' },
  reasoning:            { icon: '💭', border: 'border-purple-500/20', bg: 'bg-purple-600/5' },
  handoff:              { icon: '↗️', border: 'border-amber-500/20',  bg: 'bg-amber-600/5' },
};

/** F-03: 判断中间产物是否为失败的工具调用（错误/拒绝/超时），用于切换红色错误图标 */
function isErrorIntermediate(item: IntermediateData): boolean {
  const label = (item.label || '').trim();
  if (label.startsWith('错误') || label.startsWith('失败')) return true;
  if (/^\[?(失败|超时|异常|拒绝|denied)\]?/i.test(label)) return true;
  return /\b(failed|error|timeout|denied)\b/i.test(item.detail || '');
}

/**
 * F-16: 在已渲染的 HTML 上做防御性清洗。
 * 渲染器本身已先 escape 输入再构造标签，这里作为纵深防御：
 * 剥离 <script>/<style>/<iframe> 等危险标签、移除 on* 事件属性、阻断 javascript: 协议。
 */
function sanitizeHtml(html: string): string {
  return html
    // 移除成对的 script/style/iframe/object/embed 标签（含内容）
    .replace(/<\s*(script|style|iframe|object|embed)[\s\S]*?<\/\s*\1\s*>/gi, '')
    // 移除未闭合或自闭合的危险标签
    .replace(/<\s*(script|style|iframe|object|embed|link|meta)[^>]*>/gi, '')
    // 移除 on* 事件属性（onerror=、onclick= 等）
    .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
    // 阻断 javascript:/vbscript: 协议的 href/src
    .replace(/(href|src)\s*=\s*("|')\s*(javascript|vbscript):[^"']*\2/gi, '$1="#"');
}

export const ChatMessage = memo(function ChatMessage({ msg }: { msg: ChatMessageType }): JSX.Element {
  const hasIntermediates = Array.isArray(msg.intermediates) && msg.intermediates.length > 0;
  const isUser = msg.role === 'user';

  // 工具调用/输出配对成“执行轨迹”；reasoning / handoff 仍走折叠思考块。
  const activities = useMemo(
    () => buildToolActivities(msg.intermediates, !!msg.isStreaming),
    [msg.intermediates, msg.isStreaming],
  );
  const thinkingItems = useMemo(
    () => (msg.intermediates || []).filter(
      item => item.itemType === 'reasoning' || item.itemType === 'handoff',
    ),
    [msg.intermediates],
  );

  // ── 思考过程：只保留推理/交接块（工具过程由 ActivityTimeline 统一展示） ──
  const thinkingBlocks = thinkingItems.length > 0 && !isUser ? (
    <div className="mb-2 space-y-1.5">
      {thinkingItems.map((item, i) => (
        <ThinkingBlock
          key={`${msg.id}-int-${i}`}
          item={item}
          timestamp={msg.timestamp}
          isLast={i === thinkingItems.length - 1 && !!msg.isStreaming}
        />
      ))}
    </div>
  ) : null;

  // ── 正常对话消息 ──
  const config = roleConfig[msg.role] || roleConfig.system;
  // 正文 Markdown 转换缓存：流式 delta 仅改变当前消息，
  // 其余消息因 React.memo 跳过重渲染（见导出处）
  const contentHtml = useMemo(
    () => (msg.content ? renderMarkdown(msg.content) : ''),
    [msg.content],
  );

  return (
    <div className={`animate-fade-in mb-6 ${isUser ? 'flex justify-end' : ''}`}>
      {!isUser && (
        <div className="flex items-center gap-2 mb-1.5 ml-1">
          <span className={`text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded ${config.bg} ${config.text} border ${config.border}`}>
            {config.label}
          </span>
          <span className="text-[10px] text-gray-600">{formatTime(msg.timestamp)}</span>
        </div>
      )}

      {/* 工具执行轨迹：一行一个动作，参数/输出可展开 */}
      {!isUser && activities.length > 0 && (
        <ActivityTimeline activities={activities} isStreaming={!!msg.isStreaming} />
      )}

      {/* Thinking blocks above the main message */}
      {thinkingBlocks}

      {(msg.content || !hasIntermediates) && (
      <div
        className={`max-w-[85%] px-4 py-3 rounded-xl text-sm leading-relaxed ${
          isUser
            ? 'bg-blue-600 text-white rounded-br-md'
            : `bg-gray-800/80 border border-gray-700/50 rounded-bl-md ${msg.isStreaming ? 'border-blue-500/50 shadow-[0_0_12px_rgba(59,130,246,0.1)] cursor-blink' : ''}`
        }`}
      >
        {msg.content ? (
          <div className="prose-chat break-words space-y-0.5" dangerouslySetInnerHTML={{ __html: contentHtml }} />
        ) : msg.isStreaming ? (
          <span className="text-gray-500 italic">思考中...</span>
        ) : (
          <span className="text-gray-500 italic">(空消息)</span>
        )}

        {/* 多模态附件展示 */}
        {Array.isArray(msg.attachments) && msg.attachments.length > 0 && (
          <div className={`mt-2 flex flex-wrap gap-1.5 ${isUser ? 'justify-end' : ''}`}>
            {msg.attachments.map((fn, i) => (
              <span
                key={`${msg.id}-att-${i}`}
                className={`inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded border ${
                  isUser
                    ? 'bg-blue-500/20 border-blue-400/30 text-blue-50'
                    : 'bg-gray-700/50 border-gray-600 text-gray-300'
                }`}
                title={fn}
              >
                <span>{attachmentIcon(fn)}</span>
                <span className="max-w-[160px] truncate">{fn}</span>
              </span>
            ))}
          </div>
        )}
      </div>
      )}

      {isUser && (
        <div className="flex items-center gap-2 mt-1.5 mr-1 justify-end">
          <span className="text-[10px] text-gray-600">{formatTime(msg.timestamp)}</span>
          <span className="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded bg-blue-600/10 text-blue-400 border border-blue-600/20">
            你
          </span>
        </div>
      )}
    </div>
  );
});

/** 根据文件名返回附件类型图标 */
function attachmentIcon(filename: string): string {
  const lower = filename.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|bmp)$/.test(lower)) return '🖼️';
  if (/\.(py|js|ts|c|cpp|go|rs|java|php|sh|sql|html|css)$/.test(lower)) return '💻';
  if (/\.(zip|tar|gz|tgz|7z|bz2)$/.test(lower)) return '📦';
  if (/\.(pdf|docx?|pptx?|xlsx?)$/.test(lower)) return '📄';
  return '📎';
}

const activityStatusConfig: Record<ToolActivity['status'], { label: string; icon: string; cls: string; dot: string }> = {
  running: { label: '运行中', icon: '◌', cls: 'text-blue-400 border-blue-500/20 bg-blue-600/10', dot: 'bg-blue-400 animate-pulse' },
  ok: { label: '完成', icon: '✓', cls: 'text-emerald-400 border-emerald-500/20 bg-emerald-600/10', dot: 'bg-emerald-400' },
  error: { label: '失败', icon: '⚠', cls: 'text-red-400 border-red-500/20 bg-red-600/10', dot: 'bg-red-400' },
  denied: { label: '拒绝', icon: '⛔', cls: 'text-amber-400 border-amber-500/20 bg-amber-600/10', dot: 'bg-amber-400' },
  pending: { label: '未完成', icon: '–', cls: 'text-gray-500 border-gray-600/20 bg-gray-700/10', dot: 'bg-gray-500' },
};

/** 工具执行轨迹：把 AI 的每一步动作压缩成“工具 + 关键参数 + 状态”，避免只看到最终报告。 */
function ActivityTimeline({ activities, isStreaming }: { activities: ToolActivity[]; isStreaming: boolean }): JSX.Element {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? activities : activities.slice(-6);
  const hiddenCount = activities.length - visible.length;
  const errorCount = activities.filter(a => a.status === 'error').length;
  const running = activities.some(a => a.status === 'running');

  return (
    <div className="ml-2 mb-2 max-w-[85%] rounded-xl border border-gray-700/50 bg-gray-900/45 overflow-hidden">
      <div className="px-3 py-2 flex items-center gap-2 border-b border-gray-800/70">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">执行轨迹</span>
        <span className="text-[10px] text-gray-600">{activities.length} 次工具</span>
        {errorCount > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-600/10 border border-red-600/20 text-red-400">
            {errorCount} 失败
          </span>
        )}
        {running && isStreaming && (
          <span className="text-[10px] text-blue-400 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-ping" />
            执行中
          </span>
        )}
        <span className="flex-1" />
        {hiddenCount > 0 && (
          <button
            onClick={() => setShowAll(true)}
            className="text-[10px] text-blue-400 hover:text-blue-300 transition-colors"
          >
            查看全部 +{hiddenCount}
          </button>
        )}
        {showAll && activities.length > 6 && (
          <button
            onClick={() => setShowAll(false)}
            className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
          >
            收起
          </button>
        )}
      </div>
      <div className="divide-y divide-gray-800/50">
        {visible.map((activity) => {
          const cfg = activityStatusConfig[activity.status];
          const duration = typeof activity.duration === 'number'
            ? (activity.duration >= 0.1 ? `${activity.duration.toFixed(1)}s` : `${Math.max(1, Math.round(activity.duration * 1000))}ms`)
            : '';
          const outputPreview = activity.output
            ? activity.output.replace(/\s+/g, ' ').trim().slice(0, 90)
            : '';
          return (
            <details key={activity.key} className="group">
              <summary className="list-none cursor-pointer px-3 py-1.5 hover:bg-gray-800/40 transition-colors">
                <div className="flex items-center gap-2 min-w-0">
                  <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${cfg.dot}`} />
                  <span className={`text-[11px] font-mono flex-shrink-0 ${cfg.cls.split(' ').filter(c => c.startsWith('text-')).join(' ')}`}>
                    {activity.label}
                  </span>
                  {activity.agent && (
                    <span className="text-[9px] px-1 py-0.5 rounded bg-gray-800 text-gray-500 flex-shrink-0" title={activity.agent}>
                      {activity.agent}
                    </span>
                  )}
                  <span className="text-[11px] text-gray-400 truncate font-mono">
                    {activity.summary}
                  </span>
                  <span className="flex-1" />
                  {duration && <span className="text-[9px] text-gray-600 flex-shrink-0">{duration}</span>}
                  <span className={`text-[9px] px-1.5 py-0.5 rounded border flex-shrink-0 ${cfg.cls}`}>
                    {cfg.icon} {cfg.label}
                  </span>
                  <span className="text-[9px] text-gray-700 group-open:rotate-90 transition-transform flex-shrink-0">▶</span>
                </div>
                {outputPreview && (
                  <div className="mt-0.5 ml-3.5 text-[10px] text-gray-600 truncate font-mono">
                    → {outputPreview}{activity.output.length > 90 ? '…' : ''}
                  </div>
                )}
              </summary>
              <div className="px-3 pb-2 ml-3.5 space-y-1.5">
                {activity.argsDetail && (
                  <div>
                    <div className="text-[9px] text-gray-600 uppercase tracking-wider mb-0.5">参数</div>
                    <pre className="max-h-40 overflow-auto rounded-md bg-gray-950/70 border border-gray-800 px-2 py-1.5 text-[10px] text-gray-400 whitespace-pre-wrap break-all">
                      {activity.argsDetail}
                    </pre>
                  </div>
                )}
                {activity.output && (
                  <div>
                    <div className="text-[9px] text-gray-600 uppercase tracking-wider mb-0.5">输出</div>
                    <pre className="max-h-64 overflow-auto rounded-md bg-gray-950/70 border border-gray-800 px-2 py-1.5 text-[10px] text-gray-400 whitespace-pre-wrap break-all">
                      {activity.output.length > 12000
                        ? `${activity.output.slice(0, 12000)}\n\n[输出较长，仅显示前 12000 字符；完整内容见会话日志]`
                        : activity.output}
                    </pre>
                  </div>
                )}
                {!activity.output && activity.status === 'pending' && (
                  <div className="text-[10px] text-gray-600 italic">该步骤未返回输出（可能被中断或未执行完）。</div>
                )}
              </div>
            </details>
          );
        })}
      </div>
    </div>
  );
}

/** 单个思考块：折叠/展开，流式时自动展开 */
function ThinkingBlock({ item, timestamp, isLast }: { item: IntermediateData; timestamp: string; isLast: boolean }): JSX.Element {
  const [expanded, setExpanded] = useState(true); // expanded by default
  const ico = intermediateConfig[item.itemType] || intermediateConfig.handoff;
  // F-03: 失败的工具调用切换为红色错误图标与红色边框
  const isError = isErrorIntermediate(item);
  const displayIcon = isError ? '⚠' : ico.icon;
  const containerBorder = isError ? 'border-red-500/30' : ico.border;
  const containerBg = isError ? 'bg-red-600/5' : ico.bg;
  const iconColor = isError ? 'text-red-400' : '';

  // 长内容防护：超长 detail 只渲染尾部窗口（最新推理最有价值）。
  // useMemo 让未变化的思考块在消息列表重渲染时不重跑昂贵的正则转换；
  // 折叠时不计算 HTML（detail 再大也零开销）。
  const fullDetail = typeof item.detail === 'string' ? item.detail : String(item.detail ?? '');
  const detailOversize = fullDetail.length > MAX_DETAIL_RENDER_CHARS;
  const renderedHtml = useMemo(() => {
    if (!expanded) return '';
    const windowText = detailOversize
      ? fullDetail.slice(-MAX_DETAIL_RENDER_CHARS)
      : fullDetail;
    return renderThinkingDetail(windowText);
  }, [expanded, fullDetail, detailOversize]);

  // Collapse when streaming ends (isLast transitions from true to false)
  const wasLast = useRef(isLast);
  useEffect(() => {
    if (wasLast.current && !isLast) {
      // Streaming just ended, collapse all
      setExpanded(false);
    }
    wasLast.current = isLast;
  }, [isLast]);

  return (
    <div className="ml-2 group">
      <button
        onClick={() => setExpanded(!expanded)}
        aria-label={isError ? '失败的步骤，点击展开详情' : '步骤详情，点击展开'}
        aria-expanded={expanded}
        className={`w-full text-left flex items-center gap-2 px-3 py-1.5 rounded-lg border ${containerBorder} ${containerBg} hover:bg-gray-700/30 transition-colors cursor-pointer ${
          isLast ? 'animate-pulse border-opacity-60' : ''
        }`}
      >
        <span
          className="text-[10px] text-gray-500 flex-shrink-0 transition-transform duration-200"
          style={{ transform: expanded ? 'rotate(90deg)' : '' }}
          aria-hidden="true"
        >
          ▶
        </span>
        <span className={`text-xs flex-shrink-0 ${iconColor}`} aria-hidden="true">{displayIcon}</span>
        <span className="text-xs text-gray-300 font-mono truncate flex-1">
          {item.label}
        </span>
        {isLast && (
          <span className="text-[10px] text-gray-500 flex-shrink-0 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-ping inline-block" />
            <span className="text-xs text-blue-400/70">running</span>
          </span>
        )}
        <span className="text-[10px] text-gray-600 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          {formatTime(timestamp)}
        </span>
      </button>
      {expanded && (
        <div className={`mt-1 mx-1 px-3 py-2 rounded-lg border ${ico.border} bg-gray-800/40 max-h-60 overflow-y-auto`}>
          {detailOversize && (
            <div className="text-[10px] text-amber-400/80 mb-1 pb-1 border-b border-gray-700/50">
              思考过程较长（共 {fullDetail.length.toLocaleString()} 字符），仅渲染最近 {MAX_DETAIL_RENDER_CHARS.toLocaleString()} 字符；完整记录已随任务存档。
            </div>
          )}
          <div className="text-xs whitespace-pre-wrap break-words font-mono"
               dangerouslySetInnerHTML={{ __html: renderedHtml }} />
        </div>
      )}
    </div>
  );
}

/** 渲染思考块详情：在纯文本基础上，对证实/证否行着色 */
function renderThinkingDetail(text: string): string {
  // Escape HTML
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

  // 逐行处理，对含 ✓/✅ 的行标绿，含 ❌/✗/失败 的行标红
  const lines = html.split('\n');
  const out: string[] = [];
  for (const line of lines) {
    if (/[✅✓]/.test(line) && !/[❌✗]/.test(line)) {
      // 证实行：绿色
      out.push(`<span class="text-green-400">${line}</span>`);
    } else if (/[❌✗]/.test(line)) {
      // 证否行：红色
      out.push(`<span class="text-red-400">${line}</span>`);
    } else if (/^\[?(失败|超时|异常|死路)\]?/.test(line.trim()) || /\b(failed|error|timeout)\b/i.test(line)) {
      out.push(`<span class="text-red-400">${line}</span>`);
    } else if (/^\[?(成功|完成|已确认|已验证)\]?/.test(line.trim()) || /\b(success|confirmed|verified)\b/i.test(line)) {
      out.push(`<span class="text-green-400">${line}</span>`);
    } else {
      out.push(`<span class="text-gray-300">${line}</span>`);
    }
  }
  // F-16: 思考详情也做防御性清洗
  return sanitizeHtml(out.join('<br/>'));
}

function renderMarkdown(text: string): string {
  // Escape HTML（含引号，防止属性逃逸注入 XSS）
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

  // Code blocks: ```...```（先提取，避免内部被其他规则误伤）
  // F-05: 提取语言标签并显示在代码块顶部，便于识别代码类型
  const codeBlocks: string[] = [];
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, lang, code) => {
    const placeholder = `\x00CODEBLOCK${codeBlocks.length}\x00`;
    const safeLang = (lang || '').replace(/[^a-zA-Z0-9_+-]/g, '');
    const langTag = safeLang ? `<span class="code-lang">${safeLang}</span>` : '';
    codeBlocks.push(`<pre class="code-block" data-lang="${safeLang}">${langTag}<code>${code.trim()}</code></pre>`);
    return placeholder;
  });

  // 逐行处理块级元素
  const lines = html.split('\n');
  const outLines: string[] = [];
  let inTable = false;
  let inList = false;
  let listType = ''; // 'ul' | 'ol'
  let inQuote = false;

  function flushList() {
    if (inList) { outLines.push(`</${listType}>`); inList = false; }
  }
  function flushQuote() {
    if (inQuote) { outLines.push('</blockquote>'); inQuote = false; }
  }
  function flushTable() {
    if (inTable) { outLines.push('</tbody></table>'); inTable = false; }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Codeblock placeholder（整行）
    if (/^\x00CODEBLOCK\d+\x00$/.test(line.trim())) {
      flushList(); flushQuote(); flushTable();
      outLines.push(line.trim());
      continue;
    }

    // 水平分隔线
    if (/^---+\s*$/.test(line.trim())) {
      flushList(); flushQuote(); flushTable();
      outLines.push('<hr class="border-gray-700 my-2"/>');
      continue;
    }

    // 标题 # ~ ######
    const hMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (hMatch) {
      flushList(); flushQuote(); flushTable();
      const level = hMatch[1].length;
      const sizes = ['text-base', 'text-sm', 'text-sm', 'text-xs', 'text-xs', 'text-xs'];
      outLines.push(`<h${level} class="font-bold text-gray-100 mt-2 mb-1 ${sizes[level-1]}">${hMatch[2]}</h${level}>`);
      continue;
    }

    // 表格行（含 | 分隔）
    if (line.includes('|') && line.trim().startsWith('|')) {
      // 分隔行（|---|---|）跳过
      if (/^\|[\s:|-]+$/.test(line.trim())) continue;
      if (!inTable) {
        flushList(); flushQuote();
        outLines.push('<table class="w-full text-xs my-2 border-collapse">');
        outLines.push('<tbody>');
        inTable = true;
      }
      const cleanCells = line.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      outLines.push('<tr>' + cleanCells.map(c => `<td class="border border-gray-700 px-2 py-1 text-gray-300">${c}</td>`).join('') + '</tr>');
      continue;
    } else if (inTable) {
      flushTable();
    }

    // 引用块 > 
    if (line.trim().startsWith('&gt;')) {
      if (!inQuote) {
        flushList(); flushTable();
        outLines.push('<blockquote class="border-l-2 border-gray-600 pl-3 my-1 text-gray-400 italic">');
        inQuote = true;
      }
      outLines.push(line.replace(/^\s*&gt;\s?/, '') + '<br/>');
      continue;
    } else if (inQuote) {
      flushQuote();
    }

    // 无序列表 - 或 *
    if (/^\s*[-*]\s+/.test(line)) {
      if (!inList || listType !== 'ul') { flushList(); outLines.push('<ul class="list-disc list-inside my-1 space-y-0.5">'); inList = true; listType = 'ul'; }
      outLines.push(`<li class="text-gray-300">${line.replace(/^\s*[-*]\s+/, '')}</li>`);
      continue;
    }
    // 有序列表 1.
    if (/^\s*\d+\.\s+/.test(line)) {
      if (!inList || listType !== 'ol') { flushList(); outLines.push('<ol class="list-decimal list-inside my-1 space-y-0.5">'); inList = true; listType = 'ol'; }
      outLines.push(`<li class="text-gray-300">${line.replace(/^\s*\d+\.\s+/, '')}</li>`);
      continue;
    }
    // 空行
    if (!line.trim()) {
      flushList(); flushQuote(); flushTable();
      outLines.push('<div class="h-1"></div>');
      continue;
    }

    // 普通行
    flushList(); flushQuote(); flushTable();
    // Inline code: `code`
    let processed = line.replace(/`([^`]+)`/g, '<code class="bg-gray-800/60 px-1 py-0.5 rounded text-emerald-300 text-xs">$1</code>');
    // Bold/italic
    processed = processed.replace(/\*\*(.+?)\*\*/g, '<strong class="font-bold text-gray-100">$1</strong>');
    processed = processed.replace(/\*(.+?)\*/g, '<em class="italic">$1</em>');
    // Links
    processed = processed.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, label, url) => {
      const target = url.trim();
      if (/^(https?:|mailto:)/i.test(target)) {
        return `<a href="${target}" target="_blank" rel="noopener noreferrer" class="text-blue-400 underline hover:text-blue-300">${label}</a>`;
      }
      return label;
    });
    outLines.push(`<span class="text-gray-300">${processed}</span><br/>`);
  }

  flushList(); flushQuote(); flushTable();

  html = outLines.join('\n');

  // 还原代码块
  codeBlocks.forEach((block, i) => {
    html = html.replace(`\x00CODEBLOCK${i}\x00`, block);
  });

  // F-16: 输出前做防御性清洗，阻断可能的注入
  return sanitizeHtml(html);
}
