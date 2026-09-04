import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { ToastContainer } from './Toast';
import { useApp } from '../context/AppContext';
import { useTheme } from '../context/ThemeContext';
import { SettingsModal } from './SettingsModal';
import { Chat } from '../pages/Chat';
import type { JSX } from 'react';

export function Layout(): JSX.Element {
  const { toasts } = useApp();
  const { toggleTheme, isDark } = useTheme();
  const location = useLocation();
  // Chat 常驻挂载：切到其他页面时只隐藏（display:none）而不卸载。
  // 若随路由卸载，会 (1) 中断进行中的 SSE 流式连接；(2) 切回时重新挂载，
  // 其 mount useEffect 会用后端历史覆盖 AppContext 中的消息，而流式过程中
  // 的内容尚未落库，进行中的对话就"消失"了，必须等结束后刷新才看得到。
  const isChat = location.pathname.startsWith('/chat');

  return (
    <div className={`flex h-screen overflow-hidden ${isDark ? 'bg-gray-950 text-gray-100' : 'bg-gray-50 text-gray-900'}`}>
      {/* Sidebar */}
      <Sidebar />

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Theme Toggle Bar */}
        <div className={`flex justify-end px-4 py-1 border-b ${isDark ? 'border-gray-800' : 'border-gray-200'}`}>
          <button
            onClick={toggleTheme}
            aria-label={isDark ? '切换到亮色模式' : '切换到暗色模式'}
            className={`p-1.5 rounded-lg transition-colors ${isDark ? 'hover:bg-gray-800 text-gray-400' : 'hover:bg-gray-200 text-gray-600'}`}
          >
            {isDark ? (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
              </svg>
            ) : (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
              </svg>
            )}
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          {/* Chat 常驻挂载，非聊天页时隐藏以保持流式状态 */}
          <div className={`h-full ${isChat ? 'block' : 'hidden'}`}>
            <Chat />
          </div>
          {!isChat && <Outlet />}
        </div>
      </div>

      {/* Settings Modal */}
      <SettingsModal />

      {/* Toast Notifications */}
      <ToastContainer toasts={toasts} />
    </div>
  );
}
