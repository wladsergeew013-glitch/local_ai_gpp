import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';

const appVersion = readFileSync(new URL('../VERSION', import.meta.url), 'utf8').trim();
const packageVersion = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')).version;
if (appVersion !== packageVersion) {
    throw new Error('Frontend package version must match VERSION');
}

export default defineConfig({
    plugins: [react()],
    define: {
        __APP_VERSION__: JSON.stringify(appVersion),
    },
    build: {
        assetsDir: 'ui-assets',
    },
});
