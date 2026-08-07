import { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import {
  setApiBase, getApiBase, getAuthToken, healthCheck, logout,
  getModelConfig, updateModelConfig, clearModelConfig,
} from '../api/client';
import type { JSX } from 'react';

export function Settings(): JSX.Element {
  const { serverConnected, setServerConnected, serverVersion, setServerVersion, addToast } = useApp();
  const [apiUrl, setApiUrl] = useState(getApiBase());
  const [testing, setTesting] = useState(false);
  const [authToken, setAuthToken] = useState<string | null>(null);

  // Single model config
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [modelName, setModelName] = useState('');
  const [contextMaxTurns, setContextMaxTurns] = useState<number>(0);
  const [configLoading, setConfigLoading] = useState(true);
  const [configSaving, setConfigSaving] = useState(false);
  const [configured, setConfigured] = useState(false);

  useEffect(() => {
    setAuthToken(getAuthToken());
    getModelConfig()
      .then(cfg => {
        if (cfg.configured) {
          setBaseUrl(cfg.base_url);
          setApiKey(cfg.api_key);
          setModelName(cfg.model);
          setContextMaxTurns(cfg.context_max_turns ?? 0);
          setConfigured(true);
        }
      })
      .catch(() => {})
      .finally(() => setConfigLoading(false));
  }, []);

  async function handleSaveModelConfig() {
    if (!baseUrl.trim() || !modelName.trim()) {
      addToast({ type: 'warning', title: '请填写 Base URL 和 Model' });
      return;
    }
    setConfigSaving(true);
    try {
      const cfg = await updateModelConfig({
        base_url: baseUrl,
        api_key: apiKey,
        model: modelName,
        context_max_turns: contextMaxTurns,
      });
      setConfigured(cfg.configured);
      setContextMaxTurns(cfg.context_max_turns ?? 0);
      addToast({ type: 'success', title: '已保存', message: `模型已配置为 ${cfg.model}` });
    } catch (err: any) {
      addToast({ type: 'error', title: '保存失败', message: err.message });
    } finally {
      setConfigSaving(false);
    }
  }

  async function handleClearModelConfig() {
    setConfigSaving(true);
    try {
      await clearModelConfig();
      setConfigured(false);
      setBaseUrl('');
      setApiKey('');
      setModelName('');
      setContextMaxTurns(0);
      addToast({ type: 'info', title: '已清除', message: '模型配置已重置' });
    } catch (err: any) {
      addToast({ type: 'error', title: '清除失败', message: err.message });
    } finally {
      setConfigSaving(false);
    }
  }

  async function handleTestConnection() {
    setTesting(true);
    setApiBase(apiUrl);
    try {
      const r = await healthCheck();
      setServerConnected(true);
      setServerVersion(r.version);
      addToast({ type: 'success', title: '连接成功', message: `白泽 v${r.version}` });
    } catch (err: any) {
      setServerConnected(false);
      addToast({ type: 'error', title: '连接失败', message: err.message });
    } finally {
      setTesting(false);
    }
  }

  function handleLogout() {
    logout();
    window.location.reload();  // 强制回到登录页
  }

  const inputCls = "w-full px-4 py-2.5 bg-gray-800 border border-gray-700 rounded-xl text-sm text-gray-200 focus:border-blue-500 outline-none";

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8 max-w-3xl mx-auto">
      <div className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight">系统设置</h1>
        <p className="text-sm text-gray-500 mt-1">配置 API 连接、模型和认证</p>
      </div>

      {/* API Connection */}
      <section className="bg-gray-900 border border-gray-800 rounded-2xl p-6 mb-6">
        <h2 className="text-sm font-semibold mb-4 flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${serverConnected ? 'bg-emerald-400' : 'bg-red-400'}`} />
          API 连接
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          当前状态：
          <span className={serverConnected ? 'text-emerald-400 ml-1' : 'text-red-400 ml-1'}>
            {serverConnected ? `已连接 (v${serverVersion})` : '未连接'}
          </span>
        </p>

        <div className="flex gap-3">
          <input
            type="text"
            value={apiUrl}
            onChange={e => setApiUrl(e.target.value)}
            placeholder="http://localhost:9999/api/v1"
            className={`${inputCls} flex-1 font-mono`}
          />
          <button
            onClick={handleTestConnection}
            disabled={testing}
            className="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 rounded-xl text-sm font-medium transition-all"
          >
            {testing ? '测试中...' : '测试连接'}
          </button>
        </div>
        <p className="text-[10px] text-gray-600 mt-2">
          开发模式下默认使用 Vite 代理: /api → localhost:8001
        </p>
      </section>

      {/* Single Model Configuration */}
      <section className="bg-gray-900 border border-gray-800 rounded-2xl p-6 mb-6">
        <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${configured ? 'bg-emerald-400' : 'bg-gray-500'}`} />
          模型配置
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          本项目使用单模型模式。配置 Base URL、API Key 和模型名后，所有对话与智能体调用都会使用该配置，
          不再需要选择多个模型。
        </p>

        {configLoading ? (
          <p className="text-xs text-gray-500">加载中...</p>
        ) : (
          <div className="space-y-4">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Base URL（OpenAI 兼容端点）</label>
              <input
                type="text"
                value={baseUrl}
                onChange={e => setBaseUrl(e.target.value)}
                placeholder="https://api.example.com/v1"
                className={`${inputCls} font-mono`}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">API Key</label>
              <input
                type="password"
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="sk-..."
                className={`${inputCls} font-mono`}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">模型名（Model）</label>
              <input
                type="text"
                value={modelName}
                onChange={e => setModelName(e.target.value)}
                placeholder="deepseek-v4-flash"
                className={inputCls}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">
                上下文滑动窗口（轮数）
              </label>
              <input
                type="number"
                min={0}
                value={contextMaxTurns}
                onChange={e => setContextMaxTurns(Number(e.target.value) || 0)}
                placeholder="0"
                className={`${inputCls} font-mono`}
              />
              <p className="text-[10px] text-gray-600 mt-1">
                保留最近 N 轮 user/assistant 对话作为上下文，超出部分被裁剪以节省 token。
                填 0 表示不限制、保留全部历史（每轮含工具调用消息）。为节省 token 建议设为 8–20。
              </p>
            </div>

            {configured && (
              <p className="text-xs text-emerald-400">当前已配置模型：{modelName || '-'}</p>
            )}

            <div className="flex gap-3">
              <button
                onClick={handleSaveModelConfig}
                disabled={configSaving}
                className="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 rounded-xl text-sm font-medium transition-all"
              >
                {configSaving ? '保存中...' : '保存配置'}
              </button>
              {configured && (
                <button
                  onClick={handleClearModelConfig}
                  disabled={configSaving}
                  className="px-5 py-2.5 bg-red-600/10 hover:bg-red-600/20 text-red-400 border border-red-600/20 rounded-xl text-sm font-medium transition-all"
                >
                  清除配置
                </button>
              )}
            </div>
          </div>
        )}
      </section>

      {/* Authentication */}
      <section className="bg-gray-900 border border-gray-800 rounded-2xl p-6 mb-6">
        <h2 className="text-sm font-semibold mb-4 flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${authToken ? 'bg-emerald-400' : 'bg-gray-500'}`} />
          认证
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          登录凭证由服务端在每次启动时自动生成（用户名 admin、随机密码和 Token），
          并通过启动日志输出。无需在设置中手动登录。
        </p>
        <p className="text-xs text-gray-600 mb-4">
          当前状态：
          {authToken ? (
            <span className="text-emerald-400 ml-1">已认证</span>
          ) : (
            <span className="text-amber-400 ml-1">未认证</span>
          )}
        </p>
        <button
          onClick={handleLogout}
          className="px-5 py-2.5 bg-red-600/10 hover:bg-red-600/20 text-red-400 border border-red-600/20 rounded-xl text-sm font-medium transition-all"
        >
          登出
        </button>
      </section>

      {/* System Info */}
      <section className="bg-gray-900 border border-gray-800 rounded-2xl p-6 mb-6">
        <h2 className="text-sm font-semibold mb-3">系统信息</h2>
        <dl className="space-y-2 text-sm">
          <div className="flex justify-between">
            <dt className="text-gray-500">后端地址</dt>
            <dd className="font-mono text-gray-300">{apiUrl}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-gray-500">服务状态</dt>
            <dd className={serverConnected ? 'text-emerald-400' : 'text-red-400'}>
              {serverConnected ? `已连接 (v${serverVersion})` : '未连接'}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-gray-500">当前模型</dt>
            <dd className="text-gray-300">{configured ? modelName : '未配置'}</dd>
          </div>
        </dl>
      </section>

      {/* About */}
      <section className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
        <h2 className="text-sm font-semibold mb-3">关于白泽</h2>
        <div className="space-y-2 text-sm text-gray-400">
          <p>
            <strong className="text-gray-300">白泽 (Baize)</strong>
            开源 AI 安全框架，内置 30+ 安全智能体，
            支持多阶段渗透测试杀伤链。
          </p>
          <p className="text-xs text-gray-600">
            版本: {serverVersion || '未知'}
          </p>
        </div>
      </section>
    </div>
  );
}
