import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// P2: the SPA owns `/`; FastAPI serves dist with SPA fallback (main.py).
// Vite dev proxies the API/SSE to 127.0.0.1:8100.
export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8100", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8100", ws: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      input: {
        main: "index.html",
      },
      output: {
        // Keep the framework and the Markdown pipeline out of the app chunk so
        // a first visit downloads them in parallel and a page navigation never
        // re-parses the whole application bundle.
        manualChunks: {
          "vendor-react": ["react", "react-dom", "react-router-dom"],
          "vendor-markdown": ["react-markdown", "remark-gfm"],
        },
      },
    },
  },
});
