import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    // Dev : proxy de l'API vers l'app FastAPI locale
    proxy: { '/api': 'http://127.0.0.1:8001' },
  },
})
