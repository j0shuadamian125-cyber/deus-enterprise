import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// El panel nunca habla con un LLM: todo pasa por la API de HERMES.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: process.env.HERMES_API || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/health": {
        target: process.env.HERMES_API || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
