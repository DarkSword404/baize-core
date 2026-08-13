import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppProvider, useApp } from './context/AppContext';
import { ThemeProvider } from './context/ThemeContext';
import { AuthGuard } from './components/AuthGuard';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { Chat } from './pages/Chat';
import { Agents } from './pages/Agents';
import PipelineEditor from './pages/PipelineEditor';
import { Sessions } from './pages/Sessions';
import { Guardrails } from './pages/Guardrails';
import { Experiences } from './pages/Experiences';
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
          <AuthGuard>
            <Routes>
              <Route path="/" element={<Layout />}>
                <Route index element={<Navigate to="/dashboard" replace />} />
                <Route path="dashboard" element={<Dashboard />} />
                <Route path="chat" element={<Chat />} />
                <Route path="agents" element={<Agents />} />
                <Route path="orchestration" element={<OrchestrationRoute />} />
                <Route path="sessions" element={<Sessions />} />
                <Route path="guardrails" element={<Guardrails />} />
                <Route path="experiences" element={<Experiences />} />
                <Route path="settings" element={<Settings />} />
              </Route>
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </AuthGuard>
        </AppProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
