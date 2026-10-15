const assert = require("node:assert/strict");
const { chromium } = require("playwright");

const base = process.env.FRONTEND_URL || "http://127.0.0.1:4176";
const token = "frontend-fixture-not-a-real-token";
const taskId = "0dd779ff-4b7f-4486-ad56-27269aadb3f0";
const hash = "0123456789abcdef".repeat(4);
const ready = { status: "ready", chapter_number: 8, body_words: 21400, result_sha256: hash };
const checks = [];

async function main() {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}),
  });
  try {
    const context = await browser.newContext({ acceptDownloads: true });
    await context.addInitScript((value) => localStorage.setItem("scriptbook.token", value), token);
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    const pageErrors = [];
    page.on("pageerror", (failure) => pageErrors.push(failure.message));
    let scenario;
    let requests;
    const panel = page.getByRole("region", { name: "局部篇幅人工批准" });
    const approval = panel.getByRole("button", { name: "确认批准此哈希并恢复（可能产生费用）", exact: true });
    const json = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    await page.route("**/api/v1/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const pathname = url.pathname;
      requests.push({ pathname, method: request.method(), headers: request.headers(), search: url.search, body: request.postData() });
      if (pathname.endsWith("/auth/me")) return json(route, { id: "fixture-user", email: "fixture@example.org", nickname: "前端检查" });
      if (pathname.endsWith("/tasks/categories")) return json(route, { items: [] });
      if (pathname.endsWith("/length-repair")) {
        if (request.method() === "POST") {
          assert.deepEqual(request.postDataJSON(), { result_sha256: scenario.repair.result_sha256 });
          await new Promise((resolve) => setTimeout(resolve, 150));
          if (scenario.postStatus) return json(route, { detail: "修复稿哈希已变化，请重新核验" }, scenario.postStatus);
          scenario.status = "pending";
          return json(route, { task_id: taskId, status: "pending" });
        }
        if (scenario.getDelay) await new Promise((resolve) => setTimeout(resolve, scenario.getDelay));
        if (scenario.getStatus) return json(route, { detail: "修复状态接口暂不可用" }, scenario.getStatus);
        return json(route, scenario.repair);
      }
      if (pathname.endsWith("/corrections.xlsx")) {
        if (scenario.downloadStatus) return json(route, { detail: "纠错文件不可用" }, scenario.downloadStatus);
        return route.fulfill({
          status: 200,
          contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          headers: { "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent("纠错清单.xlsx")}` },
          body: Buffer.from("mock-xlsx-download-not-a-real-workbook"),
        });
      }
      if (pathname === `/api/v1/tasks/${taskId}`) return json(route, {
        id: taskId,
        source_url: "https://example.org/frontend-fixture",
        status: scenario.status,
        progress: 50,
        current_step: "已保存初稿",
        title: "前端离线模拟（不代表真实任务状态）",
        category: "general_short",
        cover_url: null,
        cover_status: null,
        aigc_required: false,
        aigc_report_available: false,
        error_message: scenario.reason,
        created_at: "2026-10-14T00:00:00Z",
      });
      if (pathname.endsWith("/chapters")) return json(route, { chapters: [] });
      if (pathname.endsWith("/similarity-report/data")) return json(route, { applicable: false });
      if (pathname.includes("/stages/")) return json(route, { content: "" });
      if (request.method() !== "GET") throw new Error(`Unexpected write: ${request.method()} ${pathname}`);
      return json(route, {});
    });

    async function open(overrides = {}) {
      scenario = { status: "failed", reason: "第8章超长，需局部篇幅压缩", repair: { status: "missing", reason: "尚无有效压缩稿" }, ...overrides };
      requests = [];
      const repairResponse = ["failed", "manual_review", "paused"].includes(scenario.status)
        ? page.waitForResponse((response) => response.url().includes("/length-repair"))
        : null;
      await page.goto(`${base}/book/${taskId}`);
      await page.getByRole("heading", { name: "前端离线模拟（不代表真实任务状态）", exact: true }).waitFor();
      if (["failed", "manual_review", "paused"].includes(scenario.status)) {
        await repairResponse;
        await page.waitForFunction(() => !document.body.textContent.includes("正在核验局部压缩稿"));
      }
    }

    const posts = () => requests.filter((request) => request.pathname.endsWith("/length-repair") && request.method === "POST");
    const getRepairs = () => requests.filter((request) => request.pathname.endsWith("/length-repair") && request.method === "GET");

    await open();
    await panel.getByText(/尚无可批准的局部压缩稿/).waitFor();
    assert.equal(await approval.count(), 0);
    assert.equal(posts().length, 0);
    checks.push("0dd779ff-shaped missing fixture never claims ready or approves");

    await open({ reason: "视频源暂时不可用", repair: { status: "missing", reason: "irrelevant" } });
    assert.ok(getRepairs().length >= 1);
    assert.equal(await panel.count(), 0);
    assert.equal(await page.getByText("irrelevant", { exact: true }).count(), 0);
    checks.push("unrelated failed task is queried without irrelevant approval panel");

    for (const status of ["failed", "manual_review", "paused"]) {
      await open({ status, repair: { status: "invalid", reason: "修复文件与原稿不匹配" } });
      await panel.getByText(/修复文件与原稿不匹配/).waitFor();
      assert.equal(await approval.count(), 0);
      assert.ok(getRepairs().length >= 1);
    }
    for (const repair of [
      { ...ready, result_sha256: undefined },
      { ...ready, result_sha256: "not-a-sha256" },
      { ...ready, chapter_number: 0 },
      { ...ready, body_words: -1 },
      { status: "unexpected" },
    ]) {
      await open({ repair });
      await panel.getByText(/不能批准/).waitFor();
      assert.equal(await approval.count(), 0);
      assert.equal(posts().length, 0);
    }
    checks.push("all three hold statuses queried; invalid/malformed metadata fail closed");

    for (const getStatus of [404, 503]) {
      await open({ getStatus });
      await panel.getByRole("alert").waitFor();
      assert.equal(await approval.count(), 0);
      scenario.getStatus = undefined;
      await panel.getByRole("button", { name: "刷新修复状态" }).click();
      await panel.getByText(/尚无可批准的局部压缩稿/).waitFor();
    }
    checks.push("unavailable GET and 404 show actionable non-approval state and retry");

    for (const status of ["processing", "completed"]) {
      await open({ status });
      assert.equal(getRepairs().length, 0);
      assert.equal(await panel.count(), 0);
    }
    checks.push("active/completed tasks do not query or expose approval");

    await open({ repair: ready });
    await panel.getByText(hash, { exact: true }).waitFor();
    await panel.getByText(/修复章号：第 8 章 · 修复稿正文 21400/).waitFor();
    await panel.getByText(/后续可能调用付费模型并产生费用/).waitFor();
    assert.equal(posts().length, 0);
    page.once("dialog", async (dialog) => {
      assert.ok(dialog.message().includes(hash));
      assert.ok(dialog.message().includes("费用"));
      await dialog.dismiss();
    });
    await approval.click();
    assert.equal(posts().length, 0);
    page.once("dialog", (dialog) => dialog.accept());
    const resumed = page.waitForResponse((response) => response.request().method() === "POST" && response.url().includes("/length-repair"));
    await approval.click();
    await resumed;
    await panel.waitFor({ state: "detached" });
    assert.equal(posts().length, 1);
    assert.equal(posts()[0].headers.authorization, `Bearer ${token}`);
    assert.deepEqual(JSON.parse(posts()[0].body), { result_sha256: hash });
    checks.push("ready displays full server hash/chapter/words/cost; only explicit confirmation POSTs once and resumes");

    await open({ reason: "其他暂停原因", repair: ready, postStatus: 409 });
    page.once("dialog", (dialog) => dialog.accept());
    await approval.click();
    await panel.getByRole("alert").waitFor();
    assert.ok((await panel.getByRole("alert").textContent()).includes("哈希已变化"));
    assert.equal(await approval.count(), 0);
    assert.equal(posts().length, 1);
    scenario.repair = { ...ready, result_sha256: "f".repeat(64) };
    await panel.getByRole("button", { name: "刷新修复状态" }).click();
    await panel.getByText("f".repeat(64), { exact: true }).waitFor();
    assert.equal(posts().length, 1);
    checks.push("409 invalidates displayed approval; refresh uses new server hash without automatic resubmit");

    await open({ status: "completed", downloadStatus: 404 });
    await page.getByRole("button", { name: "纠错 Excel", exact: true }).click();
    await page.getByText(/暂无可下载的纠错 Excel/).waitFor();
    assert.equal(await page.getByRole("button", { name: "纠错 Excel", exact: true }).isEnabled(), true);
    scenario.downloadStatus = undefined;
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "纠错 Excel", exact: true }).click();
    const download = await downloadPromise;
    assert.ok(download.url().startsWith("blob:"));
    assert.equal(download.suggestedFilename(), "纠错清单.xlsx");
    assert.equal(await download.failure(), null);
    const excelRequests = requests.filter((request) => request.pathname.endsWith("/corrections.xlsx"));
    assert.equal(excelRequests.length, 2);
    for (const request of excelRequests) {
      assert.equal(request.headers.authorization, `Bearer ${token}`);
      assert.ok(!request.search.includes("token"));
      assert.ok(!request.search.includes(token));
    }
    assert.equal(pageErrors.length, 0, pageErrors.join("\n"));
    checks.push("Excel 404 is recoverable; download uses Bearer header, Blob, UTF-8 filename, no token URL");
    console.log(JSON.stringify({ passed: checks.length, checks, pageErrors }, null, 2));
    await context.close();
  } finally {
    await browser.close();
  }
}

main().catch((failure) => { console.error(failure); process.exitCode = 1; });
