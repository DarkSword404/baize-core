/*/* ===== 白泽(Baize) API Client ===== */

import type {
  HealthResponse,
  AgentsResponse,
  ModelsResponse,
  SessionsResponse,
  SessionSummary,
  SessionDetail,
  CreateSessionRequest,
  InferenceRequest,
  InferenceResponse,
  UXSummarizeLiteRequest,
  UXSummarizeLiteResponse,
  UXTitleLiteRequest,
  UXTitleLiteResponse,
  CancelTaskResponse,
  InterruptResponse,
  AuthLoginRequest,
  AuthLoginResponse,
} from '../types';

export type SessionInfo = SessionSummary;

const DEFAULT_BASE = '/api/v1';

let apiBase = localStorage.getItem('baize_api_base') || DEFAULT_BASE;

export function setApiBase(url: string) {
  apiBase = url;
  localStorage.setItem('baize_api_base', url);
}

export function getApiBase(): string {
  return apiBase;
}

// ===== Auth via URL parameter (OpenClaw-style) =====
// Access the app like: http://localhost:5173/?token=<your-api-key>
let _authToken: string | null = null;
const SESSION_KEY = 'baize_url_token';

/** Extract token from URL query param and persist it in sessionStorage.
 *  SessionStorage survives Vite HMR reloads & page refreshes within the same tab. */
export function initAuthFromUrl(): void {
  const params = new URLSearchParams(window.location.search);
  const tokenFromUrl = params.get('token') || params.get('api_key') || null;
  if (tokenFromUrl) {
    _authToken = tokenFromUrl;
    sessionStorage.setItem(SESSION_KEY, tokenFromUrl);
    // Clean the URL visually without a full page reload
    const url = new URL(window.location.href);
    url.searchParams.delete('token');
    url.searchParams.delete('api_key');
    window.history.replaceState({}, '', url.toString());
  } else {
    // Recover token that survived an HMR module reload (sessionStorage persists)
    const saved = sessionStorage.getItem(SESSION_KEY);
    if (saved) {
      _authToken = saved;
    }
  }
}

/** Return the current auth token (URL param → sessionStorage → login session). */
export function getAuthToken(): string | null {
  return _authToken
    || sessionStorage.getItem(SESSION_KEY)
    || localStorage.getItem('baize_session_token');
}

function getToken(): string | null {
  return getAuthToken();
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['X-Baize-API-Key'] = token;
  return headers;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${apiBase}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: { ...authHeaders(), ...options?.headers },
  });
  if (!res.ok) {
    const body = await res.text();
    let detail = res.statusText;
    try { detail = JSON.parse(body).detail || detail; } catch {}
    // 401：token 无效或过期，清除本地 token，触发重新登录
    if (res.status === 401) {
      sessionStorage.removeItem(SESSION_KEY);
      localStorage.removeItem('baize_session_token');
      _authToken = null;
    }
    throw new Error(`[${res.status}] ${detail}`);
  }
  // 204 No Content: no body to parse
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ===== Health =====
export async function healthCheck(): Promise<HealthResponse> {
  return request('/health');
}

// ===== Agents =====
export async function listAgents(): Promise<AgentsResponse> {
  return request('/agents');
}

// ===== Pipelines =====
export interface PipelineStep {
  agent: string;
  display: string;
  desc: string;
}
export interface PipelineInfo {
  name: string;
  description: string;
  pattern_type: string;
  steps: PipelineStep[];
}
export interface PipelinesResponse {
  pipelines: PipelineInfo[];
}
export async function listPipelines(): Promise<PipelinesResponse> {
  return request('/pipelines');
}

// ===== Custom Pipelines CRUD =====
export interface CustomPipeline {
  id: string;
  name: string;
  description: string;
  steps: Array<{ agent_name: string; display_name: string; description: string }>;
  created_at: string;
  updated_at: string;
  is_custom?: boolean;
}
export interface CustomPipelinesResponse {
  pipelines: CustomPipeline[];
}

export async function listCustomPipelines(): Promise<CustomPipelinesResponse> {
  return request('/pipelines/custom');
}

export async function createCustomPipeline(data: {
  name: string;
  description?: string;
  steps?: Array<{ agent_name: string; display_name: string; description: string }>;
}): Promise<CustomPipeline> {
  return request('/pipelines/custom', { method: 'POST', body: JSON.stringify(data) });
}

export async function updateCustomPipeline(id: string, data: {
  name?: string;
  description?: string;
  steps?: Array<{ agent_name: string; display_name: string; description: string }>;
}): Promise<CustomPipeline> {
  return request(`/pipelines/custom/${id}`, { method: 'PUT', body: JSON.stringify(data) });
}

export async function deleteCustomPipeline(id: string): Promise<{ success: boolean }> {
  return request(`/pipelines/custom/${id}`, { method: 'DELETE' });
}

// ===== Custom Agents CRUD =====
export interface CustomAgent {
  id: string;
  name: string;
  display_name: string;
  description: string;
  instructions: string;
  model: string;
  tools: string[];
  created_at: string;
  updated_at: string;
}
export interface CustomAgentsResponse {
  agents: CustomAgent[];
}

// Available tools (for agent creation)
export interface ToolInfo {
  id: string;
  name: string;
  description: string;
  category: string;
  import_path: string;
}
export interface ToolsResponse {
  tools: ToolInfo[];
}

export async function listAvailableTools(): Promise<ToolsResponse> {
  return request('/tools');
}

export async function listCustomAgents(): Promise<CustomAgentsResponse> {
  return request('/agents/custom');
}

export async function createCustomAgent(data: {
  name: string;
  display_name?: string;
  description?: string;
  instructions?: string;
  model?: string;
  tools?: string[];
}): Promise<CustomAgent> {
  return request('/agents/custom', { method: 'POST', body: JSON.stringify(data) });
}

export async function updateCustomAgent(id: string, data: {
  name?: string;
  display_name?: string;
  description?: string;
  instructions?: string;
  model?: string;
  tools?: string[];
}): Promise<CustomAgent> {
  return request(`/agents/custom/${id}`, { method: 'PUT', body: JSON.stringify(data) });
}

export async function deleteCustomAgent(id: string): Promise<{ success: boolean }> {
  return request(`/agents/custom/${id}`, { method: 'DELETE' });
}

// ===== Models =====
export async function listModels(): Promise<ModelsResponse> {
  return request('/models');
}

// ===== Single Model Config =====
export interface SingleModelConfig {
  base_url: string;
  api_key: string;
  model: string;
  context_max_turns?: number;
  configured: boolean;
}

export async function getModelConfig(): Promise<SingleModelConfig> {
  return request('/model-config');
}

export async function updateModelConfig(data: {
  base_url: string;
  api_key: string;
  model: string;
  context_max_turns?: number;
}): Promise<SingleModelConfig> {
  return request('/model-config', { method: 'PUT', body: JSON.stringify(data) });
}

export async function clearModelConfig(): Promise<{ ok: boolean; configured: boolean }> {
  return request('/model-config', { method: 'DELETE' });
}

// ===== Sessions =====
export async function listSessions(): Promise<SessionsResponse> {
  return request('/sessions');
}

export async function createSession(data: CreateSessionRequest): Promise<SessionSummary> {
  return request('/sessions', { method: 'POST', body: JSON.stringify(data) });
}

export async function getSession(id: string): Promise<SessionDetail> {
  // API returns { session: SessionDetail }, unwrap it
  const raw = await request<{ session: SessionDetail }>(`/sessions/${id}`);
  return raw.session;
}

export async function deleteSession(id: string): Promise<void> {
  await request(`/sessions/${id}`, { method: 'DELETE' });
}

export async function resetSession(id: string): Promise<void> {
  await request(`/sessions/${id}/reset`, { method: 'POST' });
}

export async function switchSessionModel(id: string, model: string): Promise<SessionDetail> {
  // API returns { session: SessionDetail }, unwrap it
  const raw = await request<{ session: SessionDetail }>(`/sessions/${id}/model`, {
    method: 'PATCH',
    body: JSON.stringify({ model }),
  });
  return raw.session;
}

// ===== Inference =====
export async function sendMessage(id: string, data: InferenceRequest): Promise<InferenceResponse> {
  return request(`/sessions/${id}/messages`, { method: 'POST', body: JSON.stringify(data) });
}

export function streamMessage(
  id: string,
  data: InferenceRequest,
  onChunk: (text: string) => void,
  onDone: () => void,
  onError: (err: Error) => void,
  onPrompt?: (prompt: PromptRequest) => void,
  onStep?: (step: ReasoningStep) => void,
): AbortController {
  const controller = new AbortController();

  console.log(`[SSE] Starting stream for session ${id} → ${apiBase}/sessions/${id}/messages/stream`);
  fetch(`${apiBase}/sessions/${id}/messages/stream`, {
    method: 'POST',
    headers: { ...authHeaders(), Accept: 'text/event-stream' },
    body: JSON.stringify(data),
    signal: controller.signal,
  })
    .then(async (res) => {
      console.log(`[SSE] Response status: ${res.status}, content-type: ${res.headers.get('content-type')}`);
      if (!res.ok) throw new Error(`[${res.status}] ${res.statusText}`);
      const reader = res.body?.getReader();
      if (!reader) throw new Error('无响应数据流');

      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        let chunkText = '';
        let currentEvent = '';
        for (const line of lines) {
          if (!line) { currentEvent = ''; continue; }
          // Track SSE event name (sent before data)
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
            continue;
          }
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (data === '[DONE]') {
              console.log('[SSE] Received [DONE], flushing chunkText');
              // CRITICAL: emit any accumulated chunkText before calling onDone
              // otherwise the final event content is lost forever
              if (chunkText) {
                console.log(`[SSE] onChunk(${chunkText.length} chars) [DONE flush]`);
                onChunk(chunkText);
                chunkText = '';
              }
              onDone(); return;
            }
            try {
              const parsed = JSON.parse(data);
              console.log(`[SSE] event=${currentEvent} parsed.type=`, parsed.type || 'final');
              // Web interactive prompts: password, confirmations, etc.
              if (currentEvent === 'user_prompt' && onPrompt) {
                onPrompt(parsed as PromptRequest);
                currentEvent = '';
                continue;
              }
              // Reasoning / thinking-process steps: emitted with event=reasoning_step
              // data.type is the step kind: tool_call | tool_output | handoff | agent_switched | message
              if (currentEvent === 'reasoning_step' && onStep) {
                onStep(parsed as ReasoningStep);
                currentEvent = '';
                continue;
              }
              // Handle tool/runner errors early so user sees them
              if (parsed.type === 'error' && parsed.error) {
                console.warn(`[SSE] Runner error: ${parsed.error}`);
                chunkText += `\n\n⚠️ 运行错误: ${parsed.error}`;
                currentEvent = '';
                continue;
              }
              if (parsed.text || parsed.content) {
                chunkText += parsed.text || parsed.content || '';
              }
              if (parsed.final_output) {
                if (typeof parsed.final_output === 'string') {
                  chunkText += parsed.final_output;
                } else if (Array.isArray(parsed.final_output)) {
                  const texts = parsed.final_output.map((b: any) => b?.text || '').filter(Boolean);
                  if (texts.length) chunkText += texts.join('\n');
                } else if (typeof parsed.final_output === 'object') {
                  // Try common nested fields: .text, .output, .message, .result
                  if (parsed.final_output.text) {
                    chunkText += parsed.final_output.text;
                  } else if (parsed.final_output.output) {
                    chunkText += typeof parsed.final_output.output === 'string'
                      ? parsed.final_output.output
                      : JSON.stringify(parsed.final_output.output);
                  } else if (parsed.final_output.message) {
                    chunkText += typeof parsed.final_output.message === 'string'
                      ? parsed.final_output.message
                      : JSON.stringify(parsed.final_output.message);
                  } else if (parsed.final_output.result) {
                    chunkText += typeof parsed.final_output.result === 'string'
                      ? parsed.final_output.result
                      : JSON.stringify(parsed.final_output.result);
                  } else {
                    // Last resort: stringify the whole object
                    const s = JSON.stringify(parsed.final_output);
                    if (s !== '{}') chunkText += s;
                  }
                }
              }
              // Always also check final_message (it's NOT mutually exclusive with final_output)
              if (!chunkText && parsed.final_message && typeof parsed.final_message === 'string') {
                chunkText += parsed.final_message;
              }
            } catch {
              // Plain text chunk
              console.log(`[SSE] Non-JSON data:`, data.substring(0, 80));
              chunkText += data;
            }
          }
        }
        if (chunkText) {
          console.log(`[SSE] onChunk(${chunkText.length} chars)`);
          onChunk(chunkText);
        }
      }
      console.log('[SSE] Stream ended (reader done), calling onDone');
      onDone();
    })
    .catch((err) => {
      console.error('[SSE] Error:', err.name, err.message);
      if (err.name !== 'AbortError') onError(err);
    });

  return controller;
}

export async function cancelSession(id: string): Promise<CancelTaskResponse> {
  return request(`/sessions/${id}/cancel`, { method: 'POST' });
}

// ===== Prompt (reply to interactive user prompts from agent) =====
export interface PromptRequest {
  prompt_id: string;
  prompt_type: string;   // "sudo_password" | "sensitive_command" | "confirm"
  title: string;
  message: string;
  command: string;
  options: string[];
  is_password: boolean;
}

export async function respondToPrompt(
  sessionId: string,
  promptId: string,
  response: string,
  rejected: boolean = false,
): Promise<void> {
  await request(`/sessions/${sessionId}/prompts/${promptId}/respond`, {
    method: 'POST',
    body: JSON.stringify({ response, rejected: String(rejected) }),
  });
}

// ===== Reasoning step (thinking process stream) =====
export interface ReasoningStep {
  type: string;  // "reasoning" | "tool_call" | "tool_output" | "handoff" | "agent_switched" | "message" | "error" | "pipeline_step" | "pipeline_phase_complete"
  agent?: string;
  tool?: string;
  arguments?: unknown;
  output?: unknown;
  text?: string;
  from_agent?: string;
  to_agent?: string;
  error?: string;
  // Pipeline-specific fields
  phase?: number;
  phase_agent?: string;
  phase_name?: string;
  total?: number;
  message?: string;
}
// ===== UX =====
export async function generateTitle(data: UXTitleLiteRequest): Promise<UXTitleLiteResponse> {
  return request('/ux/title', { method: 'POST', body: JSON.stringify(data) });
}

export async function generateSummary(data: UXSummarizeLiteRequest): Promise<UXSummarizeLiteResponse> {
  return request('/ux/summarize', { method: 'POST', body: JSON.stringify(data) });
}

// ===== Interrupt =====
export async function interruptSession(id: string): Promise<InterruptResponse> {
  return request(`/sessions/${id}/interrupt`, { method: 'POST' });
}

// ===== Auth =====
export async function login(data: AuthLoginRequest): Promise<AuthLoginResponse> {
  const result = await request<AuthLoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(data),
  });
  // 后端返回 token 字段；兼容 session_token 旧字段
  const token = result.token ?? result.session_token;
  if (token) {
    _authToken = token;
    localStorage.setItem('baize_session_token', token);
  }
  return result;
}

export function logout() {
  localStorage.removeItem('baize_session_token');
  sessionStorage.removeItem(SESSION_KEY);
  _authToken = null;
}

// ===== 附件（多模态）=====
export interface AttachmentInfo {
  file_id: string;
  filename: string;
  file_type: string; // image/code/document/archive/other
  mime?: string;
  size?: number;
  uploaded_at?: string;
}

/** 上传附件到会话，返回附件信息 */
export async function uploadAttachment(sessionId: string, file: File): Promise<AttachmentInfo> {
  const form = new FormData();
  form.append('file', file);
  const token = getToken();
  const res = await fetch(`${apiBase}/sessions/${sessionId}/files`, {
    method: 'POST',
    headers: token ? { 'X-Baize-API-Key': token } : {},
    body: form,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body ? `上传失败 [${res.status}]` : `上传失败 [${res.status}]`);
  }
  const data = await res.json();
  return data.attachment;
}

/** 列出会话的全部附件 */
export async function listAttachments(sessionId: string): Promise<AttachmentInfo[]> {
  const data = await request<{ attachments: AttachmentInfo[] }>(`/sessions/${sessionId}/files`);
  return data.attachments || [];
}

/** 删除会话附件 */
export async function deleteAttachment(sessionId: string, fileId: string): Promise<void> {
  await request(`/sessions/${sessionId}/files/${fileId}`, { method: 'DELETE' });
}
