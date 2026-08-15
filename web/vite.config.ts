import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `ragged serve` mounts dist/ at / and answers /api itself, so the built app is
// same-origin. In dev the proxy reproduces that rather than adding CORS to the
// API for a case only the dev server has.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
