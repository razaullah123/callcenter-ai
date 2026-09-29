import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by the voice server at /console; `npm run dev` proxies API + WebSockets to it.
export default defineConfig({
  base: "/console/",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8080", ws: true },
      "/ws": { target: "http://localhost:8080", ws: true },
    },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 4000 },
});
