/*/* ===== 白泽·智脑(Baize) API TypeScript Types ===== */

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
  browser_collab?: boolean;
  scope?: string;
  goal?: string;
}

export interface SessionDetail extends SessionSummary {
  history: Array<Record<string, unknown>>;
  blackboard?: BlackboardSnapshot | null;
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
  browser_collab?: boolean;
  // 协作模式（黑板驱动）：目标范围 + 成功条件
  scope?: string;
  goal?: string;
}

// ---- 黑板（协作模式攻击图）----
export interface BlackboardNode {
  id: string;
  kind: 'origin' | 'fact' | 'intent' | 'goal' | 'hint';
  label: string;
  detail: string;
  status: string;
  discovered_by: string;
  created_at: string;
  updated_at: string;
  properties: Record<string, unknown>;
}

export interface BlackboardEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  created_at: string;
  properties: Record<string, unknown>;
}

export interface BlackboardSnapshot {
  session_id: string;
  scope: string;
  goal: string;
  version: number;
  nodes: BlackboardNode[];
  edges: BlackboardEdge[];
  stats: {
    facts: number;
    intents_pending: number;
    intents_done: number;
    hints: number;
  };
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

export type ViewPage = 'dashboard' | 'chat' | 'agents' | 'tools' | 'sessions' | 'guardrails' | 'settings' | 'orchestration' | 'experiences' | 'browser';

/** 共享协作浏览器状态 */
export interface SharedBrowserStatus {
  running: boolean;
  headless: boolean;
  url: string;
  profile: string;
  confirm_pending: boolean;
  /** 固定视口尺寸（截图坐标映射基准） */
  viewport?: { width: number; height: number };
}

// ===== 长期记忆：memory 子系统（Episode / 经验 / 语义事实 / 知识图谱）=====
export type MemoryExperienceStatus = 'draft' | 'active' | 'superseded' | 'invalidated';
export type MemoryExperienceKind = 'method' | 'lesson' | 'intel';

/** 证据引用：定位到 Episode 内具体步骤（ref 如 "t:3"），可回溯原始轨迹 */
export interface MemoryEvidenceRef {
  episode_id: string;
  kind: string;
  ref: string;
  excerpt: string;
  at: string;
}

export interface MemoryExperience {
  id: string;
  title: string;
  content: string;         // 自然语言经验（核心存储单位）
  tags: string[];
  kind: MemoryExperienceKind;
  status: MemoryExperienceStatus;
  confidence: number;      // 0-1，自动沉淀置信度（≥0.7 才 active）
  importance: number;      // 0-5
  scope: string;           // "global" | "agent:<key>"
  agent_key: string;
  source_session_id: string;
  episode_id: string;
  evidence: MemoryEvidenceRef[];
  supersedes: string[];    // 本条目取代的历史 id（谱系）
  replaced_by: string;     // 本条目被谁取代（'' 表示仍有效）
  history: Array<{ action: string; at: string; actor: string; [k: string]: unknown }>;
  created_at: string;
  updated_at: string;
  source: string;
  /** 评价闭环：被注入上下文次数 / 被采纳次数 / 被判无用次数 */
  hit_count?: number;
  useful_count?: number;
  noise_count?: number;
  last_hit_at?: string;
  /** 检索命中的附加得分 */
  score?: number;
}

export interface MemoryExperiencesResponse {
  total: number;
  experiences: MemoryExperience[];
}

export type MemoryEpisodeStatus = 'success' | 'failed' | 'in_progress';

export interface MemoryEpisodeBrief {
  id: string;
  task: string;            // 用户给 Agent 的任务（一次任务 = 一个 Episode）
  target: string;
  agent_key: string;
  session_id: string;
  status: MemoryEpisodeStatus | string;
  start_at: string;
  end_at?: string;
  error?: string;
  result_text?: string;
  summary?: string;
  steps: number;           // 列表接口：步数（完整内容请取详情）
  entity_keys: string[];
  messages_count: number;
  created_at?: string;
  updated_at?: string;
}

export interface MemoryEpisodeStep {
  no: number;
  type: 'decision' | 'observation' | 'tool_call';
  role?: string;
  text?: string;           // decision / observation 内容
  name?: string;           // tool_call 工具名
  arguments?: string;      // tool_call 参数（原始文本）
  output?: string;         // tool_call 返回（可能被截断）
  status?: string;         // ok | error | denied
  ts?: string;
}

export interface MemoryEpisodeDetail extends Omit<MemoryEpisodeBrief, 'steps'> {
  steps: MemoryEpisodeStep[];
}

export type MemoryFactStatus = 'active' | 'invalidated';

export interface MemoryFact {
  id: string;
  statement: string;
  subject: string;
  rel_type: string;
  object: string;
  status: MemoryFactStatus;
  confidence: number;
  valid_from?: string;
  invalid_at?: string;
  sources?: Array<{ kind?: string; id: string }>;
  agent_key?: string;
  created_at?: string;
  updated_at?: string;
}

export interface MemoryFactsResponse {
  total: number;
  facts: MemoryFact[];
}

export interface MemoryEntity {
  key: string;
  label: string;
  kind: string;
  aliases?: string[];
  first_seen?: string;
  last_seen?: string;
  mention_count?: number;
  properties?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface MemoryEntitiesResponse {
  total: number;
  entities: MemoryEntity[];
}

export interface MemoryRelation {
  id: string;
  type: string;            // MENTIONS / SUPPORTS / BEFORE / ...
  source: string;
  target: string;
  status?: string;
  valid_from?: string;
  invalid_at?: string;
  provenance?: string;
  occurrences?: number;
  [k: string]: unknown;
}

export interface MemoryStats {
  episodes: number;
  experiences: number;
  experiences_active: number;
  experiences_draft: number;
  facts: number;
  entities: number;
  relations: number;
}

export interface MemoryGraphNode {
  id: string;
  label: string;
  kind: 'entity' | 'experience' | 'episode' | 'fact';
  group?: string;          // entity 类别（ip/domain/port/...）
  status?: string;
  confidence?: number;
  tags?: string[];
  mentions?: number;
  [k: string]: unknown;
}

export interface MemoryGraphEdge {
  source: string;
  target: string;
  type: string;
  valid_from?: string;
  invalid_at?: string;
}

export interface MemoryGraphSnapshot {
  stats: MemoryStats;
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
}

export interface MemorySearchResult {
  query: string;
  experiences: MemoryExperience[];
  facts: MemoryFact[];
  entities: MemoryEntity[];
  neighbors: { entities: MemoryEntity[]; relations: MemoryRelation[] };
  episodes: MemoryEpisodeBrief[];
}

// ---- 请求载荷 --------------------------------------------------------------
export interface MemoryExperienceCreateInput {
  title: string;
  content: string;
  tags?: string[];
  kind?: MemoryExperienceKind;
  scope?: string;
  agent_key?: string;
  source_session_id?: string;
  importance?: number;
}

export interface MemoryExperienceUpdateInput {
  title?: string;
  content?: string;
  tags?: string[];
  kind?: MemoryExperienceKind;
  importance?: number;
  note?: string;
}

export interface MemoryExperienceStatusInput {
  status: MemoryExperienceStatus;
  note?: string;
}

export interface MemoryFeedbackInput {
  useful: boolean;   // true=有用（采纳），false=无用
  note?: string;
}

// ===== 安全护栏 =====
export interface SSRFGuardrailSettings {
  enabled: boolean;
  block_private: boolean;
  block_loopback: boolean;
  block_link_local: boolean;
  block_reserved: boolean;
  block_multicast: boolean;
  block_unspecified: boolean;
  allowlist_cidrs: string[];
  allowlist_hosts: string[];
}

export interface GuardrailSettings {
  input_enabled: boolean;
  output_enabled: boolean;
  max_input_length: number;
  ssrf: SSRFGuardrailSettings;
}

export interface GuardrailRule {
  id: string;
  name: string;
  category: string;
  description: string;
  severity: string; // low | medium | high
  kind: string;     // regex
  pattern: string;
  enabled: boolean;
}

export interface GuardrailConfig {
  settings: GuardrailSettings;
  rules: GuardrailRule[];
}

export interface GuardrailTestResult {
  blocked: boolean;
  message: string;
  rule_id: string | null;
}

// ===== 沙箱策略配置 =====
export interface SandboxPolicyConfig {
  enabled: boolean;
  default_permission: string;   // allow | approve | deny
  tool_permissions: Record<string, string>;  // tool_name -> allow | approve | deny
  auto_approve_after: number;
  max_dangerous_per_turn: number;
}


