import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `okrag serve` mounts the built app at / and answers /api itself, so it is
// same-origin. In dev the proxy reproduces that rather than adding CORS to the
// API for a case only the dev server has.
//
// The build lands *inside the Python package* rather than in web/dist, because
// that is the only location that is true for an installed user. `web/dist` is a
// path relative to this checkout, and `okrag serve` running against somebody
// else's corpus has no way to reach it — the defect DESIGN §13 M6 records. One
// output directory serves both: the wheel ships it, and a checkout finds it
// exactly where an install would.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/okrag/api/static", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
