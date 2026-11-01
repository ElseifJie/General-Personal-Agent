import { expect, test } from "@playwright/test";

const PIN = process.env.WB_E2E_PIN || process.env.DEMO_LOGIN_PIN || "";

test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  },
});

test.beforeEach(async ({ page }) => {
  test.skip(!PIN, "WB_E2E_PIN/DEMO_LOGIN_PIN 未设置");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "登录交互工作台" })).toBeVisible();
  await page.getByPlaceholder("共享 PIN").fill(PIN);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "文本交互" })).toBeVisible({
    timeout: 15_000,
  });
});

test("hold-to-talk streams partial/final, text delta and audio over the conversation WS", async ({
  page,
}) => {
  let partialSent = false;
  await page.routeWebSocket(/\/ws\/session/, (ws) => {
    ws.send(JSON.stringify({ type: "ready", conversationId: "mock", sampleRate: 24_000 }));
    ws.onMessage((message) => {
      let data;
      try {
        data = JSON.parse(String(message));
      } catch {
        return;
      }
      if (data.type === "audio" && !partialSent) {
        partialSent = true;
        ws.send(JSON.stringify({ type: "partial", text: "你" }));
      }
      if (data.type === "audio_end") {
        ws.send(JSON.stringify({ type: "final", text: "你好，介绍一下你自己", asr_ms: 150 }));
        ws.send(JSON.stringify({ type: "text.delta", text: "你好！我是 GPA。" }));
        ws.send(JSON.stringify({ type: "audio", data: "AAAA".repeat(400), sample_rate: 24_000 }));
        ws.send(
          JSON.stringify({
            type: "turn.metrics",
            turnId: "turn_mock",
            outcome: "completed",
            final: true,
            text: "你好！我是 GPA。",
            metrics: { asr_ms: 150, ttft_ms: 100, first_audio_ms: 220, total_ms: 700 },
          }),
        );
      }
    });
  });

  await page.getByRole("button", { name: "语音", exact: true }).click();
  await expect(page).toHaveURL(/\/interact\/voice\/c_/);
  const talk = page.getByRole("button", { name: /按住说话/ });
  await expect(talk).toBeVisible();
  const box = await talk.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await expect(page.locator(".gpa-talk.is-recording")).toBeVisible({
    timeout: 8_000,
  });
  await page.mouse.up();

  await expect(page.getByText("你好！我是 GPA。")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "详情", exact: true }).click();
  const rail = page.locator("#gpa-detail-rail");
  await expect(rail).toBeVisible();
  await expect(rail).toContainText("150ms");
  await expect(rail).toContainText("220ms");
  await expect(page.locator('textarea[aria-label="输入消息"]')).toHaveCount(0);
});
