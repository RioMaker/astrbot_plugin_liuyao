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
  const state = window.qa = { balance: 0, posts: [], grants: [], failAfterSave: false, block: false };
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
    apiGet: async (endpoint) => {
      if (endpoint === "manage/users/20002") return user();
      if (endpoint === "manage/users") return {
        items: [user()], page: 1, pages: 1, total: 1, stats: { users: 1, casts: 1, banned: 0 },
      };
      if (endpoint === "manage/cases") return { items: [], all_count: 0 };
      throw new Error(`Unexpected endpoint: ${endpoint}`);
    },
    apiPost: async (endpoint, payload) => {
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
    if (path === "/app.js" || path === "/style.css") return route.fulfill({
      contentType: path.endsWith(".js") ? "text/javascript" : "text/css", headers,
      body: readFileSync(join(pageRoot, path.slice(1)), "utf8"),
    });
    return route.abort();
  });
  await page.goto(`${protocol}://liuyao.test/`);
  const frame = page.frames().find((item) => item.url().endsWith("/index.html"));
  await frame.getByRole("button", { name: "管理用户", exact: true }).click();
  await frame.getByLabel("赠送次数", { exact: true }).waitFor();
  return { frame, errors };
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
