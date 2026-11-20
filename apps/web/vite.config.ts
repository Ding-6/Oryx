import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// Vite 配置
//
// 为什么 dev 时把 /api 代理到内核
// ------------------------------
// 开发时前端跑在 5173，内核跑在 8765 —— 跨源。
// 内核的 CORS 允许 `http://127.0.0.1:<任意端口>`，所以直接跨源也能通；
// 但**代理更省事**：同源就没有 cookie / Origin / 预检那一堆麻烦，
// 也不用在 dev 时额外处理令牌。
//
// 生产构建产物由内核托管在 `/web/newui/`（core/oryx/server.py 的 /newui 路由），
// 不经过 Vite。
export default defineConfig(({ command }) => ({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: false,
      },
    },
  },
  // 构建产物的 base。
  //
  // 生产产物被内核托管在 `/web/newui/`，所以资源路径要带上这一段前缀；
  // 开发时用 `/`（Vite dev server 自己会处理）。
  //
  // **不能用 `import.meta.env.PROD`** —— 那是给**应用源码**用的，
  // 在配置文件里是 undefined（`Cannot read properties of undefined`）。
  // 配置里要判断「是不是在构建」，用 `command`。
  base: command === 'build' ? '/web/newui/' : '/',
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
}))
