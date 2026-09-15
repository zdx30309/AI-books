/* Deterministic browser smoke. API requests are intercepted; no production writes. */
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require("playwright");

const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const base = option("--base", "http://127.0.0.1:4176");
const fixturePath = path.resolve(option("--fixture", ".local/general_longform_integration/browser/browser_fixture.json"));
const output = path.dirname(fixturePath);
const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
const checks = [];

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
    await context.addInitScript(() => localStorage.setItem("scriptbook.token", "local-browser-fixture-not-a-real-token"));
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    let state = structuredClone(fixture.initial);
    const actions = [];
    const creates = [];
    const manifestRequests = [];
    let manifestFailure = false;
    const task = () => ({ id: fixture.task_id, source_url: "manual://general_longform", category: "general_longform", category_label: "通用长篇", title: "通用长篇测试", status: state.task_status, progress: state.progress, current_step: state.current_step, config: {}, created_at: "2026-09-14T08:00:00Z", bookshelf: "通用长篇", aigc_required: false });
    await page.route("**/api/v1/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const p = url.pathname;
      const json = (data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
      if (p.endsWith("/auth/me")) return json({ id: "00000000-0000-4000-8000-000000000001", email: "fixture@example.org", nickname: "界面测试" });
      if (p.endsWith("/tasks/categories")) return json({ items: fixture.categories });
      if (p.endsWith("/prompt-manifest")) {
        manifestRequests.push(url.searchParams.get("version"));
        if (manifestFailure) return json({ detail: "版本清单读取测试错误" }, 503);
        return json(url.searchParams.get("version") === fixture.legacy_manifest.version ? fixture.legacy_manifest : fixture.manifest);
      }
      if (p.endsWith("/general-longform/" + fixture.task_id)) return json(state);
      if (p.endsWith("/general-longform/" + fixture.task_id + "/actions")) {
        const body = request.postDataJSON(); actions.push(body);
        if (body.action === "confirm_outline") state = structuredClone(fixture.confirmed);
        if (body.action === "next_chapter") state = structuredClone(fixture.one_chapter);
        if (body.action === "import_materials") state.materials.forEach((m) => { m.imported = true; });
        return json({ task_id: fixture.task_id, request_id: body.request_id || "fixture", status: "paused" });
      }
      if (p.endsWith("/general-longform/" + fixture.task_id + "/materials")) {
        state.materials.push({ id: "material", filename: "编辑反馈.txt", role: "report", imported: false, characters: 12, sha256: "a".repeat(64), confirmed: false });
        return json({ status: "uploaded", id: "material", characters: 12 });
      }
      if (p.endsWith("/chapters/1/content")) return json({ kind: "accepted", version: 1, content: fixture.chapter_content });
      if (p.includes("/artifacts/")) {
        const file = fixture.files[p.split("/").pop()];
        if (!file) return json({ detail: "missing artifact" }, 404);
        return route.fulfill({ status: 200, headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(file.filename)}` }, body: Buffer.from(file.base64, "base64") });
      }
      if (p === "/api/v1/tasks" && request.method() === "POST") { creates.push(request.postDataJSON()); return json({ task_id: fixture.task_id, status: "pending" }, 201); }
      if (p === "/api/v1/tasks") return json({ items: [], total: 0, page: 1, page_size: 0 });
      if (p.endsWith("/tasks/" + fixture.task_id)) return json(task());
      if (p.endsWith("/chapters")) return json({ task_id: fixture.task_id, chapters: [] });
      if (p.endsWith("/classical-classification/default") || p.endsWith("/classical-reference-rewrite/default-reference")) return json({ enabled: false });
      return json({ detail: "No fixture for " + p }, 404);
    });
    await page.goto(base + "/bookshelf");
    await page.getByRole("button", { name: "新建书稿", exact: true }).first().click();
    assert.equal(await page.locator("#task-category").inputValue(), "social_science");
    await page.locator("#task-category").selectOption("general_longform");
    await page.getByText(/840 段完整保留/).waitFor();
    assert.equal(await page.getByRole("button", { name: "候选库", exact: true }).isDisabled(), true);
    await page.getByLabel("已有材料：先创建工作区，暂不调用模型").check();
    await page.getByRole("button", { name: "创建通用长篇工作区", exact: true }).click();
    await page.getByTestId("general-longform-workspace").waitFor();
    assert.equal(creates[0].config.category, "general_longform");
    assert.equal(creates[0].config.general_longform_import_first, true);
    checks.push("category creation, unchanged default, no-input import-first, batch isolation");
    assert.equal(await page.getByRole("radio").count(), 5);
    await page.screenshot({ path: path.join(output, "workspace-outline.png"), fullPage: true });
    await page.getByRole("button", { name: "确认选中包装与大纲", exact: true }).click();
    await page.getByRole("button", { name: "研究并写作第1章", exact: true }).waitFor();
    assert.equal(Object.keys(state.chapters).length, 0);
    assert.equal(actions.length, 1);
    await page.getByRole("button", { name: "研究并写作第1章", exact: true }).click();
    await page.getByRole("button", { name: "研究并写作第2章", exact: true }).waitFor();
    assert.deepEqual(Object.keys(state.chapters), ["1"]);
    checks.push("five packaging choices, confirmation stops, explicit one-chapter operation");
    for (const name of ["规划", "章节", "材料", "文件", "研究", "日志"]) {
      await page.getByRole("tab", { name, exact: true }).click();
      assert.equal(await page.getByRole("tab", { name, exact: true }).getAttribute("aria-selected"), "true");
    }
    checks.push("six workspace tabs");
    await page.getByTestId("longform-reference-roles").waitFor();
    await page.getByText("840 个原段落 · 23 个完整分段 · 五份附录完整保留", { exact: true }).waitFor();
    for (const example of fixture.manifest.reference_examples) {
      await page.getByText(new RegExp(`${example.role === "positive" ? "正面样稿" : "反面样稿"}.*${example.title}`)).waitFor();
    }
    assert.ok(manifestRequests.includes(fixture.manifest.version));
    await page.screenshot({ path: path.join(output, "workspace-v02-roles.png"), fullPage: true });
    checks.push("v02 840 paragraphs / 23 sections, explicit positive-negative roles, historical-only reports");
    await page.getByRole("tab", { name: "章节", exact: true }).click();
    await page.getByTestId("longform-style-review").getByText("正反方法与语境复核", { exact: true }).click();
    await page.getByText(/普通词语境判断：未采用机械禁词/).waitFor();
    await page.getByText("历史样稿报告未作为当前稿件的检测结果。", { exact: true }).waitFor();
    checks.push("chapter style evidence, role-specific checks and contextual ordinary-word review");
    await page.getByRole("button", { name: "查看验收稿 v1", exact: true }).click();
    await page.getByText("第1章 · 验收正文 v1", { exact: true }).waitFor();
    await page.screenshot({ path: path.join(output, "workspace-chapter.png"), fullPage: true });
    checks.push("accepted chapter preview with literal company headings");
    await page.getByRole("tab", { name: "材料", exact: true }).click();
    await page.getByLabel("选择通用长篇材料").setInputFiles({ name: "编辑反馈.txt", mimeType: "text/plain", buffer: Buffer.from("核对第三段中的条件范围") });
    await page.getByRole("button", { name: "上传 1 份材料", exact: true }).click();
    await page.getByText("编辑反馈.txt", { exact: true }).waitFor();
    await page.getByRole("button", { name: "识别材料并继续当前阶段", exact: true }).click();
    await page.getByText(/检测 \/ 编辑反馈 · 12 字符 · 已处理/).waitFor();
    checks.push("material upload and progress-import action");
    await page.getByRole("tab", { name: "文件", exact: true }).click();
    const downloaded = page.waitForEvent("download");
    await page.getByRole("button", { name: /^下载 / }).first().click();
    const download = await downloaded;
    assert.match(download.suggestedFilename(), /\d{2}时\d{2}分\d{2}秒\.docx$/);
    await download.saveAs(path.join(output, "downloaded.docx"));
    assert.equal(fs.readFileSync(path.join(output, "downloaded.docx")).subarray(0, 2).toString(), "PK");
    await page.screenshot({ path: path.join(output, "workspace-files.png"), fullPage: true });
    checks.push("real DOCX browser download with Beijing second-resolution name");
    state = structuredClone(fixture.one_chapter);
    state.phase = "needs_review";
    state.chapters["1"].candidate = structuredClone(state.chapters["1"].accepted);
    state.chapters["1"].accepted = null;
    state.chapters["1"].candidate.audit.style_review = { positive_application: [null], negative_checks: "malformed", ordinary_word_review: { examples: [{ chapter_number: { invalid: true }, evidence: { invalid: true } }] } };
    await page.reload();
    await page.getByTestId("general-longform-workspace").waitFor();
    await page.getByRole("tab", { name: "章节", exact: true }).click();
    await page.getByTestId("longform-style-review").getByText("正反方法与语境复核", { exact: true }).click();
    await page.getByText(/普通词语境判断：待复核/).waitFor();
    checks.push("malformed model style review stays inspectable without crashing the workspace");
    state = structuredClone(fixture.legacy_project);
    await page.reload();
    await page.getByTestId("general-longform-workspace").waitFor();
    await page.getByRole("tab", { name: "日志", exact: true }).click();
    await page.getByText("571 个原段落 · 19 个完整分段 · 五份附录完整保留", { exact: true }).waitFor();
    await page.getByText(/本项目继续使用原固定版本/).waitFor();
    assert.ok(manifestRequests.includes(fixture.legacy_manifest.version));
    assert.equal(await page.getByTestId("longform-reference-roles").count(), 0);
    await page.screenshot({ path: path.join(output, "workspace-v16-pinned.png"), fullPage: true });
    checks.push("old project displays actual pinned v16 manifest without silently migrating to v02");
    manifestFailure = true;
    await page.reload();
    await page.getByTestId("general-longform-workspace").waitFor();
    await page.getByRole("tab", { name: "日志", exact: true }).click();
    await page.getByText("版本清单读取测试错误", { exact: true }).waitFor();
    assert.equal(await page.getByText(/个原段落 · .*个完整分段/).count(), 0);
    checks.push("manifest error stays visible without fabricated fallback counts");
    assert.deepEqual(errors, []);
    const result = { result: "pass", simulation: true, base, checks, page_errors: errors };
    fs.writeFileSync(path.join(output, "browser_report.json"), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
