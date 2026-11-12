import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'

export default defineConfig({
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(readFileSync(new URL('../VERSION', import.meta.url), 'utf8').trim()) },
  build: {
    assetsDir: 'ui-assets',
  },
  server: {
    proxy: Object.fromEntries(['/api', '/v1', '/assets', '/docs', '/openapi.json'].map(path => [path, {
      target: process.env.LOCAL_AI_GPP_API_TARGET || 'http://127.0.0.1:8765',
      changeOrigin: true,
      proxyTimeout: 210000,
    }])),
  },
})
