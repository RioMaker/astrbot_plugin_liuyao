const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginPage;
let mode = "users", page = 1, pages = 1, generation = 0, busy = false;
let detailGeneration = 0;
const number = (value) => new Intl.NumberFormat("zh-CN").format(value || 0);
const stageNames = { pre_review: "内容预审", interpretation: "解卦", reuse_review: "复解预审", reuse_interpretation: "复解", feedback: "反馈审核", ping: "模型检测", chart_comment: "旧版图卡短评", shefu: "射覆解读" };
function tokenText(usage, historical = false) {
  if (!usage?.tracked && usage?.calls === undefined) return "未记录";
  if (historical && !usage?.tracked) return "历史未记录";
  if (usage?.unknown_calls && !usage.reported_calls) return "用量未知";
  return `${usage?.unknown_calls ? "≥ " : ""}${number(usage?.total_tokens)}`;
}
function tokenBreakdown(usage) {
  return `输入 ${usage.input_unknown_calls ? "≥ " : ""}${number(usage.input_tokens)} · 输出 ${usage.output_unknown_calls ? "≥ " : ""}${number(usage.output_tokens)} · ${number(usage.calls)} 次调用${usage.unknown_calls ? ` · ${number(usage.unknown_calls)} 次用量未知` : ""}`;
}
function tokenFacts(data) {
  const facts = node("div", "", "facts token-facts");
  for (const [key, label] of [["all", "累计 Token"], ["today", "今日 Token"], ["week", "本周 Token"], ["month", "本月 Token"]]) {
    facts.append(fact(label, tokenText(data[key])));
  }
  return facts;
}
function usageSearchPlaceholder() {
  $("search").placeholder = { cases: "搜索编号、问题、QQ 号或群号", users: "搜索 QQ 号或昵称", groups: "搜索群号", daily: "搜索日期，如 2026-11-01", weekly: "搜索周起始日，如 2026-10-26", monthly: "搜索月份，如 2026-11" }[$("usage-view").value];
}
const time = (value) => {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
};
function node(tag, text = "", cls = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  if (cls) element.className = cls;
  return element;
}
function notice(message, error = false, detail = false) {
  const box = $(detail ? "detail-notice" : "notice");
  box.textContent = message;
  box.classList.toggle("error", error);
  box.hidden = !message;
}
function button(text, callback, cls = "secondary") {
  const result = node("button", text, cls);
  result.type = "button";
  result.addEventListener("click", callback);
  return result;
}
function fact(label, value) {
  const box = node("div", "", "fact");
  box.append(node("span", label), node("strong", String(value)));
  return box;
}
function section(title) {
  const result = node("section", "", "section");
  result.append(node("h3", title));
  return result;
}
function field(label, control, wide = false) {
  control.setAttribute("aria-label", label);
  const result = node("label", "", `field${wide ? " wide" : ""}`);
  result.append(node("span", label), control);
  return result;
}
function input(value, maximum, multiline = false) {
  const result = document.createElement(multiline ? "textarea" : "input");
  result.value = value || "";
  result.maxLength = maximum;
  return result;
}
function select(options, selected) {
  const result = document.createElement("select");
  for (const [value, label] of options) {
    const item = node("option", label);
    item.value = value;
    result.append(item);
  }
  result.value = selected;
  return result;
}
function dailyText(user) {
  if (user.daily_unlimited) return "不限";
  if (!user.daily_remaining.length) return "待用户查询";
  return user.daily_remaining.join(" / ");
}
function openModels() {
  return openDetail("模型与调试", "管理员专用模型", () => bridge.apiGet("manage/models"), (data) => {
    const body = $("detail-body"), grid = node("div", "", "grid"), controls = {};
    body.append(node("p", "仅对 AstrBot 管理员的起卦、复解、反馈审核和调试生效。普通用户继续使用插件通用模型；QQ 群主、群管理员身份不触发专用模型。", "help"));
    for (const [key, label] of [["admin_pre_review_provider_id", "管理员预审模型"], ["admin_interpretation_provider_id", "管理员解卦模型"], ["admin_feedback_review_provider_id", "管理员反馈审核模型"]]) {
      const options = [["", "继承通用设置（通用留空则跟随系统）"], ...data.providers.map((p) => [p.id, p.model ? `${p.id} · ${p.model}` : p.id])];
      const value = data.values[key];
      if (value && !data.providers.some((p) => p.id === value)) options.push([value, `${value}（已不可用，请重新选择）`]);
      controls[key] = select(options, value);
      grid.append(field(label, controls[key]));
    }
    body.append(grid);
    body.append(node("p", `通用预审：${data.defaults.command_pre_review_provider_id || "当前会话系统模型"}；通用解卦：${data.defaults.command_interpretation_provider_id || "当前会话系统模型"}；通用反馈：${data.defaults.feedback_review_provider_id || "当前会话系统模型"}。三项独立回退，通用模型可在插件配置中修改。`, "help"));
    if (!data.providers.length) body.append(node("p", "暂无可选聊天模型，请先在 AstrBot 模型提供商中配置。", "help"));
    const actions = node("div", "", "actions");
    actions.append(button("保存管理员模型", () => mutate(() => bridge.apiPost("manage/models/save", {
      revision: data.revision,
      ...Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.value])),
    }), "管理员模型已保存，后续调用立即生效", openModels), ""));
    actions.append(button("重新读取", openModels)); body.append(actions);
    const debug = section("合并转发调试报告");
    debug.append(node("pre", "/六爻test 这个项目能否按期完成", "reading"));
    debug.append(node("p", "由 AstrBot 管理员在已开启六爻的群内发送。结束后仅发一条合并转发：先看结论与 Token 汇总，再按预审、排盘、解卦查看完整调用详情；卦图也包含在报告内。", "help"));
    debug.append(node("p", "调试跳过每日额度和赠额扣减，实际 Token 仍记录在统计中并标记为管理员调试。调试卦不覆盖最近一卦，不进入普通卦例库和待反馈列表。", "help"));
    body.append(debug);
  });
}
async function load() {
  const current = ++generation;
  $("refresh").disabled = true;
  $("count").textContent = "正在加载…";
  try {
    const data = await bridge.apiGet(`manage/${mode}`, {
      search: $("search").value.trim(), page, state: $("state").value,
      ...(mode === "usage" ? {
        view: $("usage-view").value, start: $("usage-start").value, end: $("usage-end").value,
        group: $("usage-group").value.trim(), sender: $("usage-sender").value.trim(),
      } : {}),
    });
    if (current !== generation) return;
    page = data.page; pages = data.pages;
    if (mode === "usage") {
      renderUsageRows(data.items);
      for (const key of ["all", "today", "week", "month"]) $("token-" + key).textContent = tokenText(data.overview[key]);
      $("usage-selected").textContent = `所选日期范围：${tokenText(data.summary)} Token · ${tokenBreakdown(data.summary)} · ${data.summary.casts} 卦。搜索关键词仅筛选下方列表。`;
      $("usage-coverage").textContent = `开始记录：${time(data.started_at)}。上方概览按群号 / QQ 号筛选，日期范围用于下方统计。${data.storage_warning ? "部分用量保存失败，请检查 AstrBot 日志；未知调用未计入已知 Token。" : ""}`;
    } else renderRows(data.items);
    $("count").textContent = `共 ${data.total} 条记录`;
    $("page").textContent = `${page} / ${pages}`;
    $("prev").disabled = page <= 1; $("next").disabled = page >= pages;
    if (data.stats) {
      $("stat-users").textContent = data.stats.users;
      $("stat-banned").textContent = data.stats.banned;
      $("stat-casts").textContent = data.stats.casts;
    } else if (mode === "cases") $("stat-cases").textContent = data.all_count;
  } catch (error) {
    if (current !== generation) return;
    notice(error.message || "读取失败，请重试", true);
    $("count").textContent = "加载失败，请刷新";
    $("prev").disabled = $("next").disabled = true;
  } finally {
    if (current === generation) $("refresh").disabled = false;
  }
}
function renderRows(items) {
  const headers = mode === "users" ? ["用户", "起卦 / 最近使用", "赠送余量", "状态", "累计 Token", "操作"]
    : ["编号 / 起卦时间", "所问与卦象", "用户 / 群", "反馈", "累计 Token", "操作"];
  const head = node("tr"); headers.forEach((text) => head.append(node("th", text)));
  $("table-head").replaceChildren(head);
  $("table-body").replaceChildren();
  $("empty").hidden = items.length > 0;
  $("empty").textContent = $("search").value ? "没有找到匹配记录，试试其他关键词。" : "暂无记录。用户成功起卦后会自动出现在这里。";
  for (const item of items) {
    const row = node("tr");
    const cells = Array.from({ length: 6 }, () => node("td"));
    if (mode === "users") {
      cells[0].append(node("div", item.name || "未记录昵称", "primary"), node("small", `QQ ${item.id}`));
      cells[1].append(node("div", `${item.cast_count} 次`), node("small", time(item.last_cast_at)));
      cells[2].append(node("span", item.gift_remaining ? `+${item.gift_remaining}` : "—", "gift"));
      cells[3].append(node("span", item.banned ? "已封禁" : "正常", `badge${item.banned ? " banned" : ""}`));
      cells[5].append(button("管理用户", () => openUser(item.id), "link-button"));
    } else {
      cells[0].append(node("div", `#${item.serial}`, "primary"), node("small", time(item.created_at)));
      const question = node("div", item.question || "未记录问题", "question primary");
      question.title = item.question;
      cells[1].append(question, node("small", item.primary_name));
      cells[2].append(node("div", `${item.caster_name || "用户"} · ${item.caster_id}`), node("small", `群 ${item.group_id}`));
      cells[3].append(node("span", item.feedback_closed ? "已反馈" : "待反馈", "badge"));
      cells[5].append(button("查看卦例", () => openCase(item.id), "link-button"));
    }
    cells[4].append(node("div", tokenText(item.token_usage, true), "primary"));
    if (item.token_usage?.tracked) cells[4].append(node("small", `${item.token_usage.calls} 次调用${item.token_usage.historical ? " · 仅后续已记录用量" : ""}`));
    row.append(...cells); $("table-body").append(row);
  }
}
async function switchMode(next) {
  mode = next; page = 1; $("search").value = "";
  for (const tab of ["users", "cases", "usage"]) {
    $(`tab-${tab}`).classList.toggle("active", tab === mode);
    $(`tab-${tab}`).setAttribute("aria-pressed", String(tab === mode));
  }
  const options = mode === "users" ? [["all", "全部用户"], ["active", "正常使用"], ["banned", "已封禁"]]
    : [["all", "全部卦例"], ["pending", "待反馈"], ["feedback", "已反馈"]];
  $("state").replaceChildren(...select(options, "all").children);
  $("state").value = "all";
  $("state").parentElement.hidden = mode === "usage";
  for (const id of ["usage-summary", "usage-filters", "usage-selected"]) $(id).hidden = mode !== "usage";
  $("search").placeholder = mode === "users" ? "搜索 QQ 号、昵称或群号" : "搜索编号、问题、QQ 号或备注";
  $("table-hint").textContent = mode === "users" ? "每日额度优先使用；赠送额度跨日保留，用尽后自动隐藏 +n。"
    : "查看原卦与反馈、填写管理备注。删除卦例保留用户、积分、赠送额度与 Token 用量记录。";
  if (mode === "usage") {
    usageSearchPlaceholder();
    $("table-hint").textContent = "北京时间，周一为一周起点。按模型调用开始日期统计；包含预审、解卦、复解、反馈审核和模型检测。缓存 Token 已包含在输入中。仅累计供应商已报告用量，未知不按 0 计算；旧卦仅记录升级后的调用。";
  }
  notice(""); await load();
}
async function openDetail(kicker, title, fetcher, render) {
  if (busy) return;
  const current = ++detailGeneration;
  $("detail-kicker").textContent = kicker; $("detail-title").textContent = title;
  $("detail-body").replaceChildren(node("p", "正在加载…")); notice("", false, true);
  if (!$("detail").open) $("detail").showModal();
  try {
    const data = await fetcher();
    if (current !== detailGeneration) return;
    $("detail-body").replaceChildren(); render(data);
    $("detail").scrollTop = 0;
  } catch (error) { if (current === detailGeneration) notice(error.message, true, true); }
}
async function mutate(action, success, refresh) {
  if (busy) return;
  busy = true;
  const controls = [...$("detail").querySelectorAll("button,input,select,textarea")];
  controls.forEach((control) => { control.disabled = true; });
  notice("正在保存…", false, true);
  try {
    await action();
    busy = false;
    await refresh();
    notice(success, false, $("detail").open);
    await load();
  } catch (error) {
    notice(`${error.message}\n未确认成功时，请先刷新详情核对状态再继续。`, true, true);
  } finally {
    busy = false;
    controls.forEach((control) => { control.disabled = false; });
  }
}
function openUser(id) {
  return openDetail(`用户管理 · QQ ${id}`, "用户详情", () => bridge.apiGet(`manage/users/${encodeURIComponent(id)}`), (user) => {
    $("detail-title").textContent = user.name || `QQ ${id}`;
    const body = $("detail-body"), facts = node("div", "", "facts");
    facts.append(fact("累计起卦", `${user.cast_count} 次`), fact("每日额度余量", dailyText(user)), fact("赠送可用额度", `${user.gift_remaining} 次`));
    body.append(facts, node("p", `首次 ${time(user.first_cast_at)} · 最近 ${time(user.last_cast_at)}`, "help"));
    body.append(node("p", `曾用群：${user.groups.join("、") || "未记录"}`, "help"));
    if (user.token_overview) {
      const tokens = section("Token 消耗");
      tokens.append(tokenFacts(user.token_overview), node("p", tokenBreakdown(user.token_overview.all), "help"), node("p", "仅包含启用记录后的用量，旧版本的历史消耗无法补回。", "help"), button("查看该用户用量明细", () => usageFor("sender", id)));
      body.append(tokens);
    }
    if (user.gift_reserved) body.append(node("p", `另有 ${user.gift_reserved} 次赠送额度已被进行中的请求预留。`, "help"));
    if (user.daily_remaining.length > 1) body.append(node("p", "每日余量按已记录的机器人平台实例依次列出；赠送额度与封禁按 QQ 账号共享。", "help"));
    const grant = section("赠送额度"), form = node("form"), grid = node("div", "", "grid");
    const amount = input("1", 5); amount.type = "number"; amount.min = "1"; amount.max = "10000"; amount.step = "1"; amount.required = true;
    const note = input("", 300);
    grid.append(field("赠送次数", amount), field("赠送说明（可选）", note));
    const submit = node("button", "赠送额度"); submit.type = "submit";
    const actions = node("div", "", "actions"); actions.append(submit);
    form.append(grid, actions); grant.append(form, node("p", "赠送额度不在每日重置时清零。每天先用每日额度，再用赠送额度。", "help"));
    let operation = null;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = Number(amount.value);
      if (!Number.isInteger(value) || value < 1 || value > 10000) return;
      const signature = JSON.stringify([value, note.value.trim()]);
      if (!operation || operation.signature !== signature) operation = { signature, id: crypto.randomUUID() };
      mutate(() => bridge.apiPost(`manage/users/${encodeURIComponent(id)}/grant`, {
        amount: value, note: note.value.trim(), request_id: operation.id,
      }), `已赠送 ${value} 次额度`, () => openUser(id));
    });
    body.append(grant);
    const settings = section("使用权限与备注"), settingsGrid = node("div", "", "grid");
    const status = select([["active", "正常使用"], ["banned", "封禁：禁止使用全部六爻功能"]], user.banned ? "banned" : "active");
    const reason = input(user.ban_reason, 300), memo = input(user.note, 1000, true);
    settingsGrid.append(field("用户状态", status), field("封禁原因（管理员可见）", reason), field("用户备注", memo, true));
    const saveActions = node("div", "", "actions");
    saveActions.append(button("保存用户设置", () => mutate(() => bridge.apiPost(`manage/users/${encodeURIComponent(id)}/update`, {
      banned: status.value === "banned", ban_reason: reason.value, note: memo.value, revision: user.revision,
    }), "用户设置已保存", () => openUser(id)), ""));
    saveActions.append(button("刷新详情", () => openUser(id)));
    settings.append(settingsGrid, saveActions, node("p", "封禁覆盖起卦、复解、反馈、额度查询、模型检测和 AI 工具；额度白名单也不能绕过封禁。", "help"));
    body.append(settings);
    const history = section("赠送记录"), list = node("ul", "", "history");
    for (const row of user.grants) list.append(node("li", `${time(row.at)} · +${row.amount} 次 · ${row.actor}${row.note ? ` · ${row.note}` : ""}`));
    history.append(user.grants.length ? list : node("p", "暂无赠送记录", "help")); body.append(history);
    if (user.audit.length) {
      const audit = section("最近管理记录"), records = node("ul", "", "history");
      for (const row of [...user.audit].reverse()) records.append(node("li", `${time(row.at)} · ${row.actor} · ${row.banned ? "封禁" : "正常"}${row.reason ? ` · ${row.reason}` : ""}`));
      audit.append(records); body.append(audit);
    }
  });
}
function openCase(id) {
  return openDetail("卦例管理", "卦例详情", () => bridge.apiGet(`manage/cases/${encodeURIComponent(id)}`), ({ case: record, revision, token_usage: usage, usage_id }) => {
    $("detail-title").textContent = `卦例 #${record.serial}`;
    const body = $("detail-body"), cast = record.cast || {}, facts = node("div", "", "facts");
    facts.append(fact("本卦 → 变卦", `${cast.primary_name || "—"} → ${cast.changed_name || "—"}`), fact("起卦用户", `${record.caster_name || ""} ${record.caster_id}`), fact("群号", record.group_id));
    body.append(facts, node("p", `${time(record.created_at)} · ${record.method || ""} · ${record.intent_label || ""}`, "help"));
    const tokens = section("本卦 Token 消耗");
    tokens.append(fact("累计（含复解与反馈审核）", tokenText(usage, true)));
    if (usage?.tracked) tokens.append(node("p", tokenBreakdown(usage), "help"), button("查看调用明细", () => openUsage(usage_id)));
    else tokens.append(node("p", "本卦尚无 Token 记录，旧版本的历史消耗无法补回。", "help"));
    body.append(tokens);
    for (const [label, text] of [["所问", record.question], ["原断结论", record.verdict], ["完整解读", record.analysis]]) {
      const part = section(label); part.append(node("div", text || "未记录", "reading")); body.append(part);
    }
    const chart = section("原卦记录");
    chart.append(node("p", `六爻（初爻至上爻）：${(cast.lines || []).join("、")}；动爻：${(cast.moving_lines || []).join("、") || "无"}`, "help"));
    const raw = node("details"); raw.append(node("summary", "展开完整排盘数据"), node("pre", JSON.stringify(cast.chart || cast, null, 2), "reading")); chart.append(raw); body.append(chart);
    const feedback = section("实际反馈");
    if (!(record.feedback || []).length) feedback.append(node("p", "尚未反馈", "help"));
    for (const row of record.feedback || []) {
      feedback.append(node("p", `${time(row.observed_at)} · ${row.outcome || "未分类"}${row.valid === false ? " · 未通过" : ""}`, "help"), node("div", row.text || row.answer || "未记录正文", "reading"));
    }
    body.append(feedback);
    const memo = section("管理备注"), text = input(record.admin_note, 2000, true);
    memo.append(field("仅用于管理，不改写原断或反馈", text), button("保存备注", () => mutate(() => bridge.apiPost(`manage/cases/${encodeURIComponent(id)}/update`, {
      action: "note", note: text.value, revision,
    }), "卦例备注已保存", () => openCase(id)))); body.append(memo);
    const deletion = section("删除卦例"), confirmation = node("div", "", "confirm-delete"); confirmation.hidden = true;
    confirmation.append(node("p", `确定删除卦例 #${record.serial}？删除后不会再出现在卦例库和反馈列表中。用户、额度、奖励及 Token 用量记录（含问题摘要）会保留。`));
    confirmation.append(button("确认删除这条卦例", () => mutate(() => bridge.apiPost(`manage/cases/${encodeURIComponent(id)}/update`, {
      action: "delete", revision,
    }), `已删除卦例 #${record.serial}`, async () => { $("detail").close(); }), "danger"));
    deletion.append(button("删除卦例…", () => { confirmation.hidden = !confirmation.hidden; }, "danger"), confirmation); body.append(deletion);
  });
}
function renderUsageRows(items) {
  const view = $("usage-view").value;
  const title = { cases: "卦例 / 用户 / 群", groups: "群号", users: "用户", daily: "日期", weekly: "周起始日", monthly: "月份" }[view];
  const head = node("tr");
  for (const label of [title, "总 Token", "输入 Token", "输出 Token", "模型调用", "用量未知", "明细"]) head.append(node("th", label));
  $("table-head").replaceChildren(head); $("table-body").replaceChildren();
  $("empty").hidden = items.length > 0;
  $("empty").textContent = "此筛选范围内暂无用量记录。旧版本的 Token 消耗无法补回。";
  for (const item of items) {
    const row = node("tr"), cells = Array.from({ length: 7 }, () => node("td")), usage = item.usage;
    if (view === "cases") {
      const title = item.serial ? `卦例 #${item.serial}` : item.kind === "debug" ? "管理员调试" : item.kind === "ping" ? "模型检测" : "未留存卦例";
      const question = node("div", item.question || title, "question"); question.title = item.question;
      cells[0].append(node("div", title, "primary"), question, node("small", `${time(item.created_at)} · QQ ${item.caster_id} · 群 ${item.group_id || "无"}`));
      cells[6].append(button("调用明细", () => openUsage(item.id), "link-button"));
    } else if (view === "groups" || view === "users") {
      cells[0].append(node("div", view === "users" ? item.caster_name || `QQ ${item.id}` : `群 ${item.id || "无群号"}`, "primary"));
      if (view === "users") cells[0].append(node("small", `QQ ${item.id}`));
      cells[0].append(node("small", `${usage.casts} 卦`));
      cells[6].append(button("查看消耗", () => usageFor(view === "groups" ? "group" : "sender", item.id), "link-button"));
    } else {
      cells[0].append(node("div", view === "monthly" ? item.id.slice(0, 7) : item.id, "primary"), node("small", `${usage.casts} 卦`));
      cells[6].textContent = "—";
    }
    cells[1].append(node("strong", tokenText(usage)));
    if (view === "cases" && usage.historical && item.kind !== "ping") cells[1].append(node("small", "仅后续已记录用量"));
    cells[2].append(node("div", `${usage.input_unknown_calls ? "≥ " : ""}${number(usage.input_tokens)}`), node("small", `已知缓存 ${number(usage.cached_tokens)}`));
    cells[3].textContent = `${usage.output_unknown_calls ? "≥ " : ""}${number(usage.output_tokens)}`;
    cells[4].textContent = number(usage.calls);
    cells[5].append(node("span", `${usage.unknown_calls} 次`, usage.unknown_calls ? "badge banned" : "badge"));
    row.append(...cells); $("table-body").append(row);
  }
}
async function usageFor(key, id) {
  $("detail").close();
  $("usage-" + key).value = id;
  $("usage-view").value = "cases";
  await switchMode("usage");
}
function openUsage(id) {
  return openDetail("Token 消耗", "调用明细", () => bridge.apiGet(`manage/usage/${encodeURIComponent(id)}`), (data) => {
    const { record, calls, stages } = data, body = $("detail-body"), usage = record.usage;
    $("detail-title").textContent = record.serial ? `卦例 #${record.serial} · Token 消耗` : "Token 调用明细";
    const facts = node("div", "", "facts");
    facts.append(fact("累计 Token", tokenText(usage)), fact("模型调用", `${usage.calls} 次`), fact("用量未知", `${usage.unknown_calls} 次`));
    body.append(facts, node("p", `${record.question} · QQ ${record.caster_id} · 群 ${record.group_id || "无"}`, "help"));
    if (usage.historical && record.kind !== "ping") body.append(node("p", "仅包含启用记录后的调用，原卦的历史消耗未补记。", "help"));
    body.append(node("p", "预审、解卦、复解和反馈审核均累计到原卦。缓存输入已包含在输入和总量中。用量未知可能来自超时、取消、处理中或供应商未返回用量数据，不代表免费。", "help"));
    const stagesSection = section("按调用阶段汇总");
    for (const [stage, stats] of Object.entries(stages)) stagesSection.append(node("p", `${stageNames[stage] || stage}：${tokenText(stats)} Token · ${tokenBreakdown(stats)}`, "help"));
    body.append(stagesSection);
    const history = section(`调用记录（共 ${data.call_count} 次${data.call_count > calls.length ? "，显示最近 100 次" : ""}）`), list = node("ul", "", "history token-history");
    for (const call of calls) {
      const entry = node("li"), tokens = call.usage;
      const status = { returned: "已返回", error: "调用错误", cancelled: "超时 / 已取消", pending: "待确认" }[call.status] || call.status;
      entry.append(node("div", `${time(call.at)} · ${stageNames[call.stage] || call.stage} · ${status}`, "primary"));
      entry.append(node("div", `模型配置：${call.provider || "未记录"} · 总量 ${tokens?.total_tokens == null ? "未知" : number(tokens.total_tokens)}`));
      if (call.elapsed_ms != null) entry.append(node("div", `调用耗时：${number(call.elapsed_ms)} ms`));
      entry.append(node("div", `输入 ${tokens?.input_tokens == null ? "未知" : number(tokens.input_tokens)} · 输出 ${tokens?.output_tokens == null ? "未知" : number(tokens.output_tokens)} · 输入中的缓存 ${tokens?.cached_tokens == null ? "未知" : number(tokens.cached_tokens)}`));
      list.append(entry);
    }
    history.append(calls.length ? list : node("p", "本卦没有调用模型，Token 消耗为 0。", "help")); body.append(history);
    if (record.case_id) body.append(button("查看保存的卦例", () => openCase(record.case_id)), node("p", "卦例删除或清理后仍保留用量记录。", "help"));
  });
}
$("close-detail").addEventListener("click", () => { if (!busy) $("detail").close(); });
$("detail").addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
$("detail").addEventListener("close", () => { detailGeneration++; });
$("tab-users").addEventListener("click", () => switchMode("users"));
$("tab-cases").addEventListener("click", () => switchMode("cases"));
$("tab-usage").addEventListener("click", () => switchMode("usage"));
$("models").addEventListener("click", openModels);
$("usage-view").addEventListener("change", () => { page = 1; $("search").value = ""; usageSearchPlaceholder(); notice(""); load(); });
$("usage-reset").addEventListener("click", () => {
  for (const key of ["start", "end", "group", "sender"]) $("usage-" + key).value = "";
  $("search").value = ""; page = 1; notice(""); load();
});
$("refresh").addEventListener("click", () => { notice(""); load(); });
$("search-form").addEventListener("submit", (event) => { event.preventDefault(); page = 1; notice(""); load(); });
$("state").addEventListener("change", () => { page = 1; load(); });
$("prev").addEventListener("click", () => { if (page > 1) { page--; load(); } });
$("next").addEventListener("click", () => { if (page < pages) { page++; load(); } });
try {
  if (!bridge) throw new Error("请在 AstrBot WebUI → 插件 → 六爻起卦 → 用户与卦例管理 中打开此页面。");
  await bridge.ready();
  await load();
  const data = await bridge.apiGet("manage/cases", { page: 1 });
  $("stat-cases").textContent = data.all_count;
} catch (error) { notice(error.message, true); $("count").textContent = "尚未连接"; }
