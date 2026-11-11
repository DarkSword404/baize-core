import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 说明：Vite 8 / Rolldown 已移除 `esbuild.drop` 选项。此前该配置会让
// `tsc -b` 直接失败（ESBuildOptions 不识别 drop）。若生产环境确需剔除
// console/debugger，请在部署侧用压缩器（terser/oxc）处理，或后续通过
// 自定义 transform 插件实现，不要依赖已删除的构建选项。
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
