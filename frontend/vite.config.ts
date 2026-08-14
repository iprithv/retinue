/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// React Compiler (D1) rides the babel pipeline of @vitejs/plugin-react.
// Dev API calls proxy to the local backend, so cookies stay same-origin.
export default defineConfig({
  plugins: [
    react({ babel: { plugins: [["babel-plugin-react-compiler", {}]] } }),
    tailwindcss(),
  ],
  server: {
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: {
    target: "es2022",
    sourcemap: false,
  },
  worker: {
    format: "es", // the highlight worker lazy-imports shiki (code-split)
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
