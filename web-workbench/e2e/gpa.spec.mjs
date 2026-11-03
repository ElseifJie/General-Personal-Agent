import { expect, test } from "@playwright/test";

// P2 acceptance: login -> text conversation -> SSE reply -> history preview ->
// mode switch keeps the same conversation. Requires FastAPI on :8100 and a
// built SPA; set WB_E2E_PIN to DEMO_LOGIN_PIN to run.
const PIN = process.env.WB_E2E_PIN || process.env.DEMO_LOGIN_PIN || "";

async function login(page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "登录交互工作台" })).toBeVisible();
  await page.getByPlaceholder("共享 PIN").fill(PIN);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "文本交互" })).toBeVisible({
    timeout: 15_000,
  });
}

test.beforeEach(async ({ page }) => {
  test.skip(!PIN, "WB_E2E_PIN/DEMO_LOGIN_PIN 未设置");
  await login(page);
});

test("text conversation streams a reply and keeps the conversation id", async ({ page }) => {
  const box = page.locator('textarea[aria-label="输入消息"]');
  await expect(box).toBeVisible();
  await box.fill("请回复：P2 E2E 成功");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page).toHaveURL(/\/interact\/text\/c_/, { timeout: 15_000 });
  await expect(page.getByText(/P2 E2E 成功/).last()).toBeVisible({
    timeout: 20_000,
  });
  const conversationUrl = page.url();

  await page.getByRole("button", { name: "语音", exact: true }).click();
  await expect(page).toHaveURL(/\/interact\/voice\/c_/);
  expect(page.url().split("/").pop()).toBe(conversationUrl.split("/").pop());
  await expect(page.locator(".gpa-page--voice")).toBeVisible();
  await expect(page.getByRole("button", { name: /按住说话/ })).toBeVisible();
  await expect(page.locator('textarea')).toHaveCount(0);
  // The header carries no status line any more; the mic enabling means the
  // voice WebSocket said ready.
  await expect(page.getByRole("button", { name: /按住说话/ })).toBeEnabled({
    timeout: 10_000,
  });

  await page.getByRole("button", { name: "文本", exact: true }).click();
  await expect(page).toHaveURL(conversationUrl);
});

test("history lists conversations and opens the end-to-end timeline", async ({ page }) => {
  const marker = `历史验收 ${Date.now()}`;
  await page.locator('textarea[aria-label="输入消息"]').fill(marker);
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page).toHaveURL(/\/interact\/text\/c_/, { timeout: 15_000 });
  await expect(page.getByText(marker, { exact: true }).last()).toBeVisible({
    timeout: 20_000,
  });

  await page.getByRole("link", { name: "历史会话", exact: true }).click();
  await expect(page.getByRole("heading", { name: "历史会话", exact: true })).toBeVisible();
  await expect(page.getByLabel("会话范围")).toBeVisible();
  const card = page.locator(".gpa-history-card", { hasText: marker }).first();
  await expect(card).toBeVisible();
  await expect(card.getByText("已完成", { exact: true })).toBeVisible();
  await card.locator(".gpa-history-card__main").click();
  await expect(page).toHaveURL(/\/interact\/text\/c_/);
  await expect(page.getByText(marker, { exact: true }).last()).toBeVisible({
    timeout: 10_000,
  });
});

test("new conversation page starts a brand-new voice conversation", async ({ page }) => {
  // Seed an existing conversation: the new page's voice tab must start fresh
  // instead of resuming it.
  const marker = `新建语音 ${Date.now()}`;
  await page.locator('textarea[aria-label="输入消息"]').fill(marker);
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page).toHaveURL(/\/interact\/text\/c_/, { timeout: 15_000 });
  await expect(page.getByText(marker, { exact: true }).last()).toBeVisible({
    timeout: 20_000,
  });
  const previousId = page.url().split("/").pop();

  await page.getByRole("link", { name: "新建会话", exact: true }).click();
  await expect(page).toHaveURL(/\/interact\/text$/);
  await page.getByRole("button", { name: "语音", exact: true }).click();
  await expect(page).toHaveURL(/\/interact\/voice\/c_/, { timeout: 15_000 });
  expect(page.url().split("/").pop()).not.toBe(previousId);
  await expect(
    page.getByText("开启一段新的 conversation", { exact: false }),
  ).toBeVisible();
  await expect(page.locator('textarea')).toHaveCount(0);
  await expect(page.getByRole("button", { name: /按住说话/ })).toBeVisible();
  // No status line in the header any more: an enabled mic means the WS is ready.
  await expect(page.getByRole("button", { name: /按住说话/ })).toBeEnabled({
    timeout: 10_000,
  });
  await expect(page.getByText("正在继续", { exact: false })).toHaveCount(0);
});

test("sidebar current conversation returns to the conversation just used", async ({ page }) => {
  const marker = `当前会话 ${Date.now()}`;
  await page.locator('textarea[aria-label="输入消息"]').fill(marker);
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page).toHaveURL(/\/interact\/text\/c_/, { timeout: 15_000 });
  // Wait for the thread itself (not the sidebar row) so the conversation page
  // has loaded and remembered the surface before navigating away.
  await expect(page.locator(".gpa-thread")).toContainText(marker, {
    timeout: 20_000,
  });
  const conversationId = page.url().split("/").pop();

  await page.getByRole("link", { name: "历史会话", exact: true }).click();
  await expect(page).toHaveURL(/\/history/);
  await page.getByRole("button", { name: "当前会话", exact: true }).click();
  await expect(page).toHaveURL(
    new RegExp(`/interact/text/${conversationId}$`),
    { timeout: 15_000 },
  );
  await expect(page.getByText(marker, { exact: true }).last()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "当前会话", exact: true }),
  ).toHaveAttribute("aria-current", "page");

  // Switching the same conversation to voice is remembered as the current
  // surface even though no voice turn exists yet.
  await page.getByRole("button", { name: "语音", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/interact/voice/${conversationId}`));
  // Wait until the voice surface itself has loaded the conversation (its
  // title replaces the fallback), which is when the visited channel is
  // remembered. Scoping to the voice page avoids matching the old text page
  // during the route swap.
  await expect(page.locator(".gpa-page--voice .gpa-header__title")).toHaveText(
    marker,
    { timeout: 10_000 },
  );
  await page.getByRole("link", { name: "历史会话", exact: true }).click();
  await page.getByRole("button", { name: "当前会话", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/interact/voice/${conversationId}$`), {
    timeout: 15_000,
  });
});

test("current conversation keeps the voice channel of a voice conversation", async ({ page }) => {
  const title = `当前语音 ${Date.now()}`;
  const created = await page.request.post("/api/conversations", {
    data: { title, channel: "voice" },
  });
  expect(created.ok()).toBeTruthy();
  const cid = (await created.json()).conversationId;
  try {
    const submitted = await page.request.post(`/api/conversations/${cid}/messages`, {
      data: { content: "当前语音消息", channel: "voice" },
    });
    expect(submitted.ok()).toBeTruthy();

    await page.goto(`/interact/voice/${cid}`);
    await expect(page.getByRole("heading", { name: title })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.locator(".gpa-page--voice")).toBeVisible();

    await page.goto("/history");
    await page.getByRole("button", { name: "当前会话", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/interact/voice/${cid}$`), {
      timeout: 15_000,
    });
    await expect(page.locator(".gpa-page--voice")).toBeVisible();

    // Clicking the entry while already inside the conversation is a no-op.
    await page.getByRole("button", { name: "当前会话", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/interact/voice/${cid}$`));
  } finally {
    await page.request.delete(`/api/conversations/${cid}`).catch(() => undefined);
  }
});

test("history opens a voice-created conversation in text", async ({ page }) => {
  const title = `语音历史 ${Date.now()}`;
  const created = await page.request.post("/api/conversations", {
    data: { title, channel: "voice" },
  });
  expect(created.ok()).toBeTruthy();
  const cid = (await created.json()).conversationId;
  const submitted = await page.request.post(`/api/conversations/${cid}/messages`, {
    data: { content: "语音历史消息", channel: "voice" },
  });
  expect(submitted.ok()).toBeTruthy();

  await page.goto("/history");
  const card = page.locator(".gpa-history-card", { hasText: title }).first();
  await expect(card).toBeVisible({ timeout: 15_000 });
  await expect(card.getByText("语音", { exact: true })).toBeVisible();
  await card.locator(".gpa-history-card__main").click();
  await expect(page).toHaveURL(new RegExp(`/interact/text/${cid}`));
});

test("history search queries the server, not only the first page", async ({ page }) => {
  const title = `搜索验收 ${Date.now()}`;
  const created = await page.request.post("/api/conversations", {
    data: { title, channel: "text" },
  });
  const cid = (await created.json()).conversationId;
  try {
    await page.goto("/history");
    await page.getByLabel("搜索历史会话").fill(title);
    await expect(page.locator(".gpa-history-card")).toHaveCount(1, {
      timeout: 15_000,
    });
    await expect(page.locator(".gpa-history-card")).toContainText(title);
  } finally {
    await page.request.delete(`/api/conversations/${cid}`).catch(() => undefined);
  }
});

test("new conversation entry starts a fresh text surface", async ({ page }) => {
  const marker = `新建验收 ${Date.now()}`;
  await page.locator('textarea[aria-label="输入消息"]').fill(marker);
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page).toHaveURL(/\/interact\/text\/c_/, { timeout: 15_000 });

  await page.getByRole("link", { name: "新建会话", exact: true }).click();
  await expect(page).toHaveURL(/\/interact\/text$/);
  await expect(page.locator('textarea[aria-label="输入消息"]')).toBeVisible();
});

test("history loads beyond the first page", async ({ page }) => {
  const prefix = `分页验收 ${Date.now()}`;
  const ids = [];
  try {
    for (let index = 0; index < 31; index += 1) {
      const response = await page.request.post("/api/conversations", {
        data: { title: `${prefix}-${index}`, channel: "text" },
      });
      expect(response.ok()).toBeTruthy();
      ids.push((await response.json()).conversationId);
    }
    await page.goto("/history");
    const more = page.getByRole("button", { name: "加载更多" });
    await expect(more).toBeVisible({ timeout: 15_000 });
    const before = await page.locator(".gpa-history-card").count();
    await more.click();
    await expect
      .poll(async () => page.locator(".gpa-history-card").count())
      .toBeGreaterThan(before);
  } finally {
    for (const id of ids) {
      await page.request.delete(`/api/conversations/${id}`).catch(() => undefined);
    }
  }
});

test("mobile rail keeps the interaction reachable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/interact/text");
  await expect(page.getByRole("heading", { name: "文本交互" })).toBeVisible();
  await expect(page.locator('textarea[aria-label="输入消息"]')).toBeVisible();
});

test("late send in another conversation cannot clear the new draft", async ({ page }) => {
  const markerA = `迟到发送 A ${Date.now()}`;
  const markerB = `迟到发送 B ${Date.now()}`;
  const createdA = await page.request.post("/api/conversations", {
    data: { title: markerA, channel: "text", draft: false },
  });
  const createdB = await page.request.post("/api/conversations", {
    data: { title: markerB, channel: "text", draft: false },
  });
  expect(createdA.ok()).toBeTruthy();
  expect(createdB.ok()).toBeTruthy();
  const idA = (await createdA.json()).conversationId;
  const idB = (await createdB.json()).conversationId;

  await page.goto(`/interact/text/${idA}`);
  await expect(page.locator('textarea[aria-label="输入消息"]')).toBeVisible();

  let intercepted = false;
  await page.route(new RegExp(`/api/conversations/${idA}/messages$`), async (route) => {
    intercepted = true;
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await route.continue();
  });
  await page.locator('textarea[aria-label="输入消息"]').fill("A 迟到消息");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await page.locator(".gpa-rail__item", { hasText: markerB }).first().click();
  await expect(page).toHaveURL(new RegExp(`/interact/text/${idB}$`));
  const box = page.locator('textarea[aria-label="输入消息"]');
  // Let the conversation-change reset effect settle before typing.
  await page.waitForTimeout(350);
  await box.fill("B 新草稿");
  await expect.poll(() => intercepted).toBe(true);
  await page.waitForTimeout(1600);
  await expect(box).toHaveValue("B 新草稿");
});
