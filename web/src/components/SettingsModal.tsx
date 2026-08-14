import { useState } from 'react';
import { useApp } from '../context/AppContext';
import { setApiBase, getApiBase, healthCheck } from '../api/client';
import type { JSX } from 'react';

export function SettingsModal(): JSX.Element | null {
  const { settingsOpen, setSettingsOpen, addToast, setServerConnected, setServerVersion } = useApp();
  const [url, setUrl] = useState(getApiBase());
  const [testing, setTesting] = useState(false);

  if (!settingsOpen) return null;

  async function handleSave() {
    setApiBase(url);
    setTesting(true);
    try {
      const r = await healthCheck();
      setServerConnected(true);
      setServerVersion(r.version);
      addToast({ type: 'success', title: '已连接', message: `白泽·智脑 v${r.version}` });
      setSettingsOpen(false);
    } catch (err: any) {
      setServerConnected(false);
      addToast({ type: 'error', title: '连接失败', message: err.message });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setSettingsOpen(false)} />
      <div className="relative bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-md p-6 shadow-2xl animate-slide-up">
        <h2 className="text-lg font-semibold mb-4">设置</h2>

        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">API 地址</label>
            <input
              type="text"
              value={url}
              onChange={e => setUrl(e.target.value)}
              placeholder="http://localhost:9999/api/v1"
              className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 focus:border-blue-500 outline-none font-mono"
            />
            <p className="text-[10px] text-gray-600 mt-1.5">默认 Vite 开发代理自动转发 /api → localhost:8001</p>
          </div>

          <button
            onClick={handleSave}
            disabled={testing}
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 rounded-xl text-sm font-semibold transition-all active:scale-[0.98] cursor-pointer"
          >
            {testing ? '测试连接...' : '保存并测试连接'}
          </button>
        </div>
      </div>
    </div>
  );
}
