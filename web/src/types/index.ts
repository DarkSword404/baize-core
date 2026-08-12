/*/* ===== 白泽(Baize) API TypeScript Types ===== */

export interface HealthResponse {
  status: string;
  version: string;
}

export interface AgentTool {
  name: string;
  description: string | null;
}

export interface AgentMetadata {
  id?: string;
  name: string;
  description: string | null;
  type: 'agent' | 'pattern';
  pattern_type: string | null;
  tools: AgentTool[];
  is_custom?: boolean;
  source?: 'builtin' | 'custom';
  instructions?: string;
}

export interface AgentsResponse {
  agents: AgentMetadata[];
}

export interface ModelPricing {
  input_cost_per_token: number | null;
  output_cost_per_token: number | null;
  max_tokens: number | null;
  max_input_tokens: number | null;
  max_output_tokens: number | null;
  supports_function_calling: boolean | null;
  supports_vision: boolean | null;
  supports_response_schema: boolean | null;
  supports_tool_choice: boolean | null;
}

export interface ModelInfo {
  name: string;
  provider: string | null;
  category: string | null;
  description: string | null;
  input_cost: number | null;
  output_cost: number | null;
  pricing: ModelPricing | null;
}

export interface ModelsResponse {
  models: ModelInfo[];
}

export type ToolPermission = 'auto' | 'allow' | 'deny';

export interface SessionSummary {
  id: string;
  agent: string;
  model: string;
  stateful: boolean;
  created_at: string;
  updated_at: string;
  history_length: number;
  metadata: Record<string, unknown>;
  pattern?: string | null;
  agent_stack?: string[];
  agent_transitions?: Array<{from_agent: string | null; to_agent: string; timestamp: string; reason: string}>;
}

export interface SessionDetail extends SessionSummary {
  history: Array<Record<string, unknown>>;
}

export interface SessionsResponse {
  sessions: SessionSummary[];
}

export interface SessionHistoryResponse {
  session: SessionDetail;
}

export interface CreateSessionRequest {
  agent?: string | null;
  model?: string | null;
  stateful?: boolean;
  metadata?: Record<string, unknown> | null;
  pattern?: string | null;
}

export interface RunResultPayload {
  messages: Array<Record<string, unknown>>;
  history: Array<Record<string, unknown>>;
  final_output: unknown;
  text_output: string | null;
  input_guardrails: Array<Record<string, unknown>>;
  output_guardrails: Array<Record<string, unknown>>;
}

export interface InferenceRequest {
  input: string | Array<Record<string, unknown>>;
  context?: Record<string, unknown> | null;
  max_turns?: number | null;
  mcp_sse?: Array<{ url: string; name?: string; headers?: Record<string, string>; timeout?: number; sse_read_timeout?: number }> | null;
  /** 本次消息附带的附件 file_id 列表 */
  attachments?: string[];
}

export interface InferenceResponse {
  session: SessionSummary;
  result: RunResultPayload;
}


export interface UXSummarizeLiteRequest {
  messages?: Array<Record<string, unknown>>;
  steps?: Array<Record<string, unknown>>;
  max_len?: number;
}

export interface UXSummarizeLiteResponse {
  summary_text: string;
}

export interface UXTitleLiteRequest {
  messages?: Array<Record<string, unknown>>;
  title_hint?: string | null;
}

export interface UXTitleLiteResponse {
  title: string;
}

export interface CancelTaskResponse {
  cancelled: boolean;
  message: string;
}

export interface AuthLoginRequest {
  username: string;
  password: string;
  ip?: string | null;
}

export interface AuthLoginResponse {
  session_token?: string;
  token?: string;
  ok?: boolean;
}

export interface AuthRegisterRequest {
  username: string;
  password: string;
}

export interface InterruptResponse {
  interrupted: boolean;
}

// ===== UI-level types =====
export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

/** 中间产物类型，对应 SDK 中 tool_call / tool_output / reasoning / handoff 等 */
export type IntermediateItemType = 'function_call' | 'function_call_output' | 'reasoning' | 'handoff';

/** 中间产物的结构化数据，前端用折叠区块渲染 */
export interface IntermediateData {
  itemType: IntermediateItemType;
  label: string;     // 折叠时显示摘要标题，如 "bash cat /flag"
  detail: string;    // 展开时显示完整内容
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
  isStreaming?: boolean;
  intermediates?: IntermediateData[];
  /** 该消息关联的附件文件名列表（多模态） */
  attachments?: string[];
}

export interface Toast {
  id: string;
  type: 'success' | 'error' | 'info' | 'warning';
  title: string;
  message?: string;
}

export type ViewPage = 'dashboard' | 'chat' | 'agents' | 'sessions' | 'settings' | 'orchestration';
