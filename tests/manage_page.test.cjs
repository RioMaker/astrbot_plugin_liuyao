// Run with Playwright installed: node --test tests/manage_page.test.cjs
// LIUYAO_TEST_BROWSER_CHANNEL=chrome uses an installed Chrome instead of bundled Chromium.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { before, after, test } = require("node:test");
const { chromium } = require("playwright");

const pageRoot = join(__dirname, "..", "pages", "manage");
let browser;
before(async () => {
  browser = await chromium.launch({
    headless: true,
    channel: process.env.LIUYAO_TEST_BROWSER_CHANNEL || undefined,
  });
});
after(async () => { await browser?.close(); });

// Isolate the real management page in the same sandbox as AstrBot Plugin Pages.
// Only the Bridge is replaced; the form, click handlers and DOM updates are real.
function installBridge() {
  const state = window.qa = { balance: 0, posts: [], grants: [], failAfterSave: false, block: false, downloads: [], downloadError: "", blockDownload: false };
  state.previews = [];
  state.testRuns = [];
  state.previewError = "";
  state.previewImage = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDAAAAABJRU5ErkJggg==";
  const cases = [302, 905].map((serial) => ({
    id: `LY-${serial}`, serial, caster_id: "20002", caster_name: "测试群友", group_id: "10001",
    created_at: "2026-10-25T09:40:53+08:00", question: "项目能按期完成吗？", feedback: [],
    cast: { lines: [7, 8, 9, 6, 7, 8], moving_lines: [3, 4], primary_name: "未济", changed_name: "蒙" },
  }));
  state.cases = cases;
  function user() {
    return {
      id: "20002", name: "测试用户", cast_count: 1, groups: ["10001"],
      first_cast_at: "2026-11-04T09:00:00+08:00", last_cast_at: "2026-11-04T09:00:00+08:00",
      gift_remaining: state.balance, gift_reserved: 0, daily_remaining: [0],
      daily_unlimited: false, banned: false, grants: state.grants, audit: [], revision: "test",
    };
  }
  window.AstrBotPluginPage = {
    ready: async () => ({}),
    apiGet: async (endpoint, query = {}) => {
      if (endpoint === "optimize/cases") {
        const selected = cases.filter((c) => !query.search || query.search === "#" + c.serial);
        return { page: 1, pages: 1, items: selected.map((c) => ({ ...c, primary: c.cast.primary_name })) };
      }
      if (endpoint === "manage/reading-tests") return { page: 1, pages: 1, items: [...state.testRuns].reverse() };
      if (endpoint.startsWith("manage/reading-tests/")) return state.testRuns.find((r) => r.id === Number(endpoint.split("/").pop()));
      if (endpoint === "manage/users/20002") return user();
      if (endpoint === "manage/users") return {
        items: [user()], page: 1, pages: 1, total: 1, stats: { users: 1, casts: 1, banned: 0 },
      };
      if (endpoint === "manage/cases") return { items: cases, all_count: cases.length, total: cases.length, page: 1, pages: 1 };
      const record = cases.find((item) => endpoint === `manage/cases/${item.id}`);
      if (record) return { case: record, revision: "test" };
      throw new Error(`Unexpected endpoint: ${endpoint}`);
    },
    apiPost: async (endpoint, payload) => {
      if (endpoint === "manage/reading-tests/start") {
        state.posts.push({endpoint, ...payload});
        const id = state.testRuns.length + 1;
        state.testRuns.push({
          ...payload, id, actor: "admin", at: "2026-11-23T10:00:00+08:00", status: "success",
          report: { text: "完整解读正文", verdict: "可以签约。", steps: [{
            title: "解卦模型调用", input_summary: "常用模型", output_summary: "可以签约。",
            inputs: { request: { prompt: "全文第一行\n<原始提示词>" } }, outputs: { completion_text: "模型的原始回复" },
          }] },
        });
        return { id };
      }
      if (endpoint.endsWith("/render")) {
        state.previews.push({ endpoint, payload });
        if (state.blockPreview) await new Promise((resolve) => { state.resumePreview = resolve; });
        if (state.previewError) throw new Error(state.previewError);
        return { image_data_url: state.previewImage };
      }
      state.posts.push({ endpoint, ...payload });
      if (state.block) await new Promise((resolve) => { state.resume = resolve; });
      if (endpoint !== "manage/users/20002/grant" || !/^[a-zA-Z0-9-]{16,64}$/.test(payload.request_id)) {
        throw new Error("操作编号格式无效");
      }
      if (!state.grants.some((row) => row.request_id === payload.request_id)) {
        state.balance += payload.amount;
        state.grants.push({ ...payload, actor: "admin", at: "2026-11-04T10:00:00+08:00" });
      }
      if (state.failAfterSave) {
        state.failAfterSave = false;
        throw new Error("模拟响应丢失");
      }
      return { saved: true };
    },
    download: async (endpoint, params) => {
      state.downloads.push({ endpoint, params });
      if (state.blockDownload) await new Promise((resolve) => { state.resumeDownload = resolve; });
      if (state.downloadError) throw new Error(state.downloadError);
      return { filename: "liuyao_cases_20261112_090000.zip" };
    },
  };
}

async function openPage(t, protocol = "http") {
  const context = await browser.newContext();
  t.after(() => context.close());
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await context.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const headers = { "access-control-allow-origin": "*" };
    if (path === "/") return route.fulfill({
      contentType: "text/html", body: '<iframe id="plugin" sandbox="allow-scripts allow-forms allow-downloads" src="/index.html" style="width:100%;height:95vh"></iframe>',
    });
    if (path === "/index.html") return route.fulfill({
      contentType: "text/html", headers,
      body: readFileSync(join(pageRoot, "index.html"), "utf8").replace(
        "<head>", `<head><script>(${installBridge.toString()})();</script>`,
      ),
    });
    if (["/app.js", "/workshop-tab.js", "/reading-test.js", "/style.css"].includes(path)) return route.fulfill({
      contentType: path.endsWith(".js") ? "text/javascript" : "text/css", headers,
      body: readFileSync(join(pageRoot, path.slice(1)), "utf8"),
    });
    return route.abort();
  });
  await page.goto(`${protocol}://liuyao.test/`);
  const frame = page.frames().find((item) => item.url().endsWith("/index.html"));
  await frame.getByRole("button", { name: "管理用户", exact: true }).click();
  await frame.getByLabel("赠送次数", { exact: true }).waitFor();
  return { page, frame, errors };
}

async function waitNotice(frame, text, errors) {
  try {
    await frame.waitForFunction(
      (text) => document.getElementById("detail-notice").textContent.includes(text), text,
      { timeout: 3000 },
    );
  } catch (error) {
    assert.fail(`${text}: ${errors.join("; ") || error.message}`);
  }
}

test("reading test tab runs all three sources and expands the original inputs and outputs", async (t) => {
  const { page, frame, errors } = await openPage(t);
  await frame.locator("#close-detail").click();
  await page.setViewportSize({width: 390, height: 840});
  await frame.getByRole("button", {name: "解卦测试", exact: true}).click();
  await frame.getByLabel("查找卦例").fill("#905");
  await frame.getByLabel("选择卦例").selectOption("905");
  await frame.getByRole("button", {name: "开始测试"}).click();
  const report = frame.locator("#test-report");
  await report.getByRole("heading", {name: "测试 #1 · 已解卦"}).waitFor();
  assert.equal(await report.getByText("模型的原始回复", {exact: true}).isVisible(), false);
  await report.getByText("完整输入与输出", {exact: true}).click();
  assert.match(await report.innerText(), /全文第一行\n<原始提示词>/);
  assert.equal(await report.getByText("模型的原始回复", {exact: true}).isVisible(), true);
  const select = frame.getByLabel("卦盘来源");
  await select.selectOption("manual");
  await frame.getByLabel("六爻（初爻到上爻）").fill("789678");
  await frame.getByLabel("起卦时间（北京时间，可留空）").fill("2026-10-22T08:30");
  await frame.getByLabel("测试问题").fill("能否按期完成？");
  await frame.locator("#test-start").click();
  await report.getByRole("heading", {name: "测试 #2 · 已解卦"}).waitFor();
  await select.selectOption("random");
  assert.match(await frame.getByLabel("测试问题").inputValue(), /本月底/);
  await frame.locator("#test-start").click();
  await report.getByRole("heading", {name: "测试 #3 · 已解卦"}).waitFor();
  await frame.locator("#test-history-list button").last().click();
  await report.getByRole("heading", {name: "测试 #1 · 已解卦"}).waitFor();
  const posts = await frame.evaluate(() => qa.posts);
  assert.deepEqual(posts.map((p) => p.source), ["case", "manual", "random"]);
  assert.equal(posts[0].serial, "905");
  assert.equal(posts[1].cast_at, "2026-10-22T08:30");
  assert.equal(posts[1].lines, "789678");
  assert.equal("serial" in posts[2] || "lines" in posts[2], false);
  assert.equal(await frame.locator("#reading-test-panel").evaluate((e) => e.scrollWidth <= e.clientWidth + 1), true);
  assert.deepEqual(errors, []);
});

for (const protocol of ["http", "https"]) {
  test(`${protocol.toUpperCase()} sandbox: grant button updates balance and history`, async (t) => {
    const { frame, errors } = await openPage(t, protocol);
    if (protocol === "http") assert.deepEqual(await frame.evaluate(() => ({
      secure: isSecureContext, uuid: typeof crypto.randomUUID, random: typeof crypto.getRandomValues,
    })), { secure: false, uuid: "undefined", random: "function" });
    await frame.getByLabel("赠送次数", { exact: true }).fill("3");
    await frame.getByLabel("赠送说明（可选）", { exact: true }).fill("活动奖励");
    await frame.getByRole("button", { name: "赠送额度", exact: true }).click();
    await waitNotice(frame, "已赠送 3 次额度", errors);
    const state = await frame.evaluate(() => qa);
    assert.equal(state.balance, 3);
    assert.equal(state.posts.length, 1);
    assert.equal(state.posts[0].note, "活动奖励");
    assert.match(await frame.locator(".history").first().innerText(), /\+3 次/);
    assert.match(await frame.locator("#detail-body .facts").first().innerText(), /赠送可用额度\s+3 次/);
    assert.deepEqual(errors, []);
  });
}

test("a lost response shows an error and retry reuses the operation without double credit", async (t) => {
  const { frame, errors } = await openPage(t);
  await frame.evaluate(() => { qa.failAfterSave = true; });
  await frame.getByRole("button", { name: "赠送额度", exact: true }).click();
  await waitNotice(frame, "模拟响应丢失", errors);
  assert.equal(await frame.getByRole("button", { name: "赠送额度", exact: true }).isEnabled(), true);
  await frame.getByRole("button", { name: "赠送额度", exact: true }).click();
  await waitNotice(frame, "已赠送 1 次额度", errors);
  const state = await frame.evaluate(() => qa);
  assert.equal(state.balance, 1);
  assert.equal(state.grants.length, 1);
  assert.equal(state.posts.length, 2);
  assert.equal(state.posts[0].request_id, state.posts[1].request_id);
  assert.deepEqual(errors, []);
});

test("request ID failures show feedback and release the disabled button", async (t) => {
  const { frame, errors } = await openPage(t);
  await frame.evaluate(() => {
    crypto.getRandomValues = () => { throw new Error("模拟随机源不可用"); };
  });
  await frame.getByRole("button", { name: "赠送额度", exact: true }).click();
  await waitNotice(frame, "模拟随机源不可用", errors);
  assert.equal(await frame.getByRole("button", { name: "赠送额度", exact: true }).isEnabled(), true);
  assert.equal(await frame.evaluate(() => qa.posts.length), 0);
  assert.deepEqual(errors, []);
});

test("rapid repeated submission sends only one request; a later grant has a new ID", async (t) => {
  const { frame, errors } = await openPage(t);
  await frame.evaluate(() => {
    qa.block = true;
    const form = document.querySelector("#detail-body form");
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  assert.equal(await frame.getByRole("button", { name: "赠送额度", exact: true }).isEnabled(), false);
  assert.equal(await frame.evaluate(() => qa.posts.length), 1);
  await frame.evaluate(() => { qa.block = false; qa.resume(); });
  await waitNotice(frame, "已赠送 1 次额度", errors);
  await frame.getByRole("button", { name: "赠送额度", exact: true }).click();
  await frame.waitForFunction(() => qa.balance === 2);
  const posts = await frame.evaluate(() => qa.posts);
  assert.equal(posts.length, 2);
  assert.notEqual(posts[0].request_id, posts[1].request_id);
  assert.deepEqual(errors, []);
});

for (const protocol of ["http", "https"]) {
  test(`${protocol.toUpperCase()} sandbox: export downloads all cases independently of filters`, async (t) => {
    const { frame, errors } = await openPage(t, protocol);
    await frame.getByRole("button", { name: "关闭详情" }).click();
    await frame.getByRole("button", { name: "卦例管理", exact: true }).click();
    await frame.locator("#search").fill("仅搜索一个用户");
    await frame.locator("#state").selectOption("pending");
    await frame.getByRole("button", { name: "导出全部卦例", exact: true }).click();
    await frame.waitForFunction(() => document.getElementById("notice").textContent.includes("已发起全部卦例下载"));
    assert.deepEqual(await frame.evaluate(() => qa.downloads), [{ endpoint: "manage/export/cases", params: {} }]);
    assert.equal(await frame.locator("#export-cases").isEnabled(), true);
    assert.deepEqual(errors, []);
  });
}

test("export prevents duplicate downloads and permits retry after an error", async (t) => {
  const { frame, errors } = await openPage(t);
  await frame.getByRole("button", { name: "关闭详情" }).click();
  await frame.evaluate(() => {
    qa.blockDownload = true;
    qa.downloadError = "模拟下载失败";
    const control = document.getElementById("export-cases");
    control.dispatchEvent(new Event("click"));
    control.dispatchEvent(new Event("click"));
  });
  assert.equal(await frame.locator("#export-cases").isEnabled(), false);
  assert.equal(await frame.evaluate(() => qa.downloads.length), 1);
  await frame.evaluate(() => { qa.blockDownload = false; qa.resumeDownload(); });
  await frame.waitForFunction(() => document.getElementById("notice").textContent.includes("模拟下载失败"));
  assert.equal(await frame.locator("#export-cases").isEnabled(), true);
  await frame.evaluate(() => { qa.downloadError = ""; });
  await frame.locator("#export-cases").click();
  await frame.waitForFunction(() => document.getElementById("notice").textContent.includes("已发起全部卦例下载"));
  assert.equal(await frame.evaluate(() => qa.downloads.length), 2);
  assert.deepEqual(errors, []);
});

test("an older Bridge without download support shows a recoverable error", async (t) => {
  const { frame, errors } = await openPage(t);
  await frame.getByRole("button", { name: "关闭详情" }).click();
  await frame.evaluate(() => { delete window.AstrBotPluginPage.download; });
  await frame.locator("#export-cases").click();
  await frame.waitForFunction(() => document.getElementById("notice").textContent.includes("请升级 AstrBot"));
  assert.equal(await frame.locator("#export-cases").isEnabled(), true);
  assert.deepEqual(errors, []);
});

async function openCasePage(t, protocol = "http") {
  const result = await openPage(t, protocol);
  await result.frame.getByRole("button", { name: "关闭详情" }).click();
  await result.frame.getByRole("button", { name: "卦例管理", exact: true }).click();
  await result.frame.getByRole("button", { name: "查看卦例", exact: true }).first().click();
  await result.frame.getByRole("button", { name: "渲染卦图并预览", exact: true }).waitFor();
  return result;
}

for (const protocol of ["http", "https"]) {
  test(`${protocol.toUpperCase()} sandbox: case preview renders only on click and supports rerender and original size`, async (t) => {
    const { frame, errors } = await openCasePage(t, protocol);
    assert.equal(await frame.evaluate(() => qa.previews.length), 0);
    await frame.getByRole("button", { name: "渲染卦图并预览", exact: true }).click();
    await frame.getByRole("img", { name: "卦例 #302 的卦图" }).waitFor();
    assert.equal(await frame.locator(".chart-preview img").evaluate((img) => img.complete && img.naturalWidth > 0), true);
    await frame.getByRole("button", { name: "查看原图尺寸", exact: true }).click();
    assert.equal(await frame.locator(".chart-preview").evaluate((box) => box.classList.contains("original-size")), true);
    await frame.getByRole("button", { name: "适应宽度", exact: true }).click();
    await frame.getByRole("button", { name: "重新渲染卦图", exact: true }).click();
    await frame.getByRole("button", { name: "重新渲染卦图", exact: true }).waitFor();
    assert.deepEqual(await frame.evaluate(() => qa.previews), Array(2).fill({ endpoint: "manage/cases/LY-302/render", payload: {} }));
    assert.deepEqual(errors, []);
  });
}

test("preview prevents duplicate rendering and recovers from backend and image errors", async (t) => {
  const { frame, errors } = await openCasePage(t);
  await frame.evaluate(() => {
    qa.blockPreview = true;
    qa.previewError = "卦例不存在或已删除";
    const control = document.querySelector(".case-chart button");
    control.dispatchEvent(new Event("click"));
    control.dispatchEvent(new Event("click"));
  });
  assert.equal(await frame.evaluate(() => qa.previews.length), 1);
  assert.equal(await frame.getByRole("button", { name: "正在渲染…", exact: true }).isEnabled(), false);
  await frame.evaluate(() => { qa.blockPreview = false; qa.resumePreview(); });
  await frame.getByText("卦图预览失败：卦例不存在或已删除", { exact: true }).waitFor();
  assert.equal(await frame.locator(".chart-preview").isVisible(), false);
  const validImage = await frame.evaluate(() => qa.previewImage);
  for (const image of ["https://untrusted.test/image.png", "data:image/png;base64,bm90IGEgcG5n"]) {
    await frame.evaluate((image) => { qa.previewError = ""; qa.previewImage = image; }, image);
    await frame.getByRole("button", { name: "重试渲染卦图", exact: true }).click();
    await frame.getByRole("button", { name: "重试渲染卦图", exact: true }).waitFor();
    assert.equal(await frame.locator(".chart-status.error").isVisible(), true);
  }
  await frame.evaluate((image) => { qa.previewImage = image; }, validImage);
  await frame.getByRole("button", { name: "重试渲染卦图", exact: true }).click();
  await frame.getByRole("img", { name: "卦例 #302 的卦图" }).waitFor();
  assert.deepEqual(errors, []);
});

for (const failure of [false, true]) {
  test(`late preview ${failure ? "failure" : "success"} cannot change another case detail`, async (t) => {
    const { frame, errors } = await openCasePage(t);
    await frame.evaluate((failure) => { qa.blockPreview = true; qa.previewError = failure ? "旧请求失败" : ""; }, failure);
    await frame.getByRole("button", { name: "渲染卦图并预览", exact: true }).click();
    await frame.getByRole("button", { name: "关闭详情" }).click();
    await frame.getByRole("button", { name: "查看卦例", exact: true }).nth(1).click();
    await frame.getByRole("heading", { name: "卦例 #905", exact: true }).waitFor();
    await frame.evaluate(async () => { qa.resumePreview(); await new Promise((resolve) => setTimeout(resolve, 50)); });
    assert.equal(await frame.locator(".chart-preview img").count(), 0);
    assert.equal(await frame.locator(".chart-status").textContent(), "");
    assert.equal(await frame.getByRole("button", { name: "渲染卦图并预览", exact: true }).isEnabled(), true);
    assert.deepEqual(errors, []);
  });
}


test("case list keeps server order and distinguishes call time from historical cast time", async (t) => {
  const { page, frame, errors } = await openPage(t);
  await frame.evaluate(() => {
    qa.cases[0].created_at = "2000-01-01T08:30:00+08:00";
    qa.cases[0].recorded_at = "2026-11-16T12:30:00+08:00";
  });
  await frame.locator("#close-detail").click();
  await frame.getByRole("button", { name: "卦例管理", exact: true }).click();
  await frame.getByRole("columnheader", { name: "编号 / 调用时间", exact: true }).waitFor();
  const first = frame.locator("#table-body tr").first().locator("td").first();
  assert.match(await first.textContent(), /#302调用 2026\/11\/16 12:30起卦 2000\/01\/01 08:30/);
  assert.match(await frame.locator("#table-body tr").nth(1).textContent(), /调用 未记录/);
  assert.match(await frame.locator("#table-hint").textContent(), /按调用（录入）时间从新到旧排列/);
  if (process.env.LIUYAO_QA_OUTPUT) await page.screenshot({ path: join(process.env.LIUYAO_QA_OUTPUT, "management-time-order.png"), fullPage: true });
  await frame.getByRole("button", { name: "查看卦例", exact: true }).first().click();
  await frame.getByRole("heading", { name: "卦例 #302", exact: true }).waitFor();
  assert.match(await frame.locator("#detail-body").textContent(), /调用时间：2026\/11\/16 12:30 · 起卦时间：2000\/01\/01 08:30/);
  if (process.env.LIUYAO_QA_OUTPUT) await page.screenshot({ path: join(process.env.LIUYAO_QA_OUTPUT, "management-time-detail.png"), fullPage: true });
  assert.deepEqual(errors, []);
});

test("case states and complete reading history remain visible and escaped", async (t) => {
  const { page, frame, errors } = await openPage(t);
  await frame.evaluate(() => {
    qa.cases[0].feedback_closed = true;
    qa.cases[0].feedback_eligible = false;
    qa.cases[0].feedback_status = "已反馈";
    qa.cases[0].analysis = "首次原断";
    qa.cases[0].analysis_question = "首次问题";
    qa.cases[0].analysis_history = [1, 2].map((n) => ({
      id: `run-${n}`, at: "2026-11-16T10:00:00+08:00", status: "success",
      question: `第${n}次问题`, analysis: `第${n}次完整解读`, verdict: `第${n}次结论`,
      provider: "provider-test", model: "<img src=x onerror=alert(1)>", calls: [],
      usage: {total_tokens: 120, input_tokens: 100, output_tokens: 20, cached_tokens: 10, calls: 2, unknown_calls: 0, tracked: true},
    }));
    qa.cases[1].feedback_closed = false;
    qa.cases[1].feedback_eligible = false;
    qa.cases[1].feedback_status = "仅排盘 · 不可反馈";
    qa.cases.push({...qa.cases[1], id: "LY-906", serial: 906, feedback_eligible: true, feedback_status: "待反馈"});
  });
  await frame.locator("#close-detail").click();
  await frame.getByRole("button", {name: "卦例管理", exact: true}).click();
  assert.equal(await frame.locator(".case-number.closed").textContent(), "#302");
  assert.equal(await frame.locator(".case-number.unavailable").textContent(), "#905");
  assert.equal(await frame.locator(".case-number.pending").textContent(), "#906");
  const palette = await frame.locator(".case-number.closed").evaluate((el) => ({border: getComputedStyle(el).borderTopColor, background: getComputedStyle(el).backgroundColor}));
  assert.deepEqual(palette, {border: "rgb(172, 135, 62)", background: "rgb(241, 228, 189)"});
  if (process.env.LIUYAO_QA_OUTPUT) await page.screenshot({path: join(process.env.LIUYAO_QA_OUTPUT, "management-cases.png"), fullPage: true});
  await frame.getByRole("button", {name: "查看卦例", exact: true}).first().click();
  await frame.getByText("逐次解读记录", {exact: true}).waitFor();
  const history = frame.locator(".section").filter({has: frame.getByText("逐次解读记录", {exact: true})});
  assert.equal(await history.locator("details").count(), 2);
  await history.locator("summary").first().click();
  assert.match(await history.textContent(), /第1次结论/);
  assert.match(await history.textContent(), /第2次完整解读/);
  assert.match(await history.textContent(), /120/);
  assert.equal(await history.locator("img").count(), 0);
  if (process.env.LIUYAO_QA_OUTPUT) await page.screenshot({path: join(process.env.LIUYAO_QA_OUTPUT, "management-history.png"), fullPage: true});
  assert.deepEqual(errors, []);
});
