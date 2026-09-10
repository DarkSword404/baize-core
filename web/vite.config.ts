import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
