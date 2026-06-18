import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite + Vitest 配置(M3-3)。test.environment=node:ipc 客户端单测不需 DOM。
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
