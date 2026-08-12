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

// ===== 模块发现 =====
export interface ModuleInfo {
  installed: boolean;
  version: string;
}

export interface ModulesResponse {
  modules: Record<string, ModuleInfo>;
}

/** 获取已安装的模块列表，前端据此动态显示/隐藏功能。 */
export async function fetchModules(): Promise<ModulesResponse> {
  return request('/modules');
}

// ===== Pipeline 执行（后台 Runner 模型） =====

export interface PipelineTemplate {
  id: string;
  name: string;
  type: string;               // "auto" | "manual"
  description: string;
  category: string;
  tags?: string[];
  triggers?: string[];
  nodes?: Array<{              // 新格式
    id: string;
    type: string;
    display_name: string;
    description: string;
    agent?: string;
    prompt_template?: string;
    branches?: Array<{ when: string; goto: string; label: string; default?: boolean }>;
    parallel_branches?: Array<{ node_id: string }>;
    confirm_prompt?: string;
    confirm_options?: string[];
  }>;
  steps?: Array<{              // 兼容旧格式
    id: string;
    type: string;
    agent: string;
    display_name?: string;
    description?: string;
    prompt_template?: string;
    branches?: Array<{ when: string; goto: string; label?: string; default?: boolean }>;
  }>;
}

export interface PipelineTemplatesResponse {
  templates: PipelineTemplate[];
}

/** 获取预置流水线模板列表 */
export async function listPipelineTemplates(): Promise<PipelineTemplatesResponse> {
  return request('/pipelines/templates');
}

/** 删除内置流水线模板 */
export async function deleteBuiltinTemplate(templateId: string): Promise<{ ok: boolean; template_id: string; message: string }> {
  return request(`/pipelines/templates/${templateId}`, { method: 'DELETE' });
}

/** 恢复所有已删除的内置流水线模板 */
export async function resetBuiltinTemplates(): Promise<{ ok: boolean; restored: number; message: string }> {
  return request('/pipelines/templates/reset', { method: 'POST' });
}

/** 删除内置智能体 */
export async function deleteBuiltinAgent(agentName: string): Promise<{ ok: boolean; agent_name: string; message: string }> {
  return request(`/agents/${agentName}`, { method: 'DELETE' });
}

/** 获取智能体详情（含完整 instructions） */
export async function getAgentDetail(name: string): Promise<{ name: string; id: string; description: string; instructions: string; source: string; type: string; tools: Array<{ name: string; description: string }> }> {
  return request(`/agents/${name}`);
}

/** 恢复所有已删除的内置智能体 */
export async function resetBuiltinAgents(): Promise<{ ok: boolean; restored: number; message: string }> {
  return request('/agents/reset', { method: 'POST' });
}

// ---- 后台执行 Runs API ----

export interface RunCreateRequest {
  pipeline_id: string;
  context: Record<string, unknown>;
  webhook?: string;
}

export interface RunCreateResponse {
  ok: boolean;
  run_id: string;
  pipeline_id: string;
  status: string;
}

export interface RunBrief {
  run_id: string;
  pipeline_id: string;
  pipe_type: string;
  status: string;
  created_at: number;
  error: string;
}

export interface RunListResponse {
  runs: RunBrief[];
  total: number;
}

export interface NodeRecord {
  node_id: string;
  node_type: string;
  status: string;
  input?: unknown;
  output?: string;
  data?: Record<string, unknown>;
  error?: string;
  started_at?: number;
  ended_at?: number;
}

export interface RunEvent {
  event_id: string;
  type: string;
  run_id: string;
  timestamp: number;
  data: {
    node_id?: string;
    node_type?: string;
    [key: string]: unknown;
  };
}

export interface RunDetail {
  run_id: string;
  pipeline_id: string;
  pipe_type: string;
  status: string;
  context: Record<string, unknown>;
  created_at: number;
  started_at: number | null;
  ended_at: number | null;
  error: string;
  nodes: Record<string, NodeRecord>;
  events: RunEvent[];
  events_count: number;
  report: string;
}

/** 提交一次后台执行（立即返回 run_id） */
export async function submitRun(data: RunCreateRequest): Promise<RunCreateResponse> {
  return request('/runs', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/** 列出运行记录 */
export async function listRuns(params?: {
  pipeline_id?: string;
  status?: string;
  limit?: number;
}): Promise<RunListResponse> {
  const qs = new URLSearchParams();
  if (params?.pipeline_id) qs.set('pipeline_id', params.pipeline_id);
  if (params?.status) qs.set('status', params.status);
  if (params?.limit) qs.set('limit', String(params.limit));
  const query = qs.toString() ? `?${qs.toString()}` : '';
  return request(`/runs${query}`);
}

/** 查询单次执行详情 */
export async function getRun(runId: string): Promise<{ ok: boolean; run: RunDetail }> {
  return request(`/runs/${runId}`);
}

/** SSE 实时事件流（支持重连补齐） */
export function streamRunEvents(
  runId: string,
  lastEventId: string,
  onEvent: (event: RunEvent) => void,
  onDone: () => void,
  onError: (err: Error) => void,
): AbortController {
  const controller = new AbortController();
  const params = lastEventId ? `?last_event_id=${encodeURIComponent(lastEventId)}` : '';

  fetch(`${apiBase}/runs/${runId}/stream${params}`, {
    method: 'GET',
    headers: { ...authHeaders(), Accept: 'text/event-stream' },
    signal: controller.signal,
  })
    .then(async (res) => {
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
        for (const line of lines) {
          if (!line.trim()) continue;
          const dataMatch = line.match(/^data:\s*(.+)/);
          if (dataMatch) {
            const raw = dataMatch[1].trim();
            if (raw === '[DONE]') { onDone(); return; }
            try {
              onEvent(JSON.parse(raw) as RunEvent);
            } catch { /* skip malformed */ }
          }
        }
      }
      onDone();
    })
    .catch((err) => {
      if (err.name !== 'AbortError') onError(err);
    });
  return controller;
}

/** 人工确认恢复执行 */
export async function confirmRun(runId: string, action: string): Promise<{ ok: boolean; status: string; message: string }> {
  return request(`/runs/${runId}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  });
}

// ---- 自动化流水线激活控制 ----

export interface PipelineActivationStatus {
  pipeline_id: string;
  active: boolean;
}

/** 开启自动化流水线 */
export async function activatePipeline(pipelineId: string): Promise<{ ok: boolean; pipeline_id: string; active: boolean }> {
  return request(`/pipelines/${pipelineId}/activate`, { method: 'POST' });
}

/** 关闭自动化流水线 */
export async function deactivatePipeline(pipelineId: string): Promise<{ ok: boolean; pipeline_id: string; active: boolean }> {
  return request(`/pipelines/${pipelineId}/deactivate`, { method: 'POST' });
}

/** 获取自动化流水线激活状态 */
export async function getPipelineStatus(pipelineId: string): Promise<PipelineActivationStatus> {
  return request(`/pipelines/${pipelineId}/status`);
}

// ---- 人工介入流水线（供对话选择） ----

export interface ManualPipelineBrief {
  id: string;
  name: string;
  description: string;
  category: string;
  tags?: string[];
  nodes_count: number;
  node_types: string[];
}

export interface ManualPipelinesResponse {
  pipelines: ManualPipelineBrief[];
  total: number;
}

/** 列出人工介入流水线，供对话中选择 */
export async function listManualPipelines(): Promise<ManualPipelinesResponse> {
  return request('/pipelines/manual');
}

// ===== Receivers 数据接收器 =====
export interface ReceiverConfig {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  pipeline_id: string;
  webhook_path: string;
  syslog_port: number;
  syslog_host: string;
  syslog_protocol: string;
  watch_dir: string;
  watch_patterns: string;
  watch_recursive: boolean;
  total_received: number;
  last_received_at: number | null;
  queue_size: number;
  created_at: number;
  updated_at: number;
}
export interface ReceiversResponse { receivers: ReceiverConfig[]; }
export interface ReceiverResponse { receiver: ReceiverConfig; }

export async function listReceivers(): Promise<ReceiversResponse> {
  return request('/receivers');
}
export async function getReceiver(id: string): Promise<ReceiverResponse> {
  return request(`/receivers/${id}`);
}
export async function createReceiver(data: {
  name: string;
  kind: string;
  pipeline_id?: string;
  webhook_path?: string;
  syslog_port?: number;
  syslog_host?: string;
  syslog_protocol?: string;
  watch_dir?: string;
  watch_patterns?: string;
  watch_recursive?: boolean;
}): Promise<ReceiverResponse> {
  return request('/receivers', { method: 'POST', body: JSON.stringify(data) });
}
export async function updateReceiver(id: string, data: Record<string, any>): Promise<ReceiverResponse> {
  return request(`/receivers/${id}`, { method: 'PUT', body: JSON.stringify(data) });
}
export async function deleteReceiver(id: string): Promise<{ status: string }> {
  return request(`/receivers/${id}`, { method: 'DELETE' });
}
