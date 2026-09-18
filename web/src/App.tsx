import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppProvider, useApp } from './context/AppContext';
import { ThemeProvider } from './context/ThemeContext';
import { AuthGuard } from './components/AuthGuard';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { Tools } from './pages/Tools';
import PipelineEditor from './pages/PipelineEditor';
import { TaskArchives } from './pages/TaskArchives';
import { Containers } from './pages/Containers';
import { Guardrails } from './pages/Guardrails';
import { Experiences } from './pages/Experiences';
import { Reports } from './pages/Reports';
import { Settings } from './pages/Settings';
import type { JSX } from 'react';

/** 条件渲染编排路由：仅在 baize-orchestration 模块已安装时可用 */
function OrchestrationRoute(): JSX.Element {
  const { installedModules } = useApp();
  if (installedModules?.orchestration?.installed) {
    return <PipelineEditor />;
  }
  return <Navigate to="/dashboard" replace />;
}

export default function App(): JSX.Element {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <AppProvider>
          <ErrorBoundary title="应用初始化失败">
            <AuthGuard>
              <Routes>
                <Route path="/" element={<Layout />}>
                  <Route index element={<Navigate to="/dashboard" replace />} />
                  <Route path="dashboard" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
                  {/* Chat 由 Layout 常驻渲染（切页不卸载，保持流式状态），此处仅保留路由可达性 */}
                  <Route path="chat" element={null} />
                  <Route path="containers" element={<ErrorBoundary><Containers /></ErrorBoundary>} />
                  <Route path="tools" element={<ErrorBoundary><Tools /></ErrorBoundary>} />
                  <Route path="orchestration" element={<ErrorBoundary><OrchestrationRoute /></ErrorBoundary>} />
                  {/* 旧 /sessions 路径重定向到 /archives（任务记录） */}
                  <Route path="sessions" element={<Navigate to="/archives" replace />} />
                  <Route path="archives" element={<ErrorBoundary><TaskArchives /></ErrorBoundary>} />
                  <Route path="guardrails" element={<ErrorBoundary><Guardrails /></ErrorBoundary>} />
                  <Route path="experiences" element={<ErrorBoundary><Experiences /></ErrorBoundary>} />
                  <Route path="reports" element={<ErrorBoundary><Reports /></ErrorBoundary>} />
                  <Route path="settings" element={<ErrorBoundary><Settings /></ErrorBoundary>} />
                </Route>
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
              </Routes>
            </AuthGuard>
          </ErrorBoundary>
        </AppProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
