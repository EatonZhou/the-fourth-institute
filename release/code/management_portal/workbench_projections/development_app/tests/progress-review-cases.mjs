// Callable by the shared UI journey; all writes/feedback below use isolated SDK fixtures.
import assert from "node:assert/strict";
export async function runReviewCases(page, out) {
  const rows = () => page.locator("[data-item-key]");
  await page.getByRole("heading", { name: "消息外观更清楚" }).waitFor();
  assert.equal(await rows().count(), 2);
  assert.equal(
    await page
      .locator("body")
      .evaluate((e) => e.scrollWidth <= window.innerWidth),
    true,
  );
  await page.getByRole("button", { name: "打开团队对话", exact: true }).click();
  assert.equal(
    await page.evaluate(() => probe.pages[0]),
    "/workbenches/view?id=workbench_75b51c00005e&region=facade",
  );
  await page.evaluate(() => (probe.pageResult = false));
  await page.getByRole("button", { name: "打开团队对话", exact: true }).click();
  await page.getByText("当前布局未能打开入口；阅读已保留。").waitFor();
  await page.getByLabel("模块", { exact: true }).selectOption("conversation");
  assert.equal(await rows().count(), 1);
  await page.getByLabel("模块", { exact: true }).selectOption("");
  await rows().first().getByRole("button", { name: "讨论这项成果" }).click();
  await page.getByLabel("反馈内容").fill("第一项草稿");
  await page.getByRole("button", { name: "返回阅读", exact: true }).click();
  await rows().nth(1).getByRole("button", { name: "讨论这项成果" }).click();
  await page.getByLabel("反馈内容").fill("另一项草稿");
  await page.keyboard.press("Escape");
  await rows().first().getByRole("button", { name: "讨论这项成果" }).click();
  assert.equal(await page.getByLabel("反馈内容").inputValue(), "第一项草稿");
  await page.getByRole("button", { name: "提交反馈" }).click();
  await page.getByText("已取消，草稿仍保留。").waitFor();
  assert.equal(await page.getByLabel("反馈内容").inputValue(), "第一项草稿");
  const feedback = await page.evaluate(() => probe.feedback[0]);
  assert.equal(
    feedback.ref,
    await page.evaluate(() => probe.fixture.target.ref),
  );
  assert.equal(feedback.scene.content_sha256.length, 64);
  await rows().first().locator("summary").click();
  await rows()
    .first()
    .getByRole("button", { name: /提交说明/ })
    .click();
  await page.getByText("固定提交正文").waitFor();
  await page.getByLabel("反馈内容").focus();
  await page.getByLabel("反馈内容").evaluate((e) => e.setSelectionRange(1, 3));
  await page.evaluate(() => window.scrollTo(0, 300));
  await page.waitForTimeout(70);
  const before = await page.evaluate(() => ({
    y: scrollY,
    body: document.querySelector(".pDocument")?.textContent,
    draft: document.querySelector("textarea").value,
  }));
  await page.evaluate(() => probe.refresh());
  await page.waitForTimeout(100);
  assert.equal(await page.getByLabel("反馈内容").inputValue(), before.draft);
  assert.equal(await page.locator(".pDocument").textContent(), before.body);
  assert.equal(
    await page
      .getByLabel("反馈内容")
      .evaluate(
        (e) =>
          document.activeElement === e &&
          e.selectionStart === 1 &&
          e.selectionEnd === 3,
      ),
    true,
  );
  await page.evaluate(async () => {
    document.getElementById("app").style.display = "none";
    probe.runtime.hide();
    document.getElementById("app").style.display = "";
    await probe.runtime.show();
  });
  await page.waitForTimeout(100);
  assert.ok(
    Math.abs((await page.evaluate(() => scrollY)) - before.y) < 3,
    "hide/show scroll retained",
  );
  await page.screenshot({ path: out + "/reader-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(
    await page
      .locator("body")
      .evaluate((e) => e.scrollWidth <= window.innerWidth),
    true,
  );
  await page.screenshot({ path: out + "/reader-narrow.png", fullPage: true });
  await page.evaluate(async () => {
    probe.fixture.denied.add(probe.report);
    await probe.refresh();
  });
  assert.equal(await rows().count(), 0);
  assert.equal(await page.locator("textarea").count(), 0);
  assert.equal(await page.locator(".pDocument").count(), 0);
  await page.evaluate(async () => {
    probe.fixture.denied.clear();
    await probe.refresh();
  });
  await rows().first().getByRole("button", { name: "讨论这项成果" }).click();
  assert.equal(await page.getByLabel("反馈内容").inputValue(), "");
  return {
    navigation: "formal target + opened:false",
    filtering: "module scope",
    feedback: "isolated cancel, per-item draft, original ref/hash",
    refresh: "body/selection/draft/scroll retained",
    revocation: "body/draft/context cleared",
    narrow: "no overflow",
    scope: "controlled SDK only; no business sends or live Dev/TAU",
  };
}
