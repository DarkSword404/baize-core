import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 生产构建剔除 console.* 与 debugger，避免泄露内部状态/会话片段到浏览器控制台
const isProd = process.env.NODE_ENV === 'production'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  esbuild: isProd ? { drop: ['console', 'debugger'] as Array<'console' | 'debugger'> } : undefined,
  server: {
    proxy: {
        '/api': {
          target: process.env.BAIZE_API_TARGET || 'http://localhost:8001',
          changeOrigin: true,
          ws: true,
          // SSE/长任务：禁用代理超时，靠 SSE 心跳保活
          timeout: 0,
          proxyTimeout: 0,
        },
      },
  },
})
