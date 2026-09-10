import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The console is served as static files by the api container, so the dev proxy
// points at a locally running api process. Everything the console talks to is
// under these prefixes; nothing else is proxied.
const API_TARGET = process.env['DTK_API_TARGET'] ?? 'http://127.0.0.1:8000'

// Trailing slash on '/api' on purpose. Vite matches a proxy prefix as a plain
// string prefix, so a bare '/api' also swallows the console's own '/api-keys'
// route: opening http://localhost:5173/api-keys in the dev server proxied it to
// the API, which answered with the built console it serves, and the page came up
// blank. Everything the console calls is under '/api/v1/', so the slash costs
// nothing and stops the route from colliding with the prefix.
const PROXIED = ['/api/', '/docs', '/redoc', '/openapi.json', '/healthz', '/readyz']

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
