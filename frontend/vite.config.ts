import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'

export default defineConfig({
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(readFileSync(new URL('../VERSION', import.meta.url), 'utf8').trim()) },
  build: {
    assetsDir: 'ui-assets',
  },
})
