import { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import {
  setApiBase, getApiBase, getAuthToken, healthCheck, logout,
  getModelConfig, updateModelConfig, clearModelConfig,
  listReceivers, createReceiver, updateReceiver, deleteReceiver,
} from '../api/client';
import type { ReceiverConfig as ReceiverConfigType } from '../api/client';
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

  // 接收器状态
  const [receivers, setReceivers] = useState<ReceiverConfigType[]>([]);
  const [receiverLoading, setReceiverLoading] = useState(true);
  const [showReceiverForm, setShowReceiverForm] = useState(false);
  const [editingReceiver, setEditingReceiver] = useState<ReceiverConfigType | null>(null);
  const [receiverForm, setReceiverForm] = useState({
    name: '', kind: 'webhook',
    webhook_path: '', syslog_port: 514, syslog_host: '0.0.0.0',
    watch_dir: '', watch_patterns: '*',
  });
  const [receiverSaving, setReceiverSaving] = useState(false);

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

  // 加载接收器列表
  function loadReceivers() {
    setReceiverLoading(true);
    listReceivers()
      .then(r => setReceivers(r.receivers))
      .catch(() => {})
      .finally(() => setReceiverLoading(false));
  }
  useEffect(() => { loadReceivers(); }, []);

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

  // ---- 接收器操作 ----
  function openCreateReceiver() {
    setEditingReceiver(null);
    setReceiverForm({ name: '', kind: 'webhook', webhook_path: '', syslog_port: 514, syslog_host: '0.0.0.0', watch_dir: '', watch_patterns: '*' });
    setShowReceiverForm(true);
  }
  function openEditReceiver(r: ReceiverConfigType) {
    setEditingReceiver(r);
    setReceiverForm({
      name: r.name, kind: r.kind,
      webhook_path: r.webhook_path, syslog_port: r.syslog_port || 514,
      syslog_host: r.syslog_host || '0.0.0.0',
      watch_dir: r.watch_dir, watch_patterns: r.watch_patterns || '*',
    });
    setShowReceiverForm(true);
  }
  async function saveReceiver() {
    if (!receiverForm.name.trim()) return;
    setReceiverSaving(true);
    try {
      if (editingReceiver) {
        await updateReceiver(editingReceiver.id, receiverForm);
      } else {
        await createReceiver(receiverForm);
      }
      setShowReceiverForm(false);
      loadReceivers();
    } catch (e: any) {
      addToast({ type: 'error', title: '保存失败', message: e.message });
    } finally { setReceiverSaving(false); }
  }
  async function toggleReceiver(id: string, enabled: boolean) {
    try {
      await updateReceiver(id, { enabled });
      loadReceivers();
    } catch (e: any) {
      addToast({ type: 'error', title: '操作失败', message: e.message });
    }
  }
  async function removeReceiver(id: string) {
    if (!confirm('确定删除此接收器？')) return;
    try {
      await deleteReceiver(id);
      loadReceivers();
      addToast({ type: 'success', title: '已删除' });
    } catch (e: any) {
      addToast({ type: 'error', title: '删除失败', message: e.message });
    }
  }

  function handleLogout() {
    logout();
    window.location.reload();
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

      {/* 数据接收器 */}
      <section className="bg-gray-900 border border-gray-800 rounded-2xl p-6 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-cyan-400" />
            数据接收器
          </h2>
          <button onClick={openCreateReceiver}
            className="px-3 py-1.5 text-xs bg-cyan-600 hover:bg-cyan-500 rounded-lg transition-colors">
            + 新建接收器
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          数据接收器用于接收外部设备投递的数据（漏扫报告 PDF/HTML、Syslog 告警等），
          配合自动化流水线使用。支持 Webhook、Syslog、文件监视三种协议。
        </p>
        {receiverLoading ? (
          <p className="text-xs text-gray-500">加载中...</p>
        ) : receivers.length === 0 ? (
          <p className="text-xs text-gray-600 py-4 text-center">暂无接收器，点击上方按钮创建</p>
        ) : (
          <div className="space-y-2">
            {receivers.map(r => (
              <div key={r.id} className="flex items-center gap-3 p-3 rounded-xl bg-gray-800/50 border border-gray-700/50">
                <div className={`w-2 h-2 rounded-full shrink-0 ${r.enabled ? 'bg-emerald-400' : 'bg-gray-500'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{r.name}</div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700 text-gray-400 uppercase">{r.kind}</span>
                    {r.kind === 'webhook' && <span className="text-[10px] text-gray-500 font-mono">/api/v1/hook/{r.webhook_path}</span>}
                    {r.kind === 'syslog' && <span className="text-[10px] text-gray-500 font-mono">UDP :{r.syslog_port}</span>}
                    {r.kind === 'file' && <span className="text-[10px] text-gray-500 truncate">{r.watch_dir}</span>}
                  </div>
                  <div className="text-[10px] text-gray-600 mt-0.5">
                    已接收 {r.total_received} 条 · 队列 {r.queue_size}
                    {r.last_received_at && <> · 最后 {new Date(r.last_received_at * 1000).toLocaleString()}</>}
                  </div>
                </div>
                <button onClick={() => toggleReceiver(r.id, !r.enabled)}
                  className={`px-2 py-1 text-[10px] rounded ${r.enabled ? 'bg-amber-600/20 text-amber-400 hover:bg-amber-600/30' : 'bg-emerald-600/20 text-emerald-400 hover:bg-emerald-600/30'}`}>
                  {r.enabled ? '停用' : '启用'}
                </button>
                <button onClick={() => openEditReceiver(r)}
                  className="px-2 py-1 text-[10px] rounded bg-gray-700 text-gray-300 hover:bg-gray-600">编辑</button>
                <button onClick={() => removeReceiver(r.id)}
                  className="px-2 py-1 text-[10px] rounded bg-red-600/10 text-red-400 hover:bg-red-600/20">删除</button>
              </div>
            ))}
          </div>
        )}
        {/* 创建/编辑表单弹窗 */}
        {showReceiverForm && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowReceiverForm(false)}>
            <div className="bg-gray-900 border border-gray-700 rounded-2xl p-6 w-full max-w-lg mx-4" onClick={e => e.stopPropagation()}>
              <h3 className="text-sm font-semibold mb-4">{editingReceiver ? '编辑接收器' : '新建接收器'}</h3>
              <div className="space-y-3">
                <label className="block">
                  <span className="text-[10px] text-gray-500">名称</span>
                  <input type="text" value={receiverForm.name} onChange={e => setReceiverForm(f => ({ ...f, name: e.target.value }))}
                    className={`${inputCls} mt-0.5`} placeholder="如：SOC告警接收器" />
                </label>
                <label className="block">
                  <span className="text-[10px] text-gray-500">协议类型</span>
                  <select value={receiverForm.kind} onChange={e => setReceiverForm(f => ({ ...f, kind: e.target.value }))}
                    className={`${inputCls} mt-0.5`}>
                    <option value="webhook">HTTP Webhook</option>
                    <option value="syslog">Syslog (UDP)</option>
                    <option value="file">文件监视</option>
                  </select>
                </label>
                {receiverForm.kind === 'webhook' && (
                  <label className="block">
                    <span className="text-[10px] text-gray-500">Webhook 路径</span>
                    <div className="flex items-center mt-0.5">
                      <span className="text-xs text-gray-600 px-2 py-2 bg-gray-850 rounded-l-xl border border-r-0 border-gray-700">/api/v1/hook/</span>
                      <input type="text" value={receiverForm.webhook_path} onChange={e => setReceiverForm(f => ({ ...f, webhook_path: e.target.value }))}
                        className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-r-xl text-sm text-gray-200 outline-none focus:border-blue-500" placeholder="my-receiver" />
                    </div>
                  </label>
                )}
                {receiverForm.kind === 'syslog' && (
                  <>
                    <label className="block">
                      <span className="text-[10px] text-gray-500">监听端口</span>
                      <input type="number" value={receiverForm.syslog_port} onChange={e => setReceiverForm(f => ({ ...f, syslog_port: Number(e.target.value) }))}
                        className={`${inputCls} mt-0.5`} placeholder="514" />
                    </label>
                    <label className="block">
                      <span className="text-[10px] text-gray-500">绑定地址</span>
                      <input type="text" value={receiverForm.syslog_host} onChange={e => setReceiverForm(f => ({ ...f, syslog_host: e.target.value }))}
                        className={`${inputCls} mt-0.5`} placeholder="0.0.0.0" />
                    </label>
                  </>
                )}
                {receiverForm.kind === 'file' && (
                  <>
                    <label className="block">
                      <span className="text-[10px] text-gray-500">监视目录</span>
                      <input type="text" value={receiverForm.watch_dir} onChange={e => setReceiverForm(f => ({ ...f, watch_dir: e.target.value }))}
                        className={`${inputCls} mt-0.5`} placeholder="/var/reports/" />
                    </label>
                    <label className="block">
                      <span className="text-[10px] text-gray-500">文件匹配模式（逗号分隔）</span>
                      <input type="text" value={receiverForm.watch_patterns} onChange={e => setReceiverForm(f => ({ ...f, watch_patterns: e.target.value }))}
                        className={`${inputCls} mt-0.5`} placeholder="*.pdf,*.html,*.json" />
                    </label>
                  </>
                )}
              </div>
              <div className="flex gap-3 mt-5">
                <button onClick={saveReceiver} disabled={receiverSaving}
                  className="flex-1 px-4 py-2.5 bg-cyan-600 hover:bg-cyan-500 disabled:bg-gray-700 rounded-xl text-sm font-medium transition-all">
                  {receiverSaving ? '保存中...' : (editingReceiver ? '更新' : '创建')}
                </button>
                <button onClick={() => setShowReceiverForm(false)}
                  className="px-4 py-2.5 bg-gray-800 hover:bg-gray-700 rounded-xl text-sm transition-all">取消</button>
              </div>
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
