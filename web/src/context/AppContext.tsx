import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import type { ViewPage, Toast, ToolPermission, AgentMetadata, ChatMessage } from '../types';
import type { SessionInfo } from '../api/client';

// ============================================
// 本地持久化工具
// ============================================
function loadPersistedState(key: string, fallback: any = null) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch { return fallback; }
}
function persistState(key: string, value: any) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* quota */ }
}

// ============================================
// AppState
// ============================================
interface AppState {
  // 服务器连接
  serverConnected: boolean;
  setServerConnected: (v: boolean) => void;
  serverVersion: string;
  setServerVersion: (v: string) => void;

  // 导航（从 URL 路由同步）
  currentView: ViewPage;
  setCurrentView: (v: ViewPage) => void;

  // 全局 UI
  toasts: Toast[];
  addToast: (t: Omit<Toast, 'id'>) => void;
  removeToast: (id: string) => void;

  // 会话消息（当前活动会话）
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  isStreaming: boolean;
  setIsStreaming: (v: boolean) => void;

  // 智能体列表
  agents: AgentMetadata[];
  setAgents: (agents: AgentMetadata[]) => void;

  // 设置弹窗
  settingsOpen: boolean;
  setSettingsOpen: (v: boolean) => void;

  // Session（持久化）
  sessions: SessionInfo[];
  setSessions: (sessions: SessionInfo[]) => void;
  addSession: (s: SessionInfo) => void;
  removeSession: (id: string) => void;
  activeSessionId: string | null;
  setActiveSessionId: (id: string | null) => void;
  updateSession: (id: string, updates: Partial<SessionInfo>) => void;

  // Tool Permissions
  toolPermissions: Map<string, ToolPermission>;
  setToolPermission: (tool: string, perm: ToolPermission) => void;
  setToolPermissions: (permissions: Map<string, ToolPermission>) => void;

  // API Key
  apiKey: string;
  setApiKey: (k: string) => void;
}

const AppContext = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  // ─ Server ──────────────────────────────────────────
  const [serverConnected, setServerConnected] = useState(false);
  const [serverVersion, setServerVersion] = useState('—');

  // ─ Navigation (URL-driven, legacy compatibility) ─
  const [currentView, setCurrentView] = useState<ViewPage>('dashboard');

  // ─ Messages (per active chat session) ─
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);

  // ─ Agents ─
  const [agents, setAgents] = useState<AgentMetadata[]>([]);

  // ─ Settings modal ─
  const [settingsOpen, setSettingsOpen] = useState(false);

  // ─ Toasts ──────────────────────────────────────────
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastRef = useRef(toasts);
  toastRef.current = toasts;
  const addToast = useCallback((t: Omit<Toast, 'id'>) => {
    const id = crypto.randomUUID();
    const toast: Toast = { ...t, id };
    setToasts(prev => [...prev.slice(-9), toast]);
    setTimeout(() => {
      setToasts(prev => prev.filter(x => x.id !== id));
    }, 5000);
  }, []);
  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  // ─ Sessions (persisted to localStorage) ───────────
  const [sessions, setSessionsState] = useState<SessionInfo[]>(() =>
    loadPersistedState('baize-sessions', []),
  );
  const [activeSessionId, setActiveSessionIdState] = useState<string | null>(() =>
    loadPersistedState('baize-active-session', null),
  );

  useEffect(() => { persistState('baize-sessions', sessions); }, [sessions]);
  useEffect(() => { persistState('baize-active-session', activeSessionId); }, [activeSessionId]);

  const addSession = useCallback((s: SessionInfo) => {
    setSessionsState(prev => [s, ...prev.filter(x => x.id !== s.id)]);
  }, []);
  const setSessions = useCallback((newSessions: SessionInfo[]) => {
    setSessionsState(newSessions);
  }, []);
  const removeSession = useCallback((id: string) => {
    setSessionsState(prev => prev.filter(s => s.id !== id));
    setActiveSessionIdState(prev => prev === id ? null : prev);
  }, []);
  const setActiveSessionId = useCallback((id: string | null) => {
    setActiveSessionIdState(id);
  }, []);
  const updateSession = useCallback((id: string, updates: Partial<SessionInfo>) => {
    setSessionsState(prev => prev.map(s => s.id === id ? { ...s, ...updates } : s));
  }, []);

  // ─ Tool Permissions ─────────────────────────────────
  const [toolPermissions, setToolPermissionsState] = useState<Map<string, ToolPermission>>(() => {
    try {
      const raw = loadPersistedState('baize-tool-perms', []);
      return new Map(raw);
    } catch { return new Map(); }
  });

  useEffect(() => {
    persistState('baize-tool-perms', [...toolPermissions.entries()]);
  }, [toolPermissions]);

  const setToolPermission = useCallback((tool: string, perm: ToolPermission) => {
    setToolPermissionsState(prev => {
      const next = new Map(prev);
      next.set(tool, perm);
      return next;
    });
  }, []);
  const setToolPermissions = useCallback((permissions: Map<string, ToolPermission>) => {
    setToolPermissionsState(new Map(permissions));
  }, []);

  // ─ API Key (persisted) ────────────────────────────
  const [apiKey, setApiKeyState] = useState<string>(() =>
    loadPersistedState('baize-api-key', ''),
  );
  useEffect(() => { persistState('baize-api-key', apiKey); }, [apiKey]);
  const setApiKey = useCallback((k: string) => { setApiKeyState(k); }, []);

  return (
    <AppContext.Provider
      value={{
        serverConnected, setServerConnected,
        serverVersion, setServerVersion,
        currentView, setCurrentView,
        messages, setMessages, isStreaming, setIsStreaming,
        agents, setAgents,
        settingsOpen, setSettingsOpen,
        toasts, addToast, removeToast,
        sessions, setSessions, addSession, removeSession,
        activeSessionId, setActiveSessionId,
        updateSession,
        toolPermissions, setToolPermission, setToolPermissions,
        apiKey, setApiKey,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp 必须在 AppProvider 内部使用');
  return ctx;
}
