import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // Override when the built app is served from a subpath behind a proxy,
  // e.g. VITE_BASE=/games/engine/ npm run build
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
  server: {
    host: "0.0.0.0",
  },
});
