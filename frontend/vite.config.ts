import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    port: 5173,
    // In dev, /api and /ws are proxied to the FastAPI backend so the
    // frontend can call relative URLs. Production serves the built
    // frontend separately; VITE_API_URL then points at the backend
    // (see .env.example).
    proxy: {
      '/api': 'http://localhost:8000',
      // The KDS WebSocket needs `ws: true` — the string shorthand
      // only proxies plain HTTP, so a bare '/ws' entry would leave
      // the kitchen socket stuck on "Connecting…".
      '/ws': { target: 'http://localhost:8000', ws: true },
    },
  },
})
