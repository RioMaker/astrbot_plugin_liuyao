// Real page inside an AstrBot-style sandbox. The Bridge stands in for the dashboard.
const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { chromium } = require("playwright");

const pages = join(__dirname, "..", "pages");
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
    versions: { general: 1, weather: 1 }, directions: { career: "事业", weather: "天气" },
    sections: {
      general: { label: "通用研判规则", text: "前文\n原规则\n后文", editable: true },
      weather: { label: "天气研判规则", text: "参考时段", editable: true },
      reference: { label: "分类参考 · 通则", text: "来源文献", editable: false },
    },
  };
  const at = "2026-11-23T10:30:00+08:00";
  const records = [];
  const versions = Object.fromEntries(["general", "weather"].map((section) => [section, [
    { number: 1, at, source: "initial", text: state.sections[section].text },
  ]]));
  window.posts = [];
  window.reads = [];
  window.caseQueries = [];
  window.AstrBotPluginPage = {
    ready: async () => {},
    apiGet: async (endpoint, query = {}) => {
      window.reads.push(endpoint);
      if (endpoint === "optimize/state") return structuredClone(state);
      const list = { items: [], page: 1, pages: 1, total: 0 };
      if (endpoint === "optimize/cases") {
        window.caseQueries.push(query);
        if (query.search && query.search !== "合同") return list;
        const cases = Array.from({ length: 22 }, (_, i) => ({
          serial: 233 - i, question: "合同进度 " + (233 - i), direction: "事业", primary: "乾",
          changed: "泰", outcomes: ["未应验"],
        }));
        const page = query.page;
        return { total: 22, pages: 2, page, items: cases.slice((page - 1) * 20, page * 20) };
      }
      if (endpoint === "optimize/history") {
        if (query.id) return structuredClone(records.find((r) => r.id === query.id));
        return { ...list, total: records.length, items: records.slice().reverse().map((turn) => ({
          ...turn, proposals: turn.changes.length, approved: turn.changes.filter((c) => c.status === "approved").length,
        })) };
      }
      if (endpoint === "optimize/versions") {
        const rows = versions[query.section];
        if (query.number) return structuredClone(rows.find((v) => v.number === query.number));
        return { ...list, total: rows.length, items: structuredClone(rows.slice().reverse()) };
      }
      if (endpoint === "manage/users") return { ...list, stats: { users: 0, banned: 0, casts: 0 } };
      if (endpoint === "manage/cases") return { ...list, all_count: 0 };
      if (endpoint === "manage/usage") return { ...list, overview: {}, summary: { casts: 0 } };
      throw new Error("Unexpected endpoint " + endpoint);
    },
    apiPost: async (endpoint, payload) => {
      window.posts.push({ endpoint, payload });
      if (endpoint === "optimize/chat") {
        state.turns.push({
          id: records.length + 1, actor: "admin", at, status: "done", versions: { ...state.versions }, focus: payload.focus,
          request_revision: payload.revision, message: payload.message, reply: "复盘回复 <img src=x>", provider: "model", changes,
          use_cases: payload.use_cases, cases: payload.use_cases ? [{
            serial: 233, question: "已签约吗", chart: "原盘", reading: "原断", verdict: "会签约",
            feedback: ["只有口头意向"], answer: "",
          }] : [],
        });
        records.push(state.turns.at(-1));
      } else if (endpoint === "optimize/decision") {
        const change = changes.find((s) => s.id === payload.id);
        if (payload.action === "approve") {
          const previous = state.sections[change.section].text;
          state.sections[change.section].text = state.sections[change.section].text.replace(change.before, change.after);
          change.status = "approved";
          change.decided_at = at;
          change.base_version = state.versions[change.section];
          change.version = ++state.versions[change.section];
          versions[change.section].push({
            number: change.version, at, source: "workshop", actor: "admin", previous, turn_id: 1, can_view_turn: true,
            reason: change.reason, before: change.before, after: change.after, text: state.sections[change.section].text,
          });
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

async function open(t, width = 1200, entry = "optimize") {
  const root = join(pages, entry);
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
    if (["/app.js", "/controller.js", "/workshop-tab.js", "/style.css"].includes(path)) return route.fulfill({
      contentType: path.endsWith(".js") ? "text/javascript" : "text/css", headers,
      body: readFileSync(join(root, path.slice(1)), "utf8"),
    });
    return route.abort();
  });
  await page.goto("http://liuyao.test/");
  const frame = page.frames().find((f) => f.url().endsWith("/index.html"));
  if (entry === "manage") await frame.getByText("共 0 条记录", { exact: true }).waitFor();
  else await frame.locator("#composer:not([disabled])").waitFor();
  return { frame, errors };
}

test("the fourth management tab shows live rules and preserves drafts and pending approvals", async (t) => {
  const { frame, errors } = await open(t, 1200, "manage");
  await frame.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
  const nav = frame.getByRole("navigation", { name: "管理分类" });
  assert.deepEqual(await nav.getByRole("button").allTextContents(), ["用户管理", "卦例管理", "Token 统计", "解卦优化"]);
  assert.equal(await frame.evaluate(() => reads.includes("optimize/state")), false);
  const tab = nav.getByRole("button", { name: "解卦优化" });
  await tab.click();
  const panel = frame.getByRole("region", { name: "解卦优化", exact: true });
  await panel.getByRole("heading", { name: "复盘对话" }).waitFor();
  await panel.getByRole("heading", { name: "当前解卦逻辑" }).waitFor();
  await panel.locator("#composer:not([disabled])").waitFor();
  assert.equal(await tab.getAttribute("aria-pressed"), "true");
  assert.equal(await frame.locator("#management-list").isVisible(), false);
  await panel.getByLabel("本轮引用卦例库").check();
  await panel.locator("#serials").fill("#233");
  await panel.getByLabel("讨论内容").fill("请结合实际反馈复盘");
  await panel.getByRole("button", { name: "发送", exact: true }).click();
  const first = panel.locator(".proposal").first();
  await first.waitFor();
  assert.deepEqual(await first.locator(".comparison p").allTextContents(), ["原规则", "核实证据"]);
  assert.equal(await panel.locator("#rule-text mark").innerText(), "原规则");
  await panel.getByLabel("讨论内容").fill("还需要核实哪项证据？");
  await nav.getByRole("button", { name: "卦例管理" }).click();
  await frame.locator("#table-head").getByText("所问与卦象").waitFor();
  assert.equal(await panel.isVisible(), false);
  await nav.getByRole("button", { name: "Token 统计" }).click();
  await frame.locator("#usage-summary").waitFor();
  await tab.click();
  assert.equal(await panel.getByLabel("讨论内容").inputValue(), "还需要核实哪项证据？");
  assert.equal(await panel.locator("#rule-text").innerText(), "前文\n原规则\n后文");
  assert.deepEqual(await frame.evaluate(() => posts.map((p) => p.endpoint)), ["optimize/chat"]);
  await first.getByRole("button", { name: "确认采用此项" }).click();
  await panel.getByText("已采用", { exact: true }).waitFor();
  assert.equal(await panel.locator("#rule-text").innerText(), "前文\n核实证据\n后文");
  assert.deepEqual(await frame.evaluate(() => posts[1].payload), { id: "s1", action: "approve" });
  assert.equal(await frame.evaluate(() => reads.includes("manage/optimize")), false);
  assert.deepEqual(errors, []);
});

test("the workshop tab and its jump to the current logic fit on a phone", async (t) => {
  const { frame } = await open(t, 360, "manage");
  const tab = frame.getByRole("button", { name: "解卦优化", exact: true });
  const insideViewport = (element) => {
    const rect = element.getBoundingClientRect();
    return rect.left >= 0 && rect.right <= document.documentElement.clientWidth;
  };
  assert.equal(await tab.evaluate(insideViewport), true);
  await tab.click();
  await frame.getByRole("link", { name: "查看当前逻辑" }).click();
  const rule = frame.locator("#rule-text");
  assert.equal(await rule.evaluate((el) => el.matches(":focus")), true);
  assert.equal(await rule.evaluate(insideViewport), true);
});

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

test("picker combines manual and cross-page selections and sends only those numbers", async (t) => {
  const { frame, errors } = await open(t, 375, "manage");
  await frame.getByRole("button", { name: "解卦优化", exact: true }).click();
  await frame.getByLabel("本轮引用卦例库").check();
  await frame.locator("#serials").fill("#45");
  const picker = frame.locator("#case-picker");
  await picker.locator("summary").click();
  await frame.getByLabel("问卦方向").selectOption("career");
  await frame.getByLabel("反馈情况").selectOption("未应验");
  await frame.locator("#case-search").fill("合同");
  await frame.locator("#case-search").press("Enter");
  await picker.getByRole("checkbox", { name: /#233 ·/ }).check();
  await picker.getByRole("button", { name: "下一页" }).click();
  await picker.getByRole("checkbox", { name: /#213 ·/ }).check();
  assert.equal(await frame.locator("#serials").inputValue(), "#45，#233，#213");
  assert.equal(await picker.getByRole("checkbox", { name: /#212 ·/ }).isDisabled(), true);
  await picker.getByRole("button", { name: "上一页" }).click();
  assert.equal(await picker.getByRole("checkbox", { name: /#233 ·/ }).isChecked(), true);
  assert.deepEqual(await frame.evaluate(() => caseQueries.at(-1)),
    { page: 1, search: "合同", intent: "career", feedback: "未应验" });
  await frame.locator("#case-search").fill("没有这个记录");
  await picker.getByRole("button", { name: "查询" }).click();
  await picker.getByText("没有符合条件的卦例").waitFor();
  assert.equal(await frame.evaluate(() => posts.length), 0);
  await frame.getByLabel("讨论内容").fill("复盘勾选的记录");
  await frame.getByRole("button", { name: "发送", exact: true }).click();
  await frame.locator(".proposal").first().waitFor();
  assert.deepEqual(await frame.evaluate(() => posts[0].payload.serials), [45, 233, 213]);
  assert.deepEqual(errors, []);
});

test("archived rounds and full prompt versions remain readable after starting over", async (t) => {
  const { frame, errors } = await open(t);
  await frame.getByLabel("本轮引用卦例库").check();
  await frame.locator("#serials").fill("#233");
  await frame.getByLabel("讨论内容").fill("请核实口头承诺");
  await frame.getByRole("button", { name: "发送", exact: true }).click();
  await frame.getByRole("button", { name: "确认采用此项" }).first().click();
  await frame.getByText("已采用", { exact: true }).waitFor();
  await frame.getByRole("button", { name: "放弃此项" }).click();
  await frame.getByText("已放弃", { exact: true }).waitFor();
  await frame.getByRole("button", { name: "新对话", exact: true }).click();
  await frame.getByRole("button", { name: "开始新对话", exact: true }).click();
  await frame.locator("#conversation .empty").waitFor();

  const log = frame.locator("#round-history");
  await log.locator(":scope > summary").click();
  await log.getByText(/#1 · .*建议 2 项，已采用 1 项 · 请核实口头承诺/).click();
  await log.getByText("已采用", { exact: true }).waitFor();
  await log.getByText("已放弃", { exact: true }).waitFor();
  assert.equal(await log.getByRole("button", { name: "确认采用此项" }).count(), 0);
  await log.getByText("本轮引用卦例 #233").click();
  assert.match(await log.locator(".source").innerText(), /只有口头意向/);
  assert.equal(await log.locator("img").count(), 0);
  await log.getByText("查看采用后的 V2 全文").click();
  assert.match(await log.locator(".rule-copy").innerText(), /前文\n核实证据\n后文/);
  await log.getByText("本轮使用的提示词").click();
  await log.getByText("通用研判规则 · V1").click();
  await log.getByText("前文\n原规则\n后文", { exact: true }).waitFor();

  await frame.locator("#section").selectOption("general");
  const versions = frame.locator("#version-history");
  await versions.locator(":scope > summary").click();
  await versions.getByText(/V2 · 协同优化 · admin/).click();
  await versions.locator(".comparison p").first().waitFor();
  assert.deepEqual(await versions.locator(".comparison p").allTextContents(), ["原规则", "核实证据"]);
  assert.equal(await versions.locator(".rule-copy").innerText(), "前文\n核实证据\n后文");
  await versions.getByText("查看优化记录 #1").click();
  await versions.getByText("请核实口头承诺", { exact: true }).waitFor();
  await frame.locator("#section").selectOption("reference");
  assert.equal(await versions.isVisible(), false);
  assert.deepEqual(errors, []);
});
