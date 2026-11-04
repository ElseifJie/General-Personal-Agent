import { defineConfig } from "@playwright/test";

// 最小链路冒烟：登录 → 列表 → 时间线。
// 前置：FastAPI 在 127.0.0.1:8100（make run）；产物已构建（make build-wb）。
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  // The suite also runs on loaded machines; the default 5s is tight for the
  // voice/SSE assertions when the CPU is busy building or vacuuming.
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.WB_E2E_BASE_URL || "http://127.0.0.1:8100",
    locale: "zh-CN",
  },
  reporter: [["list"]],
});
