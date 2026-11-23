const $ = (id) => document.getElementById(id);
const sources = { case: "指定卦例", manual: "手动排盘", random: "模拟起卦" };
const statuses = { running: "进行中", success: "已解卦", skipped: "仅排盘", failed: "失败", interrupted: "已中断" };
const route = "manage/reading-tests";

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function pager(prefix, page, pages) {
  $(prefix + "page").textContent = `${page} / ${pages}`;
  $(prefix + "prev").disabled = page <= 1;
  $(prefix + "next").disabled = page >= pages;
}
function fullData(title, data) {
  const section = el("div", null, "test-data");
  section.append(el("h4", title));
  for (const [key, value] of Object.entries(data || {})) {
    if (key === "request" && value && typeof value === "object") {
      section.append(fullData(key, value));
    } else {
      section.append(el("b", key), el("pre", typeof value === "string" ? value : JSON.stringify(value, null, 2)));
    }
  }
  return section;
}
function renderReport(row, time) {
  const root = $("test-report"), report = row.report;
  const expanded = new Set(root.dataset.id === String(row.id)
    ? Array.from(root.querySelectorAll("details[open]"), (d) => d.dataset.step) : []);
  root.dataset.id = row.id;
  const title = el("h2", `测试 #${row.id} · ${statuses[row.status] || row.status}`);
  const usage = report.usage || {};
  const tokens = usage.unknown_calls ? `${usage.total_tokens || 0} 已知，${usage.unknown_calls} 次用量未知` : usage.total_tokens || 0;
  const info = el("p", `${sources[row.source]}${row.serial ? " #" + row.serial : ""} · ${time(row.at)} · ${row.actor} · ${usage.calls || 0} 次模型调用 · Token ${tokens}`, "help");
  root.replaceChildren(title, info, el("p", row.question, "test-report-question"));
  if (report.verdict) root.append(el("p", "断语：" + report.verdict, "test-verdict"));
  if (report.text) {
    const result = el("details");
    result.dataset.step = "result";
    result.open = expanded.has("result") || (!report.verdict && row.status !== "success");
    result.append(el("summary", "最终输出"), el("pre", report.text));
    root.append(result);
  } else if (row.status === "interrupted") {
    root.append(el("p", "本次测试在完成前中断，已记录的步骤如下。", "help"));
  } else if (row.status === "failed") {
    root.append(el("p", "本次测试未能完成，请查看最后一个已记录的步骤。", "help"));
  }
  for (const [index, step] of report.steps.entries()) {
    const item = el("article", null, "test-step");
    item.append(el("h3", step.title), el("p", "输入　" + step.input_summary), el("p", "输出　" + step.output_summary));
    const details = el("details");
    details.dataset.step = String(index);
    details.open = expanded.has(String(index));
    details.append(el("summary", "完整输入与输出"), fullData("输入", step.inputs));
    if (step.outputs != null) details.append(fullData("输出", step.outputs));
    item.append(details);
    root.append(item);
  }
}

export function mountReadingTests(bridge, time, makeRequestId, updateCategoryFilter) {
  let active = false, submitting = false, selected = null, submittedId = null, showingRunning = false;
  let casePage = 1, historyPage = 1, caseSeq = 0, historySeq = 0, detailSeq = 0;
  let retry = null, pollTimer, searchTimer, rendered = "";
  const questions = { case: "", manual: "", random: "这个项目能在本月底前按计划完成吗？" };
  let previousSource;
  const notice = (text) => { $("test-notice").textContent = text; };
  const pendingSubmission = () => submitting || (showingRunning && submittedId === selected);
  const buttons = () => {
    $("test-start").disabled = pendingSubmission() ||
      ($("test-source").value === "case" && ($("test-case").disabled || !$("test-case").value));
  };
  async function loadCases(page = 1) {
    const seq = ++caseSeq, select = $("test-case");
    const previous = select.value;
    select.replaceChildren(el("option", "正在查找…"));
    select.disabled = true;
    buttons();
    try {
      const data = await bridge.apiGet("optimize/cases", { search: $("test-search").value.trim(), intent: $("test-category").value, feedback: "all", page });
      if (seq !== caseSeq) return;
      updateCategoryFilter($("test-category"), data.directions);
      select.replaceChildren();
      const prompt = el("option", data.items.length ? "请选择卦例" : "没有匹配的卦例");
      prompt.value = "";
      select.append(prompt);
      for (const c of data.items) {
        const option = el("option", `#${c.serial} · ${c.direction || "未分类"} · ${c.primary || "卦名未记录"} · ${c.question || "未记录问题"}`);
        option.value = c.serial;
        select.append(option);
      }
      if (data.items.some((c) => String(c.serial) === previous)) select.value = previous;
      casePage = data.page;
      pager("test-case-", data.page, data.pages);
      select.disabled = false;
    } catch (error) {
      if (seq !== caseSeq) return;
      const missing = el("option", "卦例读取失败");
      missing.value = "";
      select.replaceChildren(missing);
      notice(error.message || "卦例读取失败，请刷新记录重试。");
    } finally { if (seq === caseSeq) buttons(); }
  }
  function sourceChanged() {
    const source = $("test-source").value;
    if (previousSource) questions[previousSource] = $("test-question").value;
    $("test-question").value = questions[source];
    previousSource = source;
    $("test-case-fields").hidden = source !== "case";
    $("test-case").required = source === "case";
    $("test-manual-fields").hidden = source !== "manual";
    $("test-lines").required = source === "manual";
    $("test-lines").disabled = $("test-at").disabled = source !== "manual";
    $("test-question").placeholder = source === "case" ? "留空沿用所选卦例的问题" : "填写具体所问，留空只排盘";
    buttons();
  }
  async function show(id) {
    const seq = ++detailSeq, previous = selected;
    clearTimeout(pollTimer);
    selected = id;
    if (previous !== id) {
      rendered = "";
      $("test-report").replaceChildren(el("p", "正在读取报告…", "help"));
      for (const b of $("test-history-list").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.id === String(id)));
    }
    try {
      const row = await bridge.apiGet(`${route}/${id}`);
      if (!active || seq !== detailSeq) return;
      const wasRunning = showingRunning && previous === id;
      showingRunning = row.status === "running";
      if (!showingRunning && submittedId === id) submittedId = null;
      buttons();
      const key = `${id}/${row.status}/${row.report.elapsed_ms}/${row.report.steps.length}`;
      if (key !== rendered) { renderReport(row, time); rendered = key; }
      if (showingRunning) pollTimer = setTimeout(() => show(id), 1800);
      else if (wasRunning) await loadHistory(historyPage, false);
    } catch (error) {
      if (seq !== detailSeq || !active) return;
      showingRunning = false; buttons();
      notice(error.message || "报告读取失败，请刷新记录重试。");
    }
  }
  async function loadHistory(page = 1, read = true) {
    const seq = ++historySeq;
    const selectionAtStart = selected;
    try {
      const data = await bridge.apiGet(route, { page });
      if (!active || seq !== historySeq) return;
      historyPage = data.page;
      pager("test-", data.page, data.pages);
      const list = $("test-history-list");
      list.replaceChildren();
      if (!data.items.length) list.append(el("p", "还没有测试记录。", "help"));
      for (const row of data.items) {
        const button = el("button", null, "test-record secondary");
        button.type = "button";
        button.dataset.id = row.id;
        button.setAttribute("aria-pressed", String(row.id === selected));
        button.append(el("strong", `#${row.id} · ${row.question}`), el("small", `${time(row.at)} · ${sources[row.source]} · ${statuses[row.status]}`));
        button.addEventListener("click", () => show(row.id));
        list.append(button);
      }
      if (read && selectionAtStart === selected && (selected || data.items.length)) await show(selected || data.items[0].id);
    } catch (error) {
      if (active && seq === historySeq) notice(error.message || "测试记录读取失败");
    }
  }
  $("test-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (pendingSubmission()) return;
    const source = $("test-source").value;
    const payload = { source, question: $("test-question").value.trim() };
    if (source === "case") payload.serial = $("test-case").value;
    if (source === "manual") Object.assign(payload, { lines: $("test-lines").value.trim(), cast_at: $("test-at").value });
    submitting = true; buttons(); notice("正在提交测试…");
    try {
      const signature = JSON.stringify(payload);
      if (retry?.signature !== signature) retry = { signature, id: makeRequestId() };
      const row = await bridge.apiPost(route + "/start", { ...payload, request_id: retry.id });
      retry = null; submittedId = selected = row.id; rendered = ""; showingRunning = true;
      notice("");
      if (active) await Promise.all([loadHistory(1, false), show(row.id)]);
    } catch (error) { notice(error.message || "提交失败，请重试。"); }
    finally { submitting = false; buttons(); }
  });
  $("test-source").addEventListener("change", sourceChanged);
  $("test-case").addEventListener("change", buttons);
  $("test-category").addEventListener("change", () => { clearTimeout(searchTimer); loadCases(); });
  $("test-search").addEventListener("input", () => {
    caseSeq++; clearTimeout(searchTimer);
    $("test-case").replaceChildren(); buttons();
    searchTimer = setTimeout(() => loadCases(), 250);
  });
  $("test-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault(); clearTimeout(searchTimer); loadCases();
    }
  });
  $("test-case-prev").addEventListener("click", () => loadCases(casePage - 1));
  $("test-case-next").addEventListener("click", () => loadCases(casePage + 1));
  $("test-prev").addEventListener("click", () => loadHistory(historyPage - 1, false));
  $("test-next").addEventListener("click", () => loadHistory(historyPage + 1, false));
  $("test-refresh").addEventListener("click", () => { notice(""); loadHistory(historyPage); loadCases(casePage); });
  sourceChanged();
  return { setActive(value) {
    active = value;
    clearTimeout(pollTimer);
    if (value) { loadHistory(historyPage); loadCases(casePage); }
    else { detailSeq++; historySeq++; }
  } };
}
