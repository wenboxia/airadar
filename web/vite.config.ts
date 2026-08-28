import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  // GitHub Pages 部署在 /airadar/ 子路径下；Vercel 部署在根路径。
  // 由环境变量切换，代码里统一用 import.meta.env.BASE_URL 拼资源路径。
  base: process.env.AIRADAR_BASE ?? '/',
  plugins: [react(), tailwindcss()],
  // 开发时直接读仓库根部的 data/feed；构建时由 prebuild 脚本拷进 public/data
  publicDir: 'public',
})
