import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** 可选：错误时显示的标题 */
  title?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * 全局错误边界：捕获子组件渲染期异常，避免整棵 React 树白屏崩溃。
 * 提供降级 UI + 重试按钮（刷新当前会话页）。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 生产环境下仅在 console.error 输出，避免泄露内部状态到 UI
    console.error('[ErrorBoundary] 渲染异常:', error, info);
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-6 text-center">
          <div className="text-5xl">⚠️</div>
          <h2 className="text-xl font-semibold text-gray-100">
            {this.props.title ?? '页面发生错误'}
          </h2>
          <p className="max-w-md text-sm text-gray-400">
            组件渲染异常，已为你隔离故障，不影响其它页面。可点击重试恢复。
          </p>
          {this.state.error?.message && (
            <pre className="max-w-lg overflow-auto rounded bg-gray-900/60 p-3 text-left text-xs text-red-300">
              {this.state.error.message}
            </pre>
          )}
          <button
            onClick={this.handleRetry}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
          >
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
