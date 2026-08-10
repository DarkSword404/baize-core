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
  assistant: { label: '白泽', bg: 'bg-emerald-600/10', text: 'text-emerald-400', border: 'border-emerald-600/20' },
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
          <div className="prose-chat whitespace-pre-wrap break-words" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }} />
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
          <pre className="text-xs text-gray-300 whitespace-pre-wrap font-mono break-all">
            {item.detail}
          </pre>
        </div>
      )}
    </div>
  );
}

function renderMarkdown(text: string): string {
  // Escape HTML
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Code blocks: ```...```
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) => {
    return `<pre>${code.trim()}</pre>`;
  });

  // Inline code: `code`
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Bold/italic
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  // Line breaks
  html = html.replace(/\n/g, '<br/>');

  return html;
}
