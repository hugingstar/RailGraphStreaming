import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 127.0.0.1, not localhost: on Windows the IPv6 loopback is often already
// claimed by Docker/WSL relays, which quietly swallow the request.
const API = 'http://127.0.0.1:8123'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5273,
    proxy: {
      '/api': { target: API, changeOrigin: true },
      '/ws': { target: API.replace('http', 'ws'), ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
