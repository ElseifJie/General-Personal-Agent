import { expect, test } from "@playwright/test";

// Shell furniture that must survive a resized artifact rail: the docked panel
// stays closed while there is nothing to show, both rails drag/keyboard-resize
// and remember their width, and a long conversation title never pushes the
// header into a second line.
const PIN = process.env.WB_E2E_PIN || process.env.DEMO_LOGIN_PIN || "";

test.use({ viewport: { width: 1440, height: 900 } });

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

async function createDraft(page, title) {
  const response = await page.request.post("/api/conversations", {
    data: { title, channel: "text", draft: true },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).conversationId;
}

async function dropConversation(page, conversationId) {
  await page.request.delete(`/api/conversations/${conversationId}`);
}

async function railWidth(page) {
  return page.locator("#gpa-artifact-rail").evaluate((el) => el.getBoundingClientRect().width);
}

test("the artifact rail starts closed, resizes and remembers its width", async ({ page }) => {
  const conversationId = await createDraft(page, "侧栏宽度验收");
  try {
    await page.goto(`/interact/text/${conversationId}`);
    const toggle = page.getByRole("button", { name: "产物", exact: true });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("#gpa-artifact-rail")).toHaveCount(0);

    await toggle.click();
    const separator = page.getByRole("separator", { name: "调整侧栏宽度" });
    await expect(separator).toHaveAttribute("aria-orientation", "vertical");
    const start = await railWidth(page);

    // Dragging the leading edge left widens the panel.
    const box = await separator.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + 200);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 - 120, box.y + 200, { steps: 8 });
    await page.mouse.up();
    await expect.poll(() => railWidth(page)).toBeGreaterThan(start + 100);

    // The arrow keys nudge it in 24px steps, like the navigation rail.
    const dragged = await railWidth(page);
    await separator.focus();
    await page.keyboard.press("ArrowLeft");
    await expect.poll(() => railWidth(page)).toBe(dragged + 24);

    // A reload starts with the panel closed again but keeps the chosen width.
    const remembered = await railWidth(page);
    await page.reload();
    await expect(page.locator("#gpa-artifact-rail")).toHaveCount(0);
    await page.getByRole("button", { name: "产物", exact: true }).click();
    await expect.poll(() => railWidth(page)).toBe(remembered);
  } finally {
    await dropConversation(page, conversationId);
  }
});

test("the navigation rail still resizes after the shared handle refactor", async ({ page }) => {
  const separator = page.getByRole("separator", { name: "调整导航宽度" });
  await expect(separator).toBeVisible();
  const navWidth = () =>
    page.locator(".ui-app-frame__left").evaluate((el) => el.getBoundingClientRect().width);
  const start = await navWidth();

  await separator.focus();
  await page.keyboard.press("ArrowRight");
  await expect.poll(navWidth).toBe(start + 24);
  await page.keyboard.press("Home");
  await expect.poll(navWidth).toBe(264);

  await page.reload();
  await expect.poll(navWidth).toBe(264);
});

test("the header keeps the title on one line, beside the actions", async ({ page }) => {
  // 51 chars: long enough that the overflow assertion is not decided by a few
  // pixels of font-metric drift between machines.
  const title =
    "总结一下供应商数据抓取结果被替换成维护页面的最终结论和可执行的改进建议清单以及后续跟进事项与责任人安排";
  const conversationId = await createDraft(page, title);
  try {
    await page.goto(`/interact/text/${conversationId}`);
    // The status/connection/updated line is gone from the text header.
    await expect(page.locator(".gpa-header__meta")).toHaveCount(0);

    const heading = page.getByRole("heading", { name: title });
    await expect(heading).toHaveAttribute("title", title);
    await expect(heading).toHaveCSS("white-space", "nowrap");
    const metrics = await heading.evaluate((el) => ({
      width: el.getBoundingClientRect().width,
      height: el.getBoundingClientRect().height,
      overflows: el.scrollWidth > el.clientWidth,
    }));
    // One 27px line, ellipsized rather than wrapped onto a second one.
    expect(metrics.height).toBeLessThan(30);
    expect(metrics.overflows).toBe(true);

    // Title and action row share one centre line.
    const centres = await page.evaluate(() => {
      const centre = (selector) => {
        const el = document.querySelector(selector);
        const box = el.getBoundingClientRect();
        return (box.top + box.bottom) / 2;
      };
      return {
        title: centre(".gpa-header__title"),
        actions: centre(".gpa-header__actions"),
      };
    });
    expect(Math.abs(centres.title - centres.actions)).toBeLessThan(1);
  } finally {
    await dropConversation(page, conversationId);
  }
});
