export function startWorkshop(root, bridge) {
  const $ = (id) => root.getElementById(id);
  const statusNames = {
    pending: "待确认", approved: "已采用", dismissed: "已放弃", stale: "原文已变化，请重新讨论",
    archived: "已归档，未采用",
  };
  const roundNames = { running: "处理中", done: "已完成", failed: "失败", interrupted: "已中断" };
  const versionNames = { initial: "初始版本", config: "检测到配置变更", workshop: "协同优化" };
  const dateFormat = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
  const time = (value) => dateFormat.format(new Date(value));
  let state;
  let busy = false;

  function element(tag, text = "", className = "") {
    const result = document.createElement(tag);
    result.textContent = text;
    if (className) result.className = className;
    return result;
  }

  function button(label, onClick, className = "") {
    const control = element("button", label, className);
    control.type = "button";
    control.addEventListener("click", onClick);
    return control;
  }

  function notice(message, error = false) {
    const box = $("notice");
    box.textContent = message;
    box.hidden = !message;
    box.classList.toggle("error", error);
  }

  function controls() {
    $("refresh").disabled = busy;
    $("new-chat").disabled = busy || !state?.turns.length;
    $("reset-confirm").disabled = busy;
    $("composer").disabled = busy || !state;
    $("send").disabled = busy || !$("provider").value || !$("message").value.trim();
    for (const control of root.querySelectorAll(".decision")) control.disabled = busy;
  }

  function renderRule(highlight = "") {
    const key = $("section").value;
    const rule = state?.sections[key];
    if (!rule) return;
    const scopes = {
      command_pre_review_prompt: "起卦和复解时用于判断是否进入解卦。",
      command_classification_rules: "预审模型同时收到本节规则和当前候选分类。可引用人工改类的卦例复盘。",
      feedback_review_prompt: "从下一次新建的反馈审核会话开始生效。已有审核会话沿用首次审核的规则。",
      workshop_discussion_prompt: "用于后台优化模型的后续讨论。",
      classification_catalog: "在卦例管理中新建分类时，可以填写适用范围供预审模型参考。",
    };
    let scope = scopes[key] || (rule.editable
      ? "解卦时使用此规则。对话建议经确认后生效。"
      : "只读资料，可在讨论时引用。");
    const template = state.sections.command_interpretation_prompt;
    if (template && !template.text.includes("{chart}") && (key.startsWith("interpretation_") || key.startsWith("reference_"))) {
      scope = "当前解卦提示词未包含 {chart}。将该变量加回提示词后，常规解卦才能收到本节内容。";
    }
    const version = state.versions?.[key];
    $("section-scope").textContent = (version ? "当前 V" + version + " · " : "") + scope;
    $("version-history").hidden = !rule.editable;
    const box = $("rule-text");
    const at = highlight ? rule.text.indexOf(highlight) : -1;
    if (at < 0) {
      box.textContent = rule.text;
      box.scrollTop = 0;
      return;
    }
    const mark = element("mark", highlight);
    box.replaceChildren(rule.text.slice(0, at), mark, rule.text.slice(at + highlight.length));
    mark.scrollIntoView({ block: "nearest" });
  }

  function showSection(key, snippet, scroll = false) {
    const changed = $("section").value !== key;
    $("section").value = key;
    renderRule(snippet);
    if (changed) versionList.load();
    if (scroll) {
      $("logic").scrollIntoView({ block: "nearest" });
      $("rule-text").focus({ preventScroll: true });
    }
  }

  function comparison(oldLabel, before, newLabel, after) {
    const diff = element("div", "", "comparison");
    for (const [label, value] of [[oldLabel, before], [newLabel, after]]) {
      const part = element("div");
      part.append(element("strong", label), element("p", value, "message"));
      diff.append(part);
    }
    return diff;
  }

  function proposal(change, reviewable = true) {
    const card = element("section", "", "proposal");
    const head = element("div", "", "proposal-head");
    head.append(element("h3", state.sections[change.section]?.label || change.section),
      element("span", statusNames[change.status], "state " + change.status));
    const diff = comparison("修改前", change.before, "建议改为", change.after || "（删除此段）");
    const actions = element("div", "", "actions");
    if (reviewable) actions.append(button("查看所在章节", () => showSection(change.section,
      change.status === "approved" ? change.after : change.before, true)));
    if (reviewable && change.status === "pending") {
      actions.append(button("确认采用此项", () => decide(change, "approve"), "primary decision"),
        button("放弃此项", () => decide(change, "dismiss"), "decision"));
    }
    card.append(head, element("p", change.reason, "reason"), diff, actions);
    if (change.decided_at) card.append(element("small", time(change.decided_at)
      + (change.version ? " · V" + change.base_version + " → V" + change.version : "")));
    if (!reviewable && change.version) {
      card.append(versionDisclosure(change.section, change.version, "查看采用后的 V" + change.version + " 全文"));
    }
    return card;
  }

  function sources(turn) {
    if (!turn.cases.length) return element("small", "本轮未检索到有实际反馈的参考卦例。");
    const details = element("details");
    const ids = turn.cases.map((item) => "#" + item.serial).join("、");
    details.append(element("summary", "本轮引用卦例 " + ids));
    for (const item of turn.cases) {
      const part = element("div", "", "source");
      const evidence = [
        item.chart, "原断：" + (item.reading || "未保存"), "断语：" + (item.verdict || "未保存"),
        "实际反馈：" + (item.feedback.join(" / ") || "暂无"),
      ];
      if (item.intent_label || item.intent) {
        const category = item.intent_label || state.directions?.[item.intent] || item.intent;
        evidence.unshift(item.original_intent_label
          ? "分类（人工修正）：" + item.original_intent_label + " → " + category : "分类：" + category);
      }
      if (item.answer) evidence.push("揭晓答案：" + item.answer);
      part.append(element("strong", "#" + item.serial + " · " + (item.question || "未记录问题")),
        element("p", evidence.join("\n"), "message"));
      details.append(part);
    }
    return details;
  }

  function disclosure(title, getBody) {
    const details = element("details", "", "record");
    const body = element("div", "", "record-body");
    details.append(element("summary", title), body);
    let loaded = false;
    details.addEventListener("toggle", async () => {
      if (!details.open || loaded) return;
      loaded = true;
      body.textContent = "正在读取…";
      try {
        body.replaceChildren(await getBody());
      } catch (error) {
        loaded = false;
        body.textContent = (error.message || "读取失败") + "，收起后可重新展开。";
      }
    });
    return details;
  }

  function versionDisclosure(section, number, title, linkToRound = false) {
    return disclosure(title, async () => {
      const v = await bridge.apiGet("optimize/versions", { section, number });
      const body = element("div");
      if (v.source === "workshop") {
        body.append(element("p", "优化记录 #" + v.turn_id + " · " + v.reason, "reason"),
          comparison("修改前", v.before, "修改后", v.after || "（删除此段）"));
        if (linkToRound && v.can_view_turn) {
          body.append(disclosure("查看优化记录 #" + v.turn_id, () => roundBody(v.turn_id)));
        }
      } else if (v.previous != null) {
        if (v.diff) body.append(element("strong", "与上一版的差异"), element("p", v.diff, "message rule-copy"));
        const previous = element("details");
        previous.append(element("summary", "查看上一版全文"), element("p", v.previous, "message"));
        body.append(previous);
      }
      body.append(element("strong", "V" + v.number + " 全文"), element("p", v.text, "message rule-copy"));
      return body;
    });
  }

  function historyRow(item) {
    const status = item.status === "done"
      ? (item.proposals ? "建议 " + item.proposals + " 项，已采用 " + item.approved + " 项" : "仅讨论")
      : roundNames[item.status];
    return disclosure(["#" + item.id, time(item.at), status, item.message].join(" · "), () => roundBody(item.id));
  }

  async function roundBody(id) {
    const turn = await bridge.apiGet("optimize/history", { id });
    const body = element("div");
    body.append(element("p", [turn.actor, turn.provider, state.sections[turn.focus]?.label || turn.focus].join(" · "), "byline"),
      element("p", turn.message, "message ask"));
    if (turn.use_cases) body.append(sources(turn));
    if (turn.reply) body.append(element("p", turn.reply, "message"));
    if (turn.status === "done" && !turn.changes.length) body.append(element("small", "本轮仅讨论"));
    for (const change of turn.changes) body.append(proposal(change, false));
    const bases = element("details");
    bases.append(element("summary", "本轮使用的提示词"));
    for (const [section, number] of Object.entries(turn.versions)) {
      bases.append(versionDisclosure(section, number, (state.sections[section]?.label || section) + " · V" + number));
    }
    body.append(bases);
    return body;
  }

  function pagedList(id, fetchPage, row, empty) {
    const panel = $(id);
    const items = panel.querySelector(".items"), paging = panel.querySelector(".paging");
    let generation = 0, page = 1;
    async function load(target = 1) {
      const request = ++generation;
      if (!panel.open || panel.hidden || !state) return;
      items.replaceChildren();
      paging.textContent = "正在读取…";
      try {
        const data = await fetchPage(target);
        if (generation !== request) return;
        page = data.page;
        items.replaceChildren(...data.items.map(row));
        const previous = button("上一页", () => load(page - 1)), next = button("下一页", () => load(page + 1));
        previous.disabled = page <= 1;
        next.disabled = page >= data.pages;
        paging.replaceChildren(element("small", data.total ? "共 " + data.total + " 条 · " + page + " / " + data.pages + " 页" : empty));
        if (data.pages > 1) paging.append(previous, next);
      } catch (error) {
        if (generation === request) paging.replaceChildren(element("span", error.message || "读取失败"), button("重试", () => load(target)));
      }
    }
    panel.addEventListener("toggle", () => load());
    return { load, refresh: () => load(page) };
  }

  function caseRow(item) {
    const label = element("label", "", "case-choice");
    const check = element("input");
    check.type = "checkbox";
    check.value = String(item.serial);
    const parts = [item.direction, [item.primary, item.changed !== item.primary && item.changed].filter(Boolean).join(" → "),
      item.outcomes.length ? item.outcomes.join("、") : "无有效反馈"];
    const desc = element("span");
    desc.append(element("strong", "#" + item.serial + " · " + (item.question || "未记录问题")),
      element("small", parts.filter(Boolean).join(" · ")));
    label.append(check, desc);
    markCase(check);
    check.addEventListener("change", () => {
      try {
        const selected = new Set(readSerials());
        if (check.checked) selected.add(item.serial);
        else selected.delete(item.serial);
        if (selected.size > 3) throw new Error("每轮最多可引用 3 个卦例。");
        $("serials").value = [...selected].map((n) => "#" + n).join("，");
        notice("");
      } catch (error) { notice(error.message, true); }
      syncChecks();
    });
    return label;
  }

  function markCase(check) {
    let selected = [];
    try { selected = readSerials(); } catch { /* Keep the manual input available for correction. */ }
    check.checked = selected.includes(Number(check.value));
    check.disabled = !check.checked && selected.length >= 3;
  }

  function syncChecks() {
    for (const check of $("case-picker").querySelectorAll('input[type="checkbox"]')) markCase(check);
  }

  const caseList = pagedList("case-picker", (page) => bridge.apiGet("optimize/cases", {
    page, search: $("case-search").value.trim(), intent: $("case-intent").value, feedback: $("case-feedback").value,
  }), caseRow, "没有符合条件的卦例");
  const roundList = pagedList("round-history", (page) => bridge.apiGet("optimize/history", { page }),
    historyRow, "还没有优化记录");
  const versionList = pagedList("version-history", (page) => bridge.apiGet("optimize/versions", { page, section: $("section").value }),
    (v) => versionDisclosure($("section").value, v.number,
      ["V" + v.number, versionNames[v.source], v.actor, time(v.at)].filter(Boolean).join(" · "), true),
    "还没有提示词版本");

  function display(snapshot) {
    const selected = $("section").value;
    const oldTop = $("conversation").scrollTop;
    state = snapshot;
    const editable = element("optgroup"), fixed = element("optgroup");
    editable.label = "可提出修改"; fixed.label = "只读资料";
    for (const [key, rule] of Object.entries(state.sections)) {
      (rule.editable ? editable : fixed).append(new Option(rule.label, key));
    }
    $("section").replaceChildren(editable, fixed);
    $("section").disabled = false;
    if (Object.hasOwn(state.sections, selected)) $("section").value = selected;
    if (snapshot.directions) {
      const intent = $("case-intent");
      const old = intent.value;
      intent.replaceChildren(new Option("全部分类", ""), new Option("未分类", "uncategorized"));
      for (const [key, label] of Object.entries(snapshot.directions)) intent.append(new Option(label, key));
      intent.value = old;
    }
    renderRule();
    roundList.refresh();
    versionList.refresh();
    const log = $("conversation");
    log.replaceChildren();
    if (!state.turns.length) {
      log.append(element("p", "可以从一次判断失准的经历开始，或贴入一段想复盘的对话。", "empty"));
    }
    for (const turn of state.turns) {
      const entry = element("article", "", "turn");
      entry.append(element("p", turn.message, "message ask"));
      if (turn.use_cases) entry.append(sources(turn));
      entry.append(element("div", turn.provider, "byline"), element("p", turn.reply, "message"));
      for (const change of turn.changes) entry.append(proposal(change));
      log.append(entry);
    }
    log.scrollTop = oldTop;
    controls();
  }

  async function refresh() {
    let data = await bridge.apiGet("optimize/state");
    while (data.processing) {
      display(data);
      notice("模型仍在处理上一轮对话…");
      await new Promise((resolve) => setTimeout(resolve, 2000));
      data = await bridge.apiGet("optimize/state");
    }
    const provider = $("provider");
    const old = provider.value;
    const available = data.providers;
    provider.replaceChildren(new Option(available.length ? "请选择聊天模型" : "暂无可用的聊天模型", ""));
    for (const item of available) {
      provider.append(new Option(item.model && item.model !== item.id ? item.id + " · " + item.model : item.id, item.id));
    }
    const candidates = [old, data.preferred, available.length === 1 ? available[0].id : ""];
    provider.value = candidates.find((id) => available.some((item) => item.id === id)) || "";
    display(data);
    await caseList.refresh();
  }

  async function run(operation, recover) {
    if (busy) return;
    busy = true;
    controls();
    try {
      await operation();
    } catch (error) {
      // A lost POST response may have followed a successful save. Re-read server state.
      try {
        if (bridge && state) {
          await refresh();
          if (recover?.()) return;
        }
      } catch { /* Keep the last readable state. */ }
      notice(error.message || "操作失败，请刷新后重试。", true);
    } finally {
      busy = false;
      controls();
    }
  }

  function readSerials() {
    if (!$("use-cases").checked || !$("serials").value.trim()) return [];
    const tokens = $("serials").value.trim().split(/[\s,，、]+/);
    if (tokens.length > 3 || tokens.some((s) => !/^[#＃]?[0-9]{1,12}$/.test(s) || Number(s.replace(/^[#＃]/, "")) < 1)) {
      throw new Error("请输入最多 3 个卦例编号，用空格或逗号分隔。");
    }
    return [...new Set(tokens.map((s) => Number(s.replace(/^[#＃]/, ""))))];
  }

  async function send(event) {
    event.preventDefault();
    if (busy || $("send").disabled) return;
    const message = $("message").value.trim();
    const revision = state.revision;
    const provider = $("provider").value;
    function showReply(turn) {
      $("message").value = "";
      const first = turn.changes.find((c) => c.status === "pending");
      if (first) showSection(first.section, first.before);
      $("conversation").lastElementChild?.scrollIntoView({ block: "nearest" });
      notice(first ? "建议已生成。请核对修改前后，再逐项确认。" : "已收到模型回复。");
    }
    await run(async () => {
      const payload = {
        revision, provider, message,
        focus: $("section").value, use_cases: $("use-cases").checked, serials: readSerials(),
      };
      notice("模型正在复盘…");
      const result = await bridge.apiPost("optimize/chat", payload);
      display(result);
      showReply(result.turns.at(-1));
    }, () => {
      const turn = state.turns.find((t) => t.request_revision === revision && t.message === message && t.provider === provider);
      if (turn) showReply(turn);
      return Boolean(turn);
    });
  }

  async function decide(change, action) {
    function showDecision() {
      showSection(change.section, action === "approve" ? change.after : change.before);
      notice(action === "approve" ? "已采用此项，规则版本已保存。" : "已放弃此项。");
    }
    await run(async () => {
      display(await bridge.apiPost("optimize/decision", { id: change.id, action }));
      showDecision();
    }, () => {
      const status = action === "approve" ? "approved" : "dismissed";
      const saved = state.turns.some((t) => t.changes.some((c) => c.id === change.id && c.status === status));
      if (saved) showDecision();
      return saved;
    });
  }

  root.querySelector(".mobile-link").addEventListener("click", (event) => {
    event.preventDefault();
    $("logic").scrollIntoView({ block: "start" });
    $("rule-text").focus({ preventScroll: true });
  });
  $("chat-form").addEventListener("submit", send);
  $("provider").addEventListener("change", controls);
  $("message").addEventListener("input", controls);
  $("message").addEventListener("keydown", (event) => {
    if (!event.isComposing && (event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      if (!$("send").disabled) $("chat-form").requestSubmit();
    }
  });
  $("use-cases").addEventListener("change", () => {
    $("case-options").hidden = !$("use-cases").checked;
    if ($("use-cases").checked && $("case-picker").open) caseList.refresh();
  });
  $("serials").addEventListener("input", syncChecks);
  $("case-find").addEventListener("click", () => caseList.load());
  $("case-search").addEventListener("keydown", (event) => {
    if (!event.isComposing && event.key === "Enter") { event.preventDefault(); caseList.load(); }
  });
  for (const id of ["case-intent", "case-feedback"]) $(id).addEventListener("change", () => caseList.load());
  $("section").addEventListener("change", () => { renderRule(); versionList.load(); });
  $("new-chat").addEventListener("click", () => { $("reset-panel").hidden = false; });
  $("reset-cancel").addEventListener("click", () => { $("reset-panel").hidden = true; });
  $("reset-confirm").addEventListener("click", () => run(async () => {
    display(await bridge.apiPost("optimize/reset", { revision: state.revision }));
    $("message").value = $("serials").value = "";
    $("use-cases").checked = false;
    $("case-picker").open = false;
    $("case-options").hidden = $("reset-panel").hidden = true;
    notice("已开始新对话。");
  }));

  async function connect() {
    if (!bridge) throw new Error("请从 AstrBot 管理后台打开「解卦优化」。");
    await bridge.ready();
    await refresh();
    notice("");
  }
  $("refresh").addEventListener("click", () => run(connect));
  run(connect);
  return () => run(refresh);
}
