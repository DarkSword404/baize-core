import { useState, useRef, useEffect } from 'react';
import type { ChatMessage as ChatMessageType, IntermediateData } from '../types';
import type { JSX } from 'react';

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

export function ChatMessage({ msg }: { msg: ChatMessageType }): JSX.Element {
  const hasIntermediates = msg.intermediates && msg.intermediates.length > 0;
  const isUser = msg.role === 'user';

  // ── 思考过程：多个中间产物折叠块（在消息体上方） ──
  const thinkingBlocks = hasIntermediates && !isUser ? (
    <div className="mb-2 space-y-1.5">
      {msg.intermediates!.map((item, i) => (
        <ThinkingBlock
          key={`${msg.id}-int-${i}`}
          item={item}
          timestamp={msg.timestamp}
          isLast={i === msg.intermediates!.length - 1 && !!msg.isStreaming}
        />
      ))}
    </div>
  ) : null;

  // ── 正常对话消息 ──
  const config = roleConfig[msg.role] || roleConfig.system;

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

      {/* Thinking blocks above the main message */}
      {thinkingBlocks}

      <div
        className={`max-w-[85%] px-4 py-3 rounded-xl text-sm leading-relaxed ${
          isUser
            ? 'bg-blue-600 text-white rounded-br-md'
            : `bg-gray-800/80 border border-gray-700/50 rounded-bl-md ${msg.isStreaming ? 'border-blue-500/50 shadow-[0_0_12px_rgba(59,130,246,0.1)] cursor-blink' : ''}`
        }`}
      >
        {msg.content ? (
          <div className="prose-chat break-words space-y-0.5" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }} />
        ) : msg.isStreaming ? (
          <span className="text-gray-500 italic">思考中...</span>
        ) : (
          <span className="text-gray-500 italic">(空消息)</span>
        )}

        {/* 多模态附件展示 */}
        {msg.attachments && msg.attachments.length > 0 && (
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
}

/** 根据文件名返回附件类型图标 */
function attachmentIcon(filename: string): string {
  const lower = filename.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|bmp)$/.test(lower)) return '🖼️';
  if (/\.(py|js|ts|c|cpp|go|rs|java|php|sh|sql|html|css)$/.test(lower)) return '💻';
  if (/\.(zip|tar|gz|tgz|7z|bz2)$/.test(lower)) return '📦';
  if (/\.(pdf|docx?|pptx?|xlsx?)$/.test(lower)) return '📄';
  return '📎';
}

/** 单个思考块：折叠/展开，流式时自动展开 */
function ThinkingBlock({ item, timestamp, isLast }: { item: IntermediateData; timestamp: string; isLast: boolean }): JSX.Element {
  const [expanded, setExpanded] = useState(true); // expanded by default
  const ico = intermediateConfig[item.itemType] || intermediateConfig.handoff;

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
        className={`w-full text-left flex items-center gap-2 px-3 py-1.5 rounded-lg border ${ico.border} ${ico.bg} hover:bg-gray-700/30 transition-colors cursor-pointer ${
          isLast ? 'animate-pulse border-opacity-60' : ''
        }`}
      >
        <span
          className="text-[10px] text-gray-500 flex-shrink-0 transition-transform duration-200"
          style={{ transform: expanded ? 'rotate(90deg)' : '' }}
        >
          ▶
        </span>
        <span className="text-xs flex-shrink-0">{ico.icon}</span>
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
          <div className="text-xs whitespace-pre-wrap break-words font-mono"
               dangerouslySetInnerHTML={{ __html: renderThinkingDetail(item.detail) }} />
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
  return out.join('<br/>');
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
  const codeBlocks: string[] = [];
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) => {
    const placeholder = `\x00CODEBLOCK${codeBlocks.length}\x00`;
    codeBlocks.push(`<pre class="code-block">${code.trim()}</pre>`);
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

  return html;
}
