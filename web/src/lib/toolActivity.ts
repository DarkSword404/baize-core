import type { IntermediateData } from '../types';
import { toolNameCN } from '../i18n/translations';

export type ToolActivityStatus = 'running' | 'ok' | 'error' | 'denied' | 'pending';

export interface ToolActivity {
  key: string;
  toolName: string;
  label: string;
  summary: string;
  argsDetail: string;
  output: string;
  hasOutput: boolean;
  status: ToolActivityStatus;
  agent?: string;
  duration?: number;
  callId?: string;
}

function parseArguments(args: unknown): Record<string, unknown> | null {
  if (!args) return null;
  if (typeof args === 'object') return args as Record<string, unknown>;
  if (typeof args !== 'string') return null;
  try {
    const parsed = JSON.parse(args);
    return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function firstLine(value: unknown, max = 120): string {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  if (!text) return '';
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** 把工具参数压成一行人类可读摘要，例如 GET https://target/home */
export function summarizeToolCall(tool: string, args: unknown): string {
  const obj = parseArguments(args);
  if (!obj) return firstLine(args);

  const pick = (...keys: string[]) => {
    for (const key of keys) {
      if (obj[key] !== undefined && obj[key] !== null && obj[key] !== '') return obj[key];
    }
    return '';
  };

  switch (tool) {
    case 'http_request': {
      const method = String(pick('method') || 'GET').toUpperCase();
      return firstLine(`${method} ${pick('url', 'target')}`);
    }
    case 'http_probe':
    case 'whatweb_fingerprint':
    case 'nikto_scan':
    case 'waf_detect':
    case 'gobuster_enum':
      return firstLine(pick('url', 'target', 'host'));
    case 'dns_lookup':
      return firstLine(`${pick('domain', 'target')} ${pick('record_type') || 'A'}`);
    case 'port_scan':
    case 'nmap_scan':
      return firstLine(`${pick('target', 'host')} ${pick('ports') || 'common'} ${pick('scan_type')}`);
    case 'ssl_cert_info':
      return firstLine(`${pick('host')}:${pick('port') || 443}`);
    case 'generic_linux_command':
    case 'execute_cli_command':
      return firstLine(pick('command'));
    case 'execute_code':
      return firstLine(`${pick('language') || 'code'} · ${pick('description', 'code')}`);
    case 'read_blackboard':
      return firstLine(`scope=${pick('scope') || 'summary'}`);
    case 'write_fact':
      return firstLine(pick('label', 'detail'));
    case 'load_tool': {
      const names = obj.names;
      return firstLine(Array.isArray(names) ? names.join(', ') : names);
    }
    case 'search_worker_traces':
      return firstLine(pick('query'));
    case 'shared_browser_open':
      return firstLine(pick('url'));
    case 'shared_browser_click':
      return firstLine(`${pick('selector', 'text')}`);
    case 'shared_browser_fill':
      return firstLine(`${pick('selector')} ← ${pick('value')}`);
    case 'shared_browser_evaluate':
      return firstLine(pick('script', 'code'));
    default: {
      const preferred = pick('target', 'url', 'host', 'domain', 'command', 'query', 'path', 'name');
      if (preferred) return firstLine(preferred);
      const first = Object.values(obj).find(v => typeof v === 'string' || typeof v === 'number');
      return firstLine(first);
    }
  }
}

export function toolLabel(tool: string): string {
  return toolNameCN[tool] || tool || '未知工具';
}

export function looksLikeToolError(output: string): boolean {
  const text = (output || '').slice(0, 1200);
  if (!text.trim()) return false;
  return /(\[[^\]]*(失败|错误|异常)|执行失败|命令不存在|command not found|no such file|traceback|apierror|exception|timed? ?out|超时|denied|拒绝|未授权|scope_guard 拦截|工具调用循环检测|exit=[1-9]\d*)/i.test(text);
}

export function looksLikeDenied(output: string): boolean {
  return /(denied|拒绝|未授权|scope_guard 拦截)/i.test((output || '').slice(0, 800));
}

/**
 * 把 function_call / function_call_output 中间产物配对成可直接展示的执行轨迹。
 * 同一 callId 优先配对；旧历史没有 callId 时，按工具名 + 调用顺序兜底。
 */
export function buildToolActivities(
  intermediates: IntermediateData[] | undefined,
  streaming = false,
): ToolActivity[] {
  const items = intermediates || [];
  const activities: ToolActivity[] = [];
  const byCallId = new Map<string, ToolActivity>();
  const pendingByTool = new Map<string, ToolActivity[]>();

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.itemType === 'function_call') {
      const toolName = item.toolName || item.label || '未知工具';
      const summary = summarizeToolCall(toolName, item.detail);
      const activity: ToolActivity = {
        key: `${item.callId || 'call'}-${i}`,
        toolName,
        label: toolLabel(toolName),
        summary: summary || item.label,
        argsDetail: item.detail || '',
        output: '',
        hasOutput: false,
        status: 'running',
        agent: item.agent,
        callId: item.callId,
      };
      activities.push(activity);
      if (item.callId) byCallId.set(item.callId, activity);
      const queue = pendingByTool.get(toolName) || [];
      queue.push(activity);
      pendingByTool.set(toolName, queue);
      continue;
    }

    if (item.itemType === 'function_call_output') {
      const toolName = item.toolName || '';
      const callId = item.callId;
      let target = callId ? byCallId.get(callId) : undefined;
      if (!target) {
        const queue = pendingByTool.get(toolName) || [];
        target = [...queue].reverse().find(a => !a.hasOutput);
      }
      if (!target) {
        // 孤立输出（例如调用事件在历史裁剪中被丢弃）：仍展示出来，避免信息消失。
        target = {
          key: `output-${callId || i}`,
          toolName: toolName || '未知工具',
          label: toolLabel(toolName),
          summary: '工具输出',
          argsDetail: '',
          output: '',
          hasOutput: false,
          status: 'ok',
          agent: item.agent,
          callId,
        };
        activities.push(target);
      }
      target.output = item.detail || '';
      target.hasOutput = true;
      target.duration = item.duration;
      if (looksLikeDenied(target.output)) target.status = 'denied';
      else if (looksLikeToolError(target.output)) target.status = 'error';
      else target.status = 'ok';
      continue;
    }
  }

  for (const activity of activities) {
    if (!activity.hasOutput) {
      activity.status = streaming ? 'running' : 'pending';
    }
  }
  return activities;
}
