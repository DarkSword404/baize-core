import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { ToastContainer } from './Toast';
import { useApp } from '../context/AppContext';
import { useTheme } from '../context/ThemeContext';
import { SettingsModal } from './SettingsModal';
import type { JSX } from 'react';

export function Layout(): JSX.Element {
  const { toasts } = useApp();
  const { toggleTheme, isDark } = useTheme();

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
          <Outlet />
        </div>
      </div>

      {/* Settings Modal */}
      <SettingsModal />

      {/* Toast Notifications */}
      <ToastContainer toasts={toasts} />
    </div>
  );
}
