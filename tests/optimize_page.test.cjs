// Real page inside an AstrBot-style sandbox. The Bridge stands in for the dashboard.
const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { chromium } = require("playwright");

const root = join(__dirname, "..", "pages", "optimize");
let browser;
before(async () => {
  browser = await chromium.launch({
    headless: true,
    channel: process.env.LIUYAO_TEST_BROWSER_CHANNEL || undefined,
  });
});
after(async () => { await browser?.close(); });

function installBridge() {
  const changes = [
    { section: "general", before: "原规则", after: "核实证据", reason: "区别口头与书面", id: "s1", status: "pending" },
    { section: "weather", before: "参考时段", after: "先核对时段", reason: "明确范围", id: "s2", status: "pending" },
  ];
  const state = {
    revision: "1", turns: [], providers: [{ id: "model", model: "M" }], preferred: "model",
    sections: {
      general: { label: "通用研判规则", text: "前文\n原规则\n后文", editable: true },
      weather: { label: "天气研判规则", text: "参考时段", editable: true },
      reference: { label: "分类参考 · 通则", text: "来源文献", editable: false },
    },
  };
  window.posts = [];
  window.AstrBotPluginPage = {
    ready: async () => {},
    apiGet: async () => structuredClone(state),
    apiPost: async (endpoint, payload) => {
      window.posts.push({ endpoint, payload });
      if (endpoint === "optimize/chat") {
        state.turns.push({
          request_revision: payload.revision, message: payload.message, reply: "复盘回复 <img src=x>", provider: "model", changes,
          use_cases: payload.use_cases, cases: payload.use_cases ? [{
            serial: 233, question: "已签约吗", chart: "原盘", reading: "原断", verdict: "会签约",
            feedback: ["只有口头意向"], answer: "",
          }] : [],
        });
      } else if (endpoint === "optimize/decision") {
        const change = changes.find((s) => s.id === payload.id);
        if (payload.action === "approve") {
          state.sections[change.section].text = state.sections[change.section].text.replace(change.before, change.after);
          change.status = "approved";
        } else change.status = "dismissed";
      } else if (endpoint === "optimize/reset") {
        state.turns = [];
      } else throw new Error("Unexpected endpoint " + endpoint);
      state.revision += "x";
      if (window.dropResponse === endpoint) {
        window.dropResponse = "";
        throw new Error("模拟响应丢失");
      }
      return structuredClone(state);
    },
  };
}

async function open(t, width = 1200) {
  const context = await browser.newContext({ viewport: { width, height: 850 } });
  t.after(() => context.close());
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await context.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const headers = { "access-control-allow-origin": "*" };
    if (path === "/") return route.fulfill({
      contentType: "text/html", body: '<iframe sandbox="allow-scripts allow-forms allow-downloads" src="/index.html" style="border:0;width:100%;height:95vh"></iframe>',
    });
    if (path === "/index.html") return route.fulfill({
      contentType: "text/html", headers,
      body: readFileSync(join(root, "index.html"), "utf8").replace("<head>", "<head><script>(" + installBridge.toString() + ")();</script>"),
    });
    if (path === "/app.js" || path === "/style.css") return route.fulfill({
      contentType: path.endsWith(".js") ? "text/javascript" : "text/css", headers,
      body: readFileSync(join(root, path.slice(1)), "utf8"),
    });
    return route.abort();
  });
  await page.goto("http://liuyao.test/");
  const frame = page.frames().find((f) => f.url().endsWith("/index.html"));
  await frame.waitForFunction(() => !document.getElementById("composer").disabled);
  return { frame, errors };
}

test("discussion, displayed source and exact preview precede independent approval", async (t) => {
  const { frame, errors } = await open(t);
  const rule = frame.locator("#rule-text");
  assert.equal(await rule.innerText(), "前文\n原规则\n后文");
  await frame.getByLabel("本轮引用卦例库").check();
  await frame.locator("#serials").fill("#233");
  await frame.getByLabel("讨论内容").fill("请复盘这次签约判断");
  await frame.getByRole("button", { name: "发送", exact: true }).click();
  const cards = frame.locator(".proposal");
  await cards.first().waitFor();
  assert.equal(await cards.count(), 2);
  assert.deepEqual(await frame.evaluate(() => posts.map((p) => p.endpoint)), ["optimize/chat"]);
  assert.deepEqual(await frame.evaluate(() => posts[0].payload.serials), [233]);
  await frame.getByText("本轮引用卦例 #233").click();
  assert.match(await frame.locator(".source").innerText(), /实际反馈：只有口头意向/);
  assert.equal(await frame.locator("#conversation img").count(), 0);
  assert.equal(await rule.locator("mark").innerText(), "原规则");
  assert.deepEqual(await cards.first().locator(".comparison p").allTextContents(), ["原规则", "核实证据"]);

  await cards.last().getByRole("button", { name: "放弃此项" }).click();
  await frame.getByText("已放弃", { exact: true }).waitFor();
  assert.equal(await rule.innerText(), "参考时段");
  await cards.first().getByRole("button", { name: "确认采用此项" }).click();
  await frame.getByText("已采用", { exact: true }).waitFor();
  assert.equal(await rule.innerText(), "前文\n核实证据\n后文");
  assert.deepEqual(await frame.evaluate(() => posts.slice(1).map((p) => p.payload)), [
    { id: "s2", action: "dismiss" }, { id: "s1", action: "approve" },
  ]);
  await frame.getByRole("button", { name: "刷新", exact: true }).click();
  await frame.locator("#section").selectOption("reference");
  assert.equal(await rule.innerText(), "来源文献");
  assert.deepEqual(errors, []);
});

test("mobile chat works without cases, validates explicit numbers and can start afresh", async (t) => {
  const { frame, errors } = await open(t, 375);
  assert.equal(await frame.evaluate(() =>
    document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
  await frame.getByLabel("本轮引用卦例库").check();
  await frame.locator("#serials").fill("233，坏编号");
  await frame.getByLabel("讨论内容").fill("仅凭这段对话讨论");
  await frame.getByRole("button", { name: "发送", exact: true }).click();
  await frame.getByText(/请输入最多 3 个卦例编号/).waitFor();
  assert.equal(await frame.evaluate(() => posts.length), 0);
  await frame.getByLabel("本轮引用卦例库").uncheck();
  await frame.getByLabel("讨论内容").press("Control+Enter");
  await frame.locator(".proposal").first().waitFor();
  assert.deepEqual(await frame.evaluate(() => [posts[0].payload.use_cases, posts[0].payload.serials]), [false, []]);
  await frame.getByRole("button", { name: "新对话", exact: true }).click();
  await frame.getByRole("button", { name: "取消", exact: true }).click();
  assert.equal(await frame.locator(".proposal").count(), 2);
  await frame.getByRole("button", { name: "新对话", exact: true }).click();
  await frame.getByRole("button", { name: "开始新对话", exact: true }).click();
  await frame.locator(".empty").waitFor();
  assert.equal(await frame.locator("#rule-text").innerText(), "前文\n原规则\n后文");
  assert.deepEqual(errors, []);
});

test("lost chat and approval responses reconcile the saved result without another POST", async (t) => {
  const { frame } = await open(t);
  await frame.evaluate(() => { window.dropResponse = "optimize/chat"; });
  await frame.getByLabel("讨论内容").fill("请提出待审建议");
  await frame.getByRole("button", { name: "发送", exact: true }).click();
  await frame.getByText("建议已生成。请核对修改前后，再逐项确认。").waitFor();
  assert.equal(await frame.getByLabel("讨论内容").inputValue(), "");
  assert.equal(await frame.locator("#rule-text").innerText(), "前文\n原规则\n后文");
  await frame.evaluate(() => { window.dropResponse = "optimize/decision"; });
  await frame.getByRole("button", { name: "确认采用此项" }).first().click();
  await frame.getByText("已采用此项，下次解卦将使用新规则。").waitFor();
  assert.equal(await frame.locator("#rule-text").innerText(), "前文\n核实证据\n后文");
  assert.deepEqual(await frame.evaluate(() => posts.map((p) => p.endpoint)), ["optimize/chat", "optimize/decision"]);
});
