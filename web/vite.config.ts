import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` proxies the API to `uv run airgap serve` on :8000.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Two entry pages: the console (index.html) and the front-door page (landing.html).
    rollupOptions: { input: { main: "index.html", landing: "landing.html" } },
  },
});
