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
    // In dev, /api is proxied to the FastAPI backend so the frontend can
    // call relative URLs. Production serves the built frontend separately;
    // VITE_API_URL then points at the backend (see .env.example).
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
