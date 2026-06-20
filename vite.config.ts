import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite + Vitest 配置(M3-3)。test.environment=node:ipc 客户端单测不需 DOM。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 忽略外部工具(.venv 包安装、Python 临时 db、Vite 自身临时文件等)的写抖动 ——
    // 否则 Windows 文件系统通知会让 Vite 误判 .env / vite.config.ts 改动而反复 restart,
    // 导致页面频繁白屏刷新(M3.6 现场暴露)。
    watch: {
      ignored: [
        "**/.venv/**", "**/node_modules/**", "**/data/**", "**/.git/**",
        "**/__pycache__/**", "**/sprites_raw/**", "**/portraits_raw/**",
        "**/vite.config.ts.timestamp-*.mjs",
      ],
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
