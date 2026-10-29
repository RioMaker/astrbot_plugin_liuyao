const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginPage;
let mode = "users", page = 1, pages = 1, generation = 0, busy = false;
let detailGeneration = 0;
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
async function load() {
  const current = ++generation;
  $("refresh").disabled = true;
  $("count").textContent = "正在加载…";
  try {
    const data = await bridge.apiGet(`manage/${mode}`, {
      search: $("search").value.trim(), page, state: $("state").value,
    });
    if (current !== generation) return;
    page = data.page; pages = data.pages;
    renderRows(data.items);
    $("count").textContent = `共 ${data.total} 条记录`;
    $("page").textContent = `${page} / ${pages}`;
    $("prev").disabled = page <= 1; $("next").disabled = page >= pages;
    if (data.stats) {
      $("stat-users").textContent = data.stats.users;
      $("stat-banned").textContent = data.stats.banned;
      $("stat-casts").textContent = data.stats.casts;
    } else $("stat-cases").textContent = data.all_count;
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
  const headers = mode === "users" ? ["用户", "起卦 / 最近使用", "赠送余量", "状态", "操作"]
    : ["编号 / 起卦时间", "所问与卦象", "用户 / 群", "反馈", "操作"];
  const head = node("tr"); headers.forEach((text) => head.append(node("th", text)));
  $("table-head").replaceChildren(head);
  $("table-body").replaceChildren();
  $("empty").hidden = items.length > 0;
  $("empty").textContent = $("search").value ? "没有找到匹配记录，试试其他关键词。" : "暂无记录。用户成功起卦后会自动出现在这里。";
  for (const item of items) {
    const row = node("tr");
    const cells = Array.from({ length: 5 }, () => node("td"));
    if (mode === "users") {
      cells[0].append(node("div", item.name || "未记录昵称", "primary"), node("small", `QQ ${item.id}`));
      cells[1].append(node("div", `${item.cast_count} 次`), node("small", time(item.last_cast_at)));
      cells[2].append(node("span", item.gift_remaining ? `+${item.gift_remaining}` : "—", "gift"));
      cells[3].append(node("span", item.banned ? "已封禁" : "正常", `badge${item.banned ? " banned" : ""}`));
      cells[4].append(button("管理用户", () => openUser(item.id), "link-button"));
    } else {
      cells[0].append(node("div", `#${item.serial}`, "primary"), node("small", time(item.created_at)));
      const question = node("div", item.question || "未记录问题", "question primary");
      question.title = item.question;
      cells[1].append(question, node("small", item.primary_name));
      cells[2].append(node("div", `${item.caster_name || "用户"} · ${item.caster_id}`), node("small", `群 ${item.group_id}`));
      cells[3].append(node("span", item.feedback_closed ? "已反馈" : "待反馈", "badge"));
      cells[4].append(button("查看卦例", () => openCase(item.id), "link-button"));
    }
    row.append(...cells); $("table-body").append(row);
  }
}
async function switchMode(next) {
  mode = next; page = 1; $("search").value = "";
  for (const tab of ["users", "cases"]) {
    $(`tab-${tab}`).classList.toggle("active", tab === mode);
    $(`tab-${tab}`).setAttribute("aria-pressed", String(tab === mode));
  }
  const options = mode === "users" ? [["all", "全部用户"], ["active", "正常使用"], ["banned", "已封禁"]]
    : [["all", "全部卦例"], ["pending", "待反馈"], ["feedback", "已反馈"]];
  $("state").replaceChildren(...select(options, "all").children);
  $("state").value = "all";
  $("search").placeholder = mode === "users" ? "搜索 QQ 号、昵称或群号" : "搜索编号、问题、QQ 号或备注";
  $("table-hint").textContent = mode === "users" ? "每日额度优先使用；赠送额度跨日保留，用尽后自动隐藏 +n。"
    : "查看原卦与反馈、填写管理备注。删除卦例保留用户、积分与赠送额度。";
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
  return openDetail("卦例管理", "卦例详情", () => bridge.apiGet(`manage/cases/${encodeURIComponent(id)}`), ({ case: record, revision }) => {
    $("detail-title").textContent = `卦例 #${record.serial}`;
    const body = $("detail-body"), cast = record.cast || {}, facts = node("div", "", "facts");
    facts.append(fact("本卦 → 变卦", `${cast.primary_name || "—"} → ${cast.changed_name || "—"}`), fact("起卦用户", `${record.caster_name || ""} ${record.caster_id}`), fact("群号", record.group_id));
    body.append(facts, node("p", `${time(record.created_at)} · ${record.method || ""} · ${record.intent_label || ""}`, "help"));
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
    confirmation.append(node("p", `确定删除卦例 #${record.serial}？删除后不会再出现在卦例库和反馈列表中。用户、额度和已获得的奖励会保留。`));
    confirmation.append(button("确认删除这条卦例", () => mutate(() => bridge.apiPost(`manage/cases/${encodeURIComponent(id)}/update`, {
      action: "delete", revision,
    }), `已删除卦例 #${record.serial}`, async () => { $("detail").close(); }), "danger"));
    deletion.append(button("删除卦例…", () => { confirmation.hidden = !confirmation.hidden; }, "danger"), confirmation); body.append(deletion);
  });
}
$("close-detail").addEventListener("click", () => { if (!busy) $("detail").close(); });
$("detail").addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
$("detail").addEventListener("close", () => { detailGeneration++; });
$("tab-users").addEventListener("click", () => switchMode("users"));
$("tab-cases").addEventListener("click", () => switchMode("cases"));
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
