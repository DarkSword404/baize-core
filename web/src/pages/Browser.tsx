import { useCallback, useEffect, useRef, useState } from 'react';
import { useApp } from '../context/AppContext';
import { sharedBrowserStreamUrl } from '../api/client';
import type { SharedBrowserStatus as Status } from '../types';
import type { JSX } from 'react';

interface BrowserPanelProps {
  open: boolean;
  onClose: () => void;
}

// Playwright 键名映射:浏览器键盘事件 → Playwright key
const KEY_MAP: Record<string, string> = {
  Enter: 'Enter',
  Tab: 'Tab',
  Backspace: 'Backspace',
  Escape: 'Escape',
  Delete: 'Delete',
  Home: 'Home',
  End: 'End',
  PageUp: 'PageUp',
  PageDown: 'PageDown',
  ArrowUp: 'ArrowUp',
  ArrowDown: 'ArrowDown',
  ArrowLeft: 'ArrowLeft',
  ArrowRight: 'ArrowRight',
  F1: 'F1', F2: 'F2', F3: 'F3', F4: 'F4', F5: 'F5', F6: 'F6',
  F7: 'F7', F8: 'F8', F9: 'F9', F10: 'F10', F11: 'F11', F12: 'F12',
};

// 视口默认尺寸(与后端 shared_browser.VIEWPORT_* 对齐,实际以 status.viewport 为准)
const DEFAULT_VW = 1366;
const DEFAULT_VH = 768;

export function BrowserPanel({ open, onClose }: BrowserPanelProps): JSX.Element {
  const { addToast } = useApp();

  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [addressInput, setAddressInput] = useState('');
  const [opening, setOpening] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [hoverPos, setHoverPos] = useState<{ x: number; y: number } | null>(null);
  const [keyboardFocused, setKeyboardFocused] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const addressFocusedRef = useRef(false);
  const lastMoveRef = useRef(0);
  const wheelAccumRef = useRef({ dx: 0, dy: 0 });
  const lastWheelRef = useRef(0);
  // 点击/拖拽状态
  const mouseDownRef = useRef<{ x: number; y: number; button: string; time: number } | null>(null);
  const lastClickRef = useRef<{ x: number; y: number; button: string; time: number } | null>(null);

  const VW = status?.viewport?.width ?? DEFAULT_VW;
  const VH = status?.viewport?.height ?? DEFAULT_VH;
  const running = !!status?.running;

  const send = useCallback((msg: object) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }, []);

  // ---- WebSocket 连接 + CDP 帧流渲染 ----------------------------------
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    let closed = false;
    let reconnectTimer: number | null = null;

    const renderFrame = async (b64: string, w: number, h: number) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      try {
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const blob = new Blob([bytes], { type: 'image/jpeg' });
        const bmp = await createImageBitmap(blob);
        if (canvas.width !== w) canvas.width = w;
        if (canvas.height !== h) canvas.height = h;
        const ctx = canvas.getContext('2d');
        ctx?.drawImage(bmp, 0, 0, w, h);
        bmp.close();
      } catch {
        // 静默
      }
    };

    const connect = () => {
      const ws = new WebSocket(sharedBrowserStreamUrl());
      wsRef.current = ws;
      ws.onopen = () => setWsConnected(true);
      ws.onmessage = (ev) => {
        let m: { t?: string; d?: string; w?: number; h?: number; url?: string; running?: boolean; msg?: string } & Partial<Status>;
        try {
          m = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (m.t === 'frame' && m.d) {
          void renderFrame(m.d, m.w ?? VW, m.h ?? VH);
        } else if (m.t === 'status') {
          setStatus(m as unknown as Status);
          if (!addressFocusedRef.current) setAddressInput(m.url || '');
          setLoading(false);
          setOpening(false);
        } else if (m.t === 'error') {
          addToast({ type: 'error', title: '浏览器错误', message: m.msg || '' });
        }
      };
      ws.onclose = () => {
        setWsConnected(false);
        if (!closed && open) reconnectTimer = window.setTimeout(connect, 1500);
      };
      ws.onerror = () => setWsConnected(false);
    };
    connect();

    return () => {
      closed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      wsRef.current?.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // ---- 地址栏 / 导航 ----------------------------------------------------
  function handleOpenUrl(raw?: string) {
    let url = (raw ?? addressInput).trim();
    if (!url) {
      addToast({ type: 'warning', title: '请输入 URL', message: '例如 https://example.com/login' });
      return;
    }
    if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
    setOpening(true);
    send({ t: 'open', url });
    setAddressInput(url);
  }

  function handleNav(action: 'back' | 'forward' | 'reload') {
    if (!running) return;
    send({ t: 'nav', action });
  }

  function handleConfirm() {
    send({ t: 'confirm' });
    addToast({ type: 'success', title: '已放行', message: '人工确认信号已发送' });
  }

  function handleClose() {
    if (!confirm('确定要关闭共享浏览器吗？登录态会保留，可随时重新打开。')) return;
    send({ t: 'close' });
  }

  // ---- 鼠标交互(坐标映射 + 虚拟光标 + 点击/拖拽/右键) ----------
  function mapCoords(clientX: number, clientY: number): { x: number; y: number } | null {
    const el = textareaRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    const x = ((clientX - rect.left) / rect.width) * VW;
    const y = ((clientY - rect.top) / rect.height) * VH;
    return { x: Math.max(0, Math.min(VW, x)), y: Math.max(0, Math.min(VH, y)) };
  }

  function onCanvasMouseMove(e: React.MouseEvent) {
    const c = mapCoords(e.clientX, e.clientY);
    if (!c) return;
    setHoverPos(c);
    if (!running) return;
    const now = Date.now();
    if (now - lastMoveRef.current >= 16) {
      // ~60fps 节流；拖拽中或单纯移动都触发远端鼠标移动
      send({ t: 'mouseMove', x: c.x, y: c.y });
      lastMoveRef.current = now;
    }
  }

  function onCanvasMouseDown(e: React.MouseEvent) {
    if (!running) return;
    const c = mapCoords(e.clientX, e.clientY);
    if (!c) return;
    textareaRef.current?.focus();
    const btn = e.button === 2 ? 'right' : e.button === 1 ? 'middle' : 'left';
    // 双击：second click 发 clickCount=2
    const lc = lastClickRef.current;
    const isDbl = lc && lc.button === btn &&
      Math.abs(c.x - lc.x) < 5 && Math.abs(c.y - lc.y) < 5 &&
      Date.now() - lc.time < 400;
    const cc = isDbl ? 2 : 1;
    mouseDownRef.current = { x: c.x, y: c.y, button: btn, time: Date.now() };
    send({ t: 'mouseDown', x: c.x, y: c.y, button: btn, clickCount: cc });
    if (isDbl) lastClickRef.current = null;
  }

  function onCanvasMouseUp(e: React.MouseEvent) {
    if (!running) return;
    const c = mapCoords(e.clientX, e.clientY);
    if (!c) return;
    const btn = e.button === 2 ? 'right' : e.button === 1 ? 'middle' : 'left';
    const md = mouseDownRef.current;
    mouseDownRef.current = null;

    // 判断是否为点击（mousedown 和 mouseup 位置接近）
    const isClick = md && md.button === btn &&
      Math.abs(c.x - md.x) < 5 && Math.abs(c.y - md.y) < 5 &&
      Date.now() - md.time < 500;

    if (isClick) {
      // clickCount 与 mousedown 保持一致（由 onCanvasMouseDown 追踪双击）
      const lc = lastClickRef.current;
      const isDbl = lc && lc.button === btn &&
        Math.abs(c.x - lc.x) < 5 && Math.abs(c.y - lc.y) < 5 &&
        Date.now() - lc.time < 400;
      const cc = isDbl ? 2 : 1;
      if (!isDbl) lastClickRef.current = { x: c.x, y: c.y, button: btn, time: Date.now() };
      send({ t: 'mouseUp', x: c.x, y: c.y, button: btn, clickCount: cc });
    } else {
      // 拖拽结束：普通 mouseUp
      send({ t: 'mouseUp', x: c.x, y: c.y, button: btn, clickCount: 1 });
    }
  }

  function onCanvasContextMenu(e: React.MouseEvent) {
    e.preventDefault();
  }

  function onCanvasWheel(e: React.WheelEvent) {
    if (!running) return;
    wheelAccumRef.current.dx += e.deltaX;
    wheelAccumRef.current.dy += e.deltaY;
    const now = Date.now();
    if (now - lastWheelRef.current >= 50) {
      send({ t: 'wheel', dx: wheelAccumRef.current.dx, dy: wheelAccumRef.current.dy });
      wheelAccumRef.current.dx = 0;
      wheelAccumRef.current.dy = 0;
      lastWheelRef.current = now;
    }
  }

  // ---- 键盘交互(透明 textarea 捕获 + IME + 复制粘贴) ------------------
  function onKeyDown(e: React.KeyboardEvent) {
    // 调试：确认键盘事件是否到达
    console.debug('[browser] keyDown:', e.key, 'running:', running, 'ws:', wsRef.current?.readyState);
    if (!running) return;
    const key = e.key;
    const ctrl = e.ctrlKey || e.metaKey;

    if (fullscreen && key === 'Escape') {
      e.preventDefault();
      setFullscreen(false);
      return;
    }

    // Ctrl+L: 聚焦地址栏
    if (ctrl && key.toLowerCase() === 'l') {
      e.preventDefault();
      const input = document.getElementById('baize-browser-address') as HTMLInputElement | null;
      input?.focus();
      input?.select();
      return;
    }

    // Ctrl+C / Ctrl+X / Ctrl+A: 转发组合键到远端浏览器
    if (ctrl && (key === 'c' || key === 'x' || key === 'a')) {
      e.preventDefault();
      send({ t: 'key', key: key === 'a' ? 'Control+A' : key === 'x' ? 'Control+X' : 'Control+C' });
      return;
    }

    // Ctrl+V: 读取本地剪贴板文本，type 到远端浏览器
    if (ctrl && key === 'v') {
      e.preventDefault();
      void (async () => {
        try {
          const text = await navigator.clipboard.readText();
          if (text) send({ t: 'type', text });
        } catch {
          // clipboard-read 未授权时回退到发送 Control+V 组合键
          send({ t: 'key', key: 'Control+V' });
        }
      })();
      return;
    }

    // 特殊键映射
    if (KEY_MAP[key]) {
      e.preventDefault();
      send({ t: 'key', key: KEY_MAP[key] });
      return;
    }

    // 可打印单字符直接注入
    if (key.length === 1 && !ctrl && !e.altKey) {
      e.preventDefault();
      send({ t: 'type', text: key });
    }
  }

  function onCompositionEnd(e: React.CompositionEvent<HTMLTextAreaElement>) {
    const text = e.data;
    if (text) {
      e.preventDefault();
      send({ t: 'type', text });
    }
  }

  // ---- UI --------------------------------------------------------------
  const Toolbar = (
    <div className="flex items-center gap-1 px-2 py-1.5 border-b border-gray-800 bg-gray-900/80">
      <button
        onClick={() => handleNav('back')}
        disabled={!running}
        title="后退"
        className="p-1.5 rounded-md text-gray-300 hover:bg-gray-800 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
        </svg>
      </button>
      <button
        onClick={() => handleNav('forward')}
        disabled={!running}
        title="前进"
        className="p-1.5 rounded-md text-gray-300 hover:bg-gray-800 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
      </button>
      <button
        onClick={() => handleNav('reload')}
        disabled={!running}
        title="刷新"
        className="p-1.5 rounded-md text-gray-300 hover:bg-gray-800 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      </button>
      <form
        className="flex-1 min-w-0 flex"
        onSubmit={(e) => { e.preventDefault(); void handleOpenUrl(); }}
      >
        <input
          id="baize-browser-address"
          type="text"
          value={addressInput}
          onChange={e => setAddressInput(e.target.value)}
          onFocus={() => { addressFocusedRef.current = true; }}
          onBlur={() => { addressFocusedRef.current = false; }}
          placeholder="输入 URL 并回车打开"
          className="flex-1 min-w-0 px-2.5 py-1.5 bg-gray-950 border border-gray-700 rounded-md text-xs text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none font-mono"
        />
      </form>
      <button
        onClick={() => handleOpenUrl()}
        disabled={opening || !addressInput.trim()}
        title="打开"
        className="p-1.5 rounded-md text-gray-300 hover:bg-gray-800 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        {opening ? (
          <span className="block w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
        ) : (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6" />
          </svg>
        )}
      </button>
      <button
        onClick={() => setFullscreen(f => !f)}
        title={fullscreen ? '退出全屏' : '全屏'}
        className="p-1.5 rounded-md text-gray-300 hover:bg-gray-800 hover:text-white transition-colors"
      >
        {fullscreen ? (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 9V4.5M9 9H4.5M9 9l3-3m3 3V4.5M15 9h4.5M15 9l-3-3m-3 9v4.5M9 15H4.5M9 15l3 3m3-3v4.5M15 15h4.5M15 15l-3 3" />
          </svg>
        ) : (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4h4m12 0h4v4m0 12v4h-4M4 16v4h4" />
          </svg>
        )}
      </button>
      <button
        onClick={onClose}
        title="收起面板"
        className="p-1.5 rounded-md text-gray-400 hover:text-red-400 hover:bg-gray-800 transition-colors"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );

  const Screen = (
    <div className="relative flex-1 min-h-0 bg-gray-950 flex items-center justify-center overflow-hidden">
      {!running ? (
        <div className="flex flex-col items-center justify-center text-gray-600 px-4 text-center">
          <svg className="w-10 h-10 mb-2 text-gray-700" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 17.25v1.007a3 3 0 01-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0115 18.257V17.25m6-12V15a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 15V5.25m18 0A2.25 2.25 0 0018.75 3H5.25A2.25 2.25 0 003 5.25m18 0V12a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 12V5.25" />
          </svg>
          <div className="text-xs">共享浏览器未启动</div>
          <div className="text-[10px] text-gray-700 mt-1">在地址栏输入 URL 回车打开，或让 AI 调用 shared_browser_open</div>
          {!wsConnected && <div className="text-[10px] text-amber-600 mt-1">正在连接实时通道…</div>}
        </div>
      ) : (
        <div className="relative inline-block max-w-full max-h-full">
          <canvas
            ref={canvasRef}
            width={VW}
            height={VH}
            className="block max-w-full max-h-full select-none"
            style={{
              cursor: 'none', // 隐藏 OS 光标，用虚拟光标
              maxHeight: fullscreen ? 'calc(100vh - 7.5rem)' : '60vh',
            }}
            draggable={false}
          />
          {/* 透明键盘捕获层 — 自然接收焦点和键盘，鼠标事件转发到后端 */}
          <textarea
            ref={textareaRef}
            tabIndex={0}
            value=""
            onChange={() => { /* onKeyDown/onCompositionEnd 拦截 */ }}
            onKeyDown={onKeyDown}
            onCompositionEnd={onCompositionEnd}
            onFocus={() => setKeyboardFocused(true)}
            onBlur={() => setKeyboardFocused(false)}
            onMouseDown={onCanvasMouseDown}
            onMouseUp={onCanvasMouseUp}
            onMouseMove={onCanvasMouseMove}
            onMouseLeave={() => { setHoverPos(null); mouseDownRef.current = null; }}
            onContextMenu={onCanvasContextMenu}
            onWheel={onCanvasWheel}
            className="absolute inset-0 w-full h-full opacity-0 resize-none outline-none border-0"
            style={{ cursor: 'none', background: 'transparent', caretColor: 'transparent' }}
            aria-label="浏览器键盘输入区"
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
          />
          {/* 虚拟光标：本地即时跟随鼠标，远端 mouseMove 同步触发 hover */}
          {hoverPos && (
            <div
              className="absolute pointer-events-none"
              style={{
                left: `${(hoverPos.x / VW) * 100}%`,
                top: `${(hoverPos.y / VH) * 100}%`,
              }}
            >
              <svg width="18" height="18" viewBox="0 0 18 18" fill="white" stroke="black" strokeWidth="1">
                <path d="M1 1 L1 14 L5 10.5 L7.5 16 L9.5 15 L7 9.5 L13 9.5 Z" />
              </svg>
            </div>
          )}
          {/* 键盘焦点指示 */}
          {running && keyboardFocused && (
            <div className="absolute top-1 left-1 px-1.5 py-0.5 rounded bg-blue-600/30 text-[10px] text-blue-300 pointer-events-none border border-blue-600/40">
              键盘已连接 · 直接输入将发送到页面
            </div>
          )}
          {/* WS 连接状态指示 */}
          {!wsConnected && (
            <div className="absolute top-1 right-1 px-1.5 py-0.5 rounded bg-amber-600/30 text-[10px] text-amber-300 pointer-events-none">
              实时通道重连中…
            </div>
          )}
          {/* 坐标提示 */}
          {running && hoverPos && (
            <div className="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-black/60 text-[10px] text-gray-300 font-mono pointer-events-none">
              {hoverPos.x.toFixed(0)}, {hoverPos.y.toFixed(0)}
            </div>
          )}
        </div>
      )}
    </div>
  );

  const BottomBar = (
    <div className="flex items-center gap-2 px-2 py-1.5 border-t border-gray-800 bg-gray-900/80">
      <div className="flex items-center gap-1.5 min-w-0 flex-1">
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${running ? (wsConnected ? 'bg-emerald-500' : 'bg-amber-500') : 'bg-gray-600'}`} />
        <span className={`text-[11px] truncate ${running ? 'text-emerald-400' : 'text-gray-500'}`}>
          {loading ? '...' : running ? (status?.headless ? '无头·运行中' : '有头·运行中') : '未启动'}
        </span>
        {status?.url && (
          <span className="text-[10px] text-gray-600 truncate font-mono hidden sm:block">{status.url}</span>
        )}
      </div>
      <button
        onClick={handleConfirm}
        disabled={!running}
        title="人工完成扫码/登录后放行 AI"
        className="px-2.5 py-1 text-[11px] bg-emerald-600/10 hover:bg-emerald-600/20 border border-emerald-600/30 text-emerald-400 rounded-md transition-colors disabled:opacity-30 disabled:cursor-not-allowed flex-shrink-0"
      >
        我已完成（放行 AI）
      </button>
      <button
        onClick={handleClose}
        disabled={!running}
        title="关闭浏览器（保留登录态）"
        className="px-2 py-1 text-[11px] bg-gray-800 hover:bg-red-600/20 hover:text-red-400 border border-gray-700 hover:border-red-600/30 text-gray-400 rounded-md transition-colors disabled:opacity-30 disabled:cursor-not-allowed flex-shrink-0"
      >
        关闭
      </button>
    </div>
  );

  // ---- 渲染 ------------------------------------------------------------
  if (fullscreen) {
    return (
      <div className="fixed inset-0 z-50 bg-gray-950 flex flex-col">
        <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800 bg-gray-900">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${running ? 'bg-emerald-500' : 'bg-gray-600'}`} />
            <span className="text-sm font-semibold text-gray-200">共享协作浏览器 · 全屏</span>
            <span className="text-[10px] text-gray-500 font-mono">{VW}×{VH}</span>
          </div>
          <button
            onClick={() => setFullscreen(false)}
            className="px-2 py-1 text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-gray-300 transition-colors"
          >
            退出全屏 (Esc)
          </button>
        </div>
        {Toolbar}
        {Screen}
        {BottomBar}
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-gray-950 border-l border-gray-800 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800 bg-gray-900/60">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${running ? 'bg-emerald-500' : 'bg-gray-600'}`} />
          <span className="text-sm font-semibold text-gray-200 truncate">共享协作浏览器</span>
        </div>
        <button
          onClick={() => setFullscreen(true)}
          title="全屏（推荐交互）"
          className="p-1.5 rounded-lg text-gray-400 hover:text-blue-400 hover:bg-gray-800 transition-colors flex-shrink-0"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4h4m12 0h4v4m0 12v4h-4M4 16v4h4" />
          </svg>
        </button>
      </div>
      {Toolbar}
      {Screen}
      {BottomBar}
    </div>
  );
}
