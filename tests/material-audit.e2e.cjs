/* eslint-disable @typescript-eslint/no-require-imports */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const { existsSync } = require("node:fs");

let testBrowser;

(async () => {
  const managedChromium = chromium.executablePath();
  const macChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const executablePath = process.env.PLAYWRIGHT_CHROME_PATH
    || (existsSync(managedChromium) ? managedChromium : macChrome);
  const browser = await chromium.launch({
    headless: true,
    // CI 优先使用 Playwright 管理的 Chromium；本机缺包时再使用已安装 Chrome。
    executablePath,
  });
  testBrowser = browser;
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  // 真实模型意图、改写、Grounding 与 Agent Loop 都通过网络调用；E2E 不以
  // 云端瞬时延迟作为产品失败，业务断言仍在各阶段完成后执行。
  page.setDefaultTimeout(120000);
  page.setDefaultNavigationTimeout(180000);
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  const assertOneScreen = async (label) => {
    const metrics = await page.evaluate(() => ({
      innerHeight: window.innerHeight,
      scrollHeight: document.documentElement.scrollHeight,
      bodyScrollHeight: document.body.scrollHeight,
    }));
    assert.ok(metrics.scrollHeight <= metrics.innerHeight + 1, `${label}: html scrolls ${JSON.stringify(metrics)}`);
    assert.ok(metrics.bodyScrollHeight <= metrics.innerHeight + 1, `${label}: body scrolls ${JSON.stringify(metrics)}`);
  };

  await page.goto(process.env.TEST_WEB_URL || "http://localhost:3000", { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: /材料齐套审核的三个架构版本/ }).waitFor();
  await assertOneScreen("architecture-v1");
  await page.screenshot({ path: "/tmp/argus-architecture-v1.png" });

  await page.getByRole("button", { name: /生成下一版/ }).click();
  await page.getByText("两个决策 Agent 的生命周期位置", { exact: true }).waitFor();
  await page.getByRole("button", { name: /生成下一版/ }).click();
  await page.getByText("共享异常取证恢复与精确回程", { exact: true }).waitFor();
  await page.getByText(/回到候选构建/).waitFor();
  await page.getByText(/已解决 → 按 Return Target 返回原 Task/).waitFor();
  await page.getByText(/未收敛 → Checkpoint \+ HITL/).waitFor();
  await page.waitForTimeout(600);
  await assertOneScreen("architecture-v3");
  await page.screenshot({ path: "/tmp/argus-architecture-v3.png" });

  await page.getByRole("button", { name: /材料知识库/ }).click();
  await page.getByText("双路召回与排序", { exact: true }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: "/tmp/argus-knowledge-debug.png" });
  if (await page.locator(".knowledge-error").count()) {
    throw new Error(`knowledge error: ${await page.locator(".knowledge-error").innerText()}`);
  }
  await page.getByText("KB-NJ-MARRIAGE-STATUS", { exact: true }).first().waitFor();
  await page.locator(".citation-list article code").filter({ hasText: /^CHILD-/ }).first().waitFor();
  await page.getByText("Metadata Pre-filter", { exact: true }).waitFor();
  await assertOneScreen("knowledge-base");
  await page.screenshot({ path: "/tmp/argus-knowledge-base.png" });

  await page.getByLabel("知识库问题").fill("帮我判断这笔贷款能批多少额度");
  await page.getByRole("button", { name: "检索证据" }).click();
  await page.getByText(/REFUSE ·/).waitFor();
  await page.getByText(/不判断贷款审批、额度、利率、估值或风险/).waitFor();
  assert.equal(await page.locator(".retrieval-hit").count(), 0, "refused query must not retrieve");

  await page.getByRole("button", { name: /对比南京、北京、广州的公积金贷款/ }).click();
  await page.getByText("KB-BJ-MARRIAGE-STATUS", { exact: true }).first().waitFor();
  await page.getByText("KB-GZ-MARRIAGE-STATUS", { exact: true }).first().waitFor();

  await page.getByRole("button", { name: /架构演进/ }).click();

  await page.getByRole("button", { name: /进入材料审核工作台/ }).click();
  await page.getByRole("button", { name: /开始材料审核/ }).waitFor();
  await page.getByText("216/216 页", { exact: true }).waitFor();
  await assertOneScreen("workbench-ready");
  await page.screenshot({ path: "/tmp/argus-workbench-ready.png" });

  await page.getByRole("button", { name: /开始材料审核/ }).click();
  await page.getByRole("dialog").waitFor({ timeout: 120000 });
  await page.getByText("审核任务账本", { exact: true }).waitFor();
  await page.getByText("进件事实关联 Agent", { exact: true }).first().waitFor();
  await page.getByText("材料语义仲裁 Agent", { exact: true }).first().waitFor();
  await page.getByText("异常取证恢复子 Agent", { exact: true }).first().waitFor();
  await page.getByText("异常取证精确回程", { exact: true }).waitFor();
  // 模型可先请求关联确认，也可直接进入材料归属确认；两条都是合法受控路径。
  let supplementReached = false;
  for (let step = 0; step < 8; step += 1) {
    const dialog = page.getByRole("dialog");
    await dialog.waitFor({ timeout: 120000 });
    const supplement = dialog.getByRole("button", { name: "发起补件单" });
    if (await supplement.isVisible()) {
      await supplement.click();
      supplementReached = true;
      break;
    }
    const association = dialog.getByRole("button", { name: "确认证据闭合" });
    const owner = dialog.getByRole("button", { name: /确认归属于/ });
    const image = dialog.getByRole("button", { name: "确认识别结果" });
    let handled;
    if (await association.isVisible()) handled = association;
    else if (await owner.isVisible()) handled = owner;
    else if (await image.isVisible()) handled = image;
    else throw new Error(`unexpected HITL branch: ${await dialog.innerText()}`);
    await handled.click();
    // 等待本次 Command(resume) 完成，而不是用固定睡眠猜测后端耗时。
    await handled.waitFor({ state: "hidden", timeout: 120000 });
  }
  assert.equal(supplementReached, true, "controlled HITL branches must converge to supplement request");

  await page.getByRole("heading", { name: "补件已到件" }).waitFor({ timeout: 120000 });
  await page.getByRole("button", { name: "登记补件到件" }).click();
  await page.getByText("材料已齐套", { exact: true }).waitFor({ timeout: 120000 });
  await page.getByText(/选择性 Replan · P\d+ → P\d+/).waitFor();
  await page.getByText(/失效并重跑/).first().waitFor();
  await page.getByText(/命中变化事实/).first().waitFor();
  await assertOneScreen("workbench-complete");
  await page.screenshot({ path: "/tmp/argus-workbench-complete.png" });

  await page.locator(".checklist-item", { hasText: "REQ-SPOUSE-CONSENT" }).click();
  await page.getByRole("button", { name: "查看补件依据" }).click();
  await page.getByRole("heading", { name: "补件依据 RAG" }).waitFor();
  await page.getByText(/最终选中/).waitFor();
  await page.waitForTimeout(600);
  await page.screenshot({ path: "/tmp/argus-requirement-rag.png" });

  const actionableErrors = consoleErrors.filter((message) => !message.includes("favicon"));
  assert.deepEqual(actionableErrors, [], `console errors: ${actionableErrors.join("\n")}`);
  console.log(JSON.stringify({
    status: "passed",
    screenshots: [
      "/tmp/argus-architecture-v1.png",
      "/tmp/argus-architecture-v3.png",
      "/tmp/argus-knowledge-base.png",
      "/tmp/argus-workbench-ready.png",
      "/tmp/argus-workbench-complete.png",
      "/tmp/argus-requirement-rag.png",
    ],
  }));
  await browser.close();
  testBrowser = undefined;
})().catch((error) => {
  console.error(error);
  void testBrowser?.close();
  process.exitCode = 1;
});
