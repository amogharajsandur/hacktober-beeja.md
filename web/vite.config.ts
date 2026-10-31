import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `make viewer` builds this and serves it with `beeja serve` (data + import API on :8765).
// For `npm run dev`, /api and /data are proxied to a running `beeja serve`.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
