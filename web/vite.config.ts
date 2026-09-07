import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The console is served as static files by the api container, so the dev proxy
// points at a locally running api process. Everything the console talks to is
// under these prefixes; nothing else is proxied.
const API_TARGET = process.env['DTK_API_TARGET'] ?? 'http://127.0.0.1:8000'

const PROXIED = ['/api', '/docs', '/redoc', '/openapi.json', '/healthz', '/readyz']

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: Object.fromEntries(
      PROXIED.map((prefix) => [
        prefix,
        { target: API_TARGET, changeOrigin: true, ws: false },
      ]),
    ),
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
    target: 'es2022',
    chunkSizeWarningLimit: 900,
  },
})
