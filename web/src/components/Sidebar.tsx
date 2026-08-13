import { useNavigate, useLocation } from 'react-router-dom';
import { useApp } from '../context/AppContext';
import { healthCheck } from '../api/client';
import type { ViewPage } from '../types';
import type { JSX } from 'react';
import { useEffect } from 'react';

const NAV_ITEMS: Array<{ id: ViewPage; label: string; icon: string; path: string }> = [
  { id: 'dashboard', label: '控制台', path: '/dashboard', icon: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6' },
  { id: 'chat', label: '对话渗透', path: '/chat', icon: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z' },
  { id: 'orchestration', label: '流水线', path: '/orchestration', icon: 'M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2zM7 11h10M12 6v12' },
  { id: 'agents', label: '智能体', path: '/agents', icon: 'M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z' },
  { id: 'sessions', label: '会话管理', path: '/sessions', icon: 'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10' },
  { id: 'guardrails', label: '安全护栏', path: '/guardrails', icon: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4' },
  { id: 'experiences', label: '经验库', path: '/experiences', icon: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253' },
  { id: 'settings', label: '设置', path: '/settings', icon: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z' },
];

export function Sidebar(): JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const {
    serverConnected, setServerConnected,
    serverVersion, setServerVersion,
    setCurrentView,
    installedModules,
  } = useApp();

  // 根据已安装模块过滤导航项
  const visibleNavItems = NAV_ITEMS.filter(item => {
    if (item.id === 'orchestration') {
      return installedModules?.orchestration?.installed === true;
    }
    return true;
  });

  // Sync currentView from URL for backward compatibility
  useEffect(() => {
    const path = location.pathname.replace(/^\/|\/$/g, '') || 'dashboard';
    const viewId = path as ViewPage;
    setCurrentView(viewId);
  }, [location.pathname, setCurrentView]);

  useEffect(() => {
    let cancelled = false;
    function check() {
      healthCheck()
        .then(r => {
          if (!cancelled) {
            setServerConnected(true);
            setServerVersion(r.version);
          }
        })
        .catch(() => {
          if (!cancelled) setServerConnected(false);
        });
    }
    check();
    const interval = setInterval(check, 15000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [setServerConnected, setServerVersion]);

  // Determine active route
  const currentPath = location.pathname.replace(/\/$/, '') || '/dashboard';

  return (
    <aside className="w-56 min-w-[224px] bg-gray-900 border-r border-gray-800 flex flex-col select-none">
      {/* Logo */}
      <div className="px-5 py-4 border-b border-gray-800 flex items-center gap-3">
        <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-sm font-bold">C</div>
        <div>
          <div className="text-sm font-semibold tracking-tight">白泽</div>
          <div className="text-[10px] text-gray-500">AI 安全助手</div>
        </div>
      </div>

      {/* Server Status */}
      <div className="px-5 py-3 border-b border-gray-800 flex items-center gap-2 text-xs">
        <span className={`w-2 h-2 rounded-full ${serverConnected ? 'bg-emerald-400 shadow-[0_0_6px] shadow-emerald-400/50' : 'bg-red-400'}`} />
        <span className={serverConnected ? 'text-emerald-400' : 'text-red-400'}>
          {serverConnected ? `已连接 · v${serverVersion}` : '未连接'}
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
        {visibleNavItems.map(item => {
          const isActive = currentPath === item.path;
          return (
            <button
              key={item.id}
              onClick={() => navigate(item.path)}
              aria-label={item.label}
              aria-current={isActive ? 'page' : undefined}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 group ${
                isActive
                  ? 'bg-blue-600/10 text-blue-400 border border-blue-600/20'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/60 border border-transparent'
              }`}
            >
              <svg className="w-4.5 h-4.5 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d={item.icon} />
              </svg>
              <span>{item.label}</span>
              {isActive && (
                <div className="ml-auto w-1 h-4 bg-blue-500 rounded-full" />
              )}
            </button>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-gray-800 text-[10px] text-gray-600 text-center">
        白泽 v{serverVersion || '—'} · 
      </div>
    </aside>
  );
}
