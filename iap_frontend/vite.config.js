import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backendHost = process.env.BACKEND_IP || "iap_backend";
const backendPort = process.env.BACKEND_PORT || "44000";
const apiProxy = {
  "/api": {
    target: `http://${backendHost}:${backendPort}`,
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 8501,
    strictPort: true,
    proxy: apiProxy,
  },
  preview: {
    host: "0.0.0.0",
    port: 8501,
    strictPort: true,
    proxy: apiProxy,
  },
});
