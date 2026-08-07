import type { Toast } from '../types';
import { useApp } from '../context/AppContext';
import type { JSX } from 'react';

const icons: Record<Toast['type'], string> = {
  success: 'M5 13l4 4L19 7',
  error: 'M6 18L18 6M6 6l12 12',
  info: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  warning: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z',
};

const colors: Record<Toast['type'], string> = {
  success: 'border-emerald-600/30 bg-emerald-950/60 text-emerald-300',
  error: 'border-red-600/30 bg-red-950/60 text-red-300',
  info: 'border-blue-600/30 bg-blue-950/60 text-blue-300',
  warning: 'border-amber-600/30 bg-amber-950/60 text-amber-300',
};

export function ToastContainer({ toasts }: { toasts: Toast[] }): JSX.Element {
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-none">
      {toasts.map(t => (
        <ToastItem key={t.id} toast={t} />
      ))}
    </div>
  );
}

function ToastItem({ toast }: { toast: Toast }): JSX.Element {
  const { removeToast } = useApp();

  return (
    <div
      className={`toast-enter pointer-events-auto flex items-start gap-3 px-4 py-3 rounded-xl border backdrop-blur-xl shadow-2xl ${colors[toast.type]}`}
    >
      <svg className="w-5 h-5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d={icons[toast.type]} />
      </svg>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold">{toast.title}</p>
        {toast.message && <p className="text-xs opacity-75 mt-0.5">{toast.message}</p>}
      </div>
      <button onClick={() => removeToast(toast.id)} className="flex-shrink-0 opacity-50 hover:opacity-100 transition-opacity">
        <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    </div>
  );
}
