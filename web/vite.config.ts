import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // 开发时直接读仓库根部的 data/feed；构建时由 prebuild 脚本拷进 public/data
  publicDir: 'public',
})
