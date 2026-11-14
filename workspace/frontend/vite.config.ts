import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: { assetsDir: "ui-assets" },
  server: {
    proxy: { "/api": { target: "http://127.0.0.1:8776", changeOrigin: true } },
  },
});
