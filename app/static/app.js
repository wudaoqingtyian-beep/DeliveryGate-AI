/* DeliveryGate AI 前端工作台
 * 纯原生 JS：只调用后端既有 API，不在前端重复实现核验规则。
 * 安全约定：动态内容一律 textContent / createElement 渲染，杜绝未转义的 HTML 注入。
 */
"use strict";

/* ---------- 基础工具 ---------- */

function $(id) {
  return document.getElementById(id);
}

/** 创建元素：attrs.class / attrs.title 等，文本用 textContent。 */
function el(tag, attrs, text) {
  var node = document.createElement(tag);
  if (attrs) {
    Object.keys(attrs).forEach(function (k) {
      if (k === "class") node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
  }
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/** 统一 fetch 封装：解析 {error:{code,message}}，网络/非 JSON 也有明确错误。 */
async function api(path, options) {
  var resp;
  try {
    resp = await fetch(path, options);
  } catch (e) {
    var netErr = new Error("无法连接服务，请确认服务已启动后重试。");
    netErr.code = "NETWORK_ERROR";
    throw netErr;
  }
  var isJson = (resp.headers.get("content-type") || "").indexOf("application/json") !== -1;
  var data = null;
  if (isJson) {
    try {
      data = await resp.json();
    } catch (e) {
      data = null;
    }
  }
  if (!resp.ok) {
    var err = new Error(
      data && data.error && data.error.message
        ? data.error.message
        : "请求失败（HTTP " + resp.status + "），服务返回了非预期响应。"
    );
    err.code = data && data.error && data.error.code ? data.error.code : "HTTP_" + resp.status;
    err.status = resp.status;
    throw err;
  }
  if (data === null) {
    var err2 = new Error("服务返回了非 JSON 响应，无法处理。");
    err2.code = "BAD_RESPONSE";
    throw err2;
  }
  return data;
}

/* ---------- 展示用固定映射（仅显示，不参与业务判断） ---------- */

var TOOL_LABELS = {
  check_required_fields: "必填资料检查",
  compare_environment_config: "环境配置核对",
  compare_api_acceptance: "接口与验收要求核对",
  inspect_acceptance_coverage: "验收覆盖检查"
};

var RUN_STATUS_LABELS = {
  queued: "排队中",
  running: "执行中",
  succeeded: "已完成",
  model_error: "模型调用失败",
  tool_error: "工具执行失败",
  failed: "执行失败",
  internal_error: "内部错误"
};

var FINAL_STATUS_LABELS = {
  READY: "通过核验（交付就绪）",
  NEEDS_REVIEW: "需进一步检查或人工确认",
  BLOCKED: "存在阻塞问题"
};

var SEVERITY_LABELS = { blocker: "Blocker", warning: "Warning" };

/** final_status → 徽章样式 class（颜色 + 文字标签同时呈现）。 */
function finalStatusBadge(finalStatus) {
  var cls = "badge badge-unknown";
  if (finalStatus === "READY") cls = "badge badge-ready";
  else if (finalStatus === "NEEDS_REVIEW") cls = "badge badge-review";
  else if (finalStatus === "BLOCKED") cls = "badge badge-blocked";
  return el("span", { class: cls }, finalStatus);
}

/** 执行状态 → 徽章样式 class。 */
function runStatusBadge(runStatus) {
  var cls = "badge badge-neutral";
  if (runStatus === "succeeded") cls = "badge badge-ready";
  else if (runStatus === "model_error" || runStatus === "tool_error" ||
           runStatus === "failed" || runStatus === "internal_error") {
    cls = "badge badge-blocked";
  }
  return el("span", { class: cls }, runStatus);
}

/** mode → 徽章样式。 */
function modeBadge(mode) {
  var cls = mode === "real" ? "badge badge-blocked" : "badge badge-neutral";
  return el("span", { class: cls }, mode);
}

/* ---------- 服务状态（GET /api/health） ---------- */

async function loadHealth() {
  var badge = $("health-status");
  var hint = $("health-hint");
  try {
    var data = await api("/api/health");
    badge.textContent = data.status === "ok" ? "在线（" + data.service + "）" : "状态异常";
    badge.className = data.status === "ok" ? "badge badge-ready" : "badge badge-blocked";
    hint.textContent = "";
  } catch (e) {
    badge.textContent = "离线 / 不可达";
    badge.className = "badge badge-blocked";
    hint.textContent = "健康检查失败：" + e.message;
  }
}

/* ---------- 场景列表（GET /api/scenarios） ---------- */

async function loadScenarios() {
  var select = $("scenario-select");
  select.disabled = true;
  select.textContent = "";
  select.appendChild(el("option", { value: "" }, "加载中…"));
  try {
    var data = await api("/api/scenarios");
    var scenarios = data.scenarios || [];
    select.textContent = "";
    if (scenarios.length === 0) {
      select.appendChild(el("option", { value: "" }, "（服务未返回任何场景）"));
      setRunStatus("场景列表为空，请检查服务状态。", true);
      return;
    }
    scenarios.forEach(function (s) {
      var text = s.id + " —— " + s.name + "（期望 " + s.expected_status + "）";
      select.appendChild(el("option", { value: s.id }, text));
    });
    select.disabled = false;
    setRunStatus("");
  } catch (e) {
    select.textContent = "";
    select.appendChild(el("option", { value: "" }, "场景加载失败"));
    setRunStatus("场景列表加载失败：" + e.message, true);
  }
}

/* ---------- 状态提示 ---------- */

function setRunStatus(text, isError) {
  var p = $("run-status");
  p.textContent = text || "";
  p.className = isError ? "inline-status is-error" : "inline-status";
}

function setQueryStatus(text, isError) {
  var p = $("query-status");
  p.textContent = text || "";
  p.className = isError ? "inline-status is-error" : "inline-status";
}

/** 已知错误码 → 更具体的用户提示（后端 message 保留在前）。 */
function friendlyError(e) {
  var extra = "";
  switch (e.code) {
    case "REAL_MODEL_CONFIG_MISSING":
      extra = "当前服务端缺少真实模型配置（由服务端环境变量提供）。请先在服务端配置后再使用真实模型模式。";
      break;
    case "SCENARIO_NOT_FOUND":
      extra = "请选择列表中的有效核验场景。";
      break;
    case "RUN_NOT_FOUND":
      extra = "请确认任务 ID 是否正确，或先发起一次核验。";
      break;
    case "REPORT_NOT_READY":
      extra = "该任务尚未完成执行，暂时没有最终报告，可先查看任务详情。";
      break;
    case "VALIDATION_ERROR":
      extra = "请求参数不合法，请检查所选场景与模式后重试。";
      break;
    case "NETWORK_ERROR":
      extra = "请检查网络或服务是否仍在运行。";
      break;
    default:
      extra = "";
  }
  return (e.message || "未知错误") + (extra ? "\n" + extra : "");
}

function showRunError(e) {
  $("run-error").hidden = false;
  $("run-error-message").textContent = friendlyError(e);
}

function hideRunError() {
  $("run-error").hidden = true;
  $("run-error-message").textContent = "";
}

/* ---------- 结果渲染 ---------- */

function metaItem(label, valueNode) {
  var item = el("div", { class: "meta-item" });
  item.appendChild(el("span", { class: "meta-key" }, label));
  if (typeof valueNode === "string" || typeof valueNode === "number") {
    item.appendChild(el("span", {}, String(valueNode)));
  } else if (valueNode) {
    item.appendChild(valueNode);
  } else {
    item.appendChild(el("span", {}, "—"));
  }
  return item;
}

/** 由 findings 统计各等级数量（仅用于展示计数，不做状态判定）。 */
function summarizeFindings(findings) {
  var summary = { total: findings.length, blocker: 0, warning: 0, other: 0 };
  findings.forEach(function (f) {
    if (f.severity === "blocker") summary.blocker += 1;
    else if (f.severity === "warning") summary.warning += 1;
    else summary.other += 1;
  });
  return summary;
}

/** 渲染核验结果：元信息、模型解释、部分结果提示、findings、工具记录。 */
function renderReport(data, sourceLabel) {
  $("result-section").hidden = false;
  $("result-source").textContent = sourceLabel;

  // 报告用 run_status，详情用 status——统一为执行状态展示。
  var runStatus = data.run_status || data.status || "";

  var grid = $("meta-grid");
  grid.textContent = "";
  grid.appendChild(metaItem("任务 ID（run_id）", data.run_id));
  grid.appendChild(metaItem("运行模式", modeBadge(data.model_mode)));
  grid.appendChild(metaItem("模型名称", data.model_name || "—"));
  var runStatusWrap = el("span");
  runStatusWrap.appendChild(runStatusBadge(runStatus));
  runStatusWrap.appendChild(document.createTextNode(" "));
  runStatusWrap.appendChild(el("span", {}, RUN_STATUS_LABELS[runStatus] || ""));
  grid.appendChild(metaItem("执行状态", runStatusWrap));
  var finalWrap = el("span");
  finalWrap.appendChild(finalStatusBadge(data.final_status));
  finalWrap.appendChild(document.createTextNode(" "));
  finalWrap.appendChild(el("span", {}, FINAL_STATUS_LABELS[data.final_status] || ""));
  grid.appendChild(metaItem("最终交付状态", finalWrap));
  grid.appendChild(metaItem("核验场景（delivery_id）", data.delivery_id || "—"));
  grid.appendChild(metaItem("创建时间", data.created_at || "—"));
  grid.appendChild(metaItem("完成时间", data.finished_at || "—"));
  grid.appendChild(metaItem("耗时（毫秒）", data.duration_ms !== undefined && data.duration_ms !== null
    ? data.duration_ms : "—"));
  grid.appendChild(metaItem("未完成的必需检查", data.required_checks_incomplete !== undefined
    ? data.required_checks_incomplete : "—"));
  if (data.counts && typeof data.counts === "object") {
    grid.appendChild(metaItem("后端统计（counts）",
      "blockers " + (data.counts.blockers !== undefined ? data.counts.blockers : "—")
      + " / warnings " + (data.counts.warnings !== undefined ? data.counts.warnings : "—")
      + " / pass " + (data.counts.pass !== undefined ? data.counts.pass : "—")));
  }
  if (data.summary) {
    grid.appendChild(metaItem("报告总结", data.summary));
  }

  var partial = $("partial-warning");
  if (runStatus && runStatus !== "succeeded") {
    partial.hidden = false;
    partial.textContent = "注意：本次任务执行状态为「" + runStatus + "」（"
      + (RUN_STATUS_LABELS[runStatus] || "未知")
      + "），核验流程未正常完成。以下展示的是后端已返回的检查结果；"
      + "执行未完成不等于交付就绪，也不等于核验通过。";
  } else {
    partial.hidden = true;
    partial.textContent = "";
  }

  // 模型解释（仅展示，绝不作为状态依据）
  var oldExplain = $("result-section").querySelector("details.explain");
  if (oldExplain) oldExplain.remove();
  if (data.model_explanation) {
    var details = el("details", { class: "explain" });
    details.appendChild(el("summary", {}, "模型解释（仅供参考，最终状态由确定性规则计算）"));
    details.appendChild(el("pre", { class: "code-block" }, data.model_explanation));
    $("meta-grid").parentNode.insertBefore(details, partial);
  }

  // --- Findings 表格（保留 id → tool_event_id → evidence.source 追溯链）---
  var findings = data.findings || [];
  var findingsBox = $("findings-container");
  findingsBox.textContent = "";
  var summary = summarizeFindings(findings);
  $("findings-summary").textContent = findings.length === 0
    ? "共 0 条问题。"
    : "共 " + findings.length + " 条：Blocker " + summary.blocker
      + " 条，Warning " + summary.warning + " 条，其他 " + summary.other + " 条。";

  if (findings.length === 0) {
    findingsBox.appendChild(el("p", { class: "empty-state" },
      "本次核验没有发现问题（无 findings）。这可能表示所有检查通过，也可能与执行状态有关，请结合上方执行状态判断。"));
  } else {
    var wrap = el("div", { class: "table-wrap" });
    var table = el("table", { class: "data-table" });
    var thead = el("thead");
    var headRow = el("tr");
    ["ID", "等级", "代码", "摘要", "确定程度", "预期值", "实际值", "证据来源", "整改建议", "关联工具 / Tool Event"]
      .forEach(function (h) { headRow.appendChild(el("th", { scope: "col" }, h)); });
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = el("tbody");
    findings.forEach(function (f) {
      var evidence = f.evidence || {};
      var tr = el("tr");
      tr.appendChild(el("td", {}, f.id || "—"));
      var sevCell = el("td");
      var sevCls = f.severity === "blocker" ? "sev-blocker"
        : f.severity === "warning" ? "sev-warning" : "sev-info";
      sevCell.appendChild(el("span", { class: "badge " + sevCls },
        (SEVERITY_LABELS[f.severity] || f.severity || "—")));
      tr.appendChild(sevCell);
      tr.appendChild(el("td", {}, f.code || "—"));
      tr.appendChild(el("td", { class: "cell-wrap" }, f.message || "—"));
      tr.appendChild(el("td", {}, f.certainty || "—"));
      tr.appendChild(el("td", { class: "cell-wrap" }, evidence.expected !== undefined && evidence.expected !== null
        ? String(evidence.expected) : "—"));
      tr.appendChild(el("td", { class: "cell-wrap" }, evidence.actual !== undefined && evidence.actual !== null
        ? String(evidence.actual) : "—"));
      tr.appendChild(el("td", { class: "cell-wrap" }, evidence.source || "—"));
      tr.appendChild(el("td", { class: "cell-wrap" }, f.remediation || "—"));
      tr.appendChild(el("td", { class: "cell-wrap" },
        (f.tool || "—") + " / " + (f.tool_event_id || "—")));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    findingsBox.appendChild(wrap);
  }

  // --- 工具执行记录（可展开详情）---
  renderToolEvents(data.tool_events || []);
}



/** 渲染工具执行事件列表；列表顺序即执行顺序（后端按执行顺序返回）。 */
function renderToolEvents(events) {
  var box = $("tools-container");
  box.textContent = "";
  if (events.length === 0) {
    box.appendChild(el("p", { class: "empty-state" }, "本次任务没有工具执行记录。"));
    return;
  }
  events.forEach(function (ev, idx) {
    var item = el("details", { class: "event-item" });
    var summary = el("summary");
    summary.appendChild(el("span", { class: "mono" }, "#" + (idx + 1)));
    var toolLabel = TOOL_LABELS[ev.tool] || ev.tool;
    summary.appendChild(el("span", {}, toolLabel));
    summary.appendChild(el("span", { class: "mono" }, ev.tool || "—"));
    summary.appendChild(runStatusBadge(ev.status));
    if (ev.error_code) {
      summary.appendChild(el("span", { class: "badge badge-blocked" }, ev.error_code));
    }
    item.appendChild(summary);

    var body = el("div", { class: "event-body" });
    body.appendChild(eventField("事件 ID（Tool Event ID）",
      el("span", { class: "mono" }, ev.tool_event_id || "—")));
    body.appendChild(eventField("执行顺序", "#" + (idx + 1) + "（按列表顺序）"));
    body.appendChild(eventField("工具标识", el("span", { class: "mono" }, ev.tool || "—")));
    body.appendChild(eventField("工具说明", toolLabel));
    body.appendChild(eventField("执行结果", ev.status || "—"));
    if (ev.error_code) body.appendChild(eventField("错误码", ev.error_code));
    body.appendChild(eventField("输入摘要（arguments）", el("pre", { class: "code-block" },
      formatMaybeJson(ev.arguments))));
    // 后端 ToolEvent 契约不包含输出摘要字段，如实说明而非编造。
    body.appendChild(eventField("输出摘要",
      "接口未返回该字段；工具输出证据请在 findings 的证据来源中查看。"));
    item.appendChild(body);
    box.appendChild(item);
  });
}

function eventField(label, valueNode) {
  var field = el("div", { class: "event-field" });
  field.appendChild(el("span", { class: "meta-key" }, label));
  if (typeof valueNode === "string" || typeof valueNode === "number") {
    field.appendChild(el("span", {}, String(valueNode)));
  } else if (valueNode) {
    field.appendChild(valueNode);
  }
  return field;
}

/** JSON/字符串安全格式化展示。 */
function formatMaybeJson(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (e) {
    return String(value);
  }
}

/* ---------- 发起核验（POST /api/runs） ---------- */

function selectedMode() {
  var checked = document.querySelector("input[name='mode']:checked");
  return checked ? checked.value : "simulation";
}

function onModeChange() {
  $("real-mode-warning").hidden = selectedMode() !== "real";
}

async function onRunSubmit(event) {
  event.preventDefault();
  hideRunError();
  var scenarioId = $("scenario-select").value;
  if (!scenarioId) {
    setRunStatus("请先选择一个核验场景。", true);
    return;
  }
  var mode = selectedMode();
  var button = $("run-button");
  button.disabled = true; // 防止连续点击重复提交
  setRunStatus("正在执行核验（模拟模式通常数秒内完成）…");
  try {
    var report = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_id: scenarioId, mode: mode })
    });
    setRunStatus("核验完成：任务 #" + report.run_id + "（"
      + report.model_mode + " / 执行状态 " + report.run_status
      + " / 最终状态 " + report.final_status + "）", false);
    renderReport(report, "本次核验（POST /api/runs）");
    rememberRecent({
      runId: report.run_id,
      scenarioId: scenarioId,
      mode: mode,
      time: report.created_at || ""
    });
  } catch (e) {
    setRunStatus("核验请求失败。", true);
    showRunError(e);
  } finally {
    button.disabled = false;
  }
}

/* ---------- 任务详情 / 报告查询（只读，不重新执行） ---------- */

function queryRunId() {
  var raw = $("run-id-input").value.trim();
  if (raw === "") {
    setQueryStatus("请输入任务 ID 后再查询。", true);
    return null;
  }
  if (!/^\d+$/.test(raw)) {
    setQueryStatus("任务 ID 必须是正整数。", true);
    return null;
  }
  return raw;
}

async function fetchAndRender(path, sourceLabel, statusMsg) {
  var button = path.indexOf("/report") !== -1 ? $("report-button") : $("detail-button");
  button.disabled = true;
  setQueryStatus(statusMsg, false);
  try {
    var data = await api(path);
    setQueryStatus("", false);
    renderReport(data, sourceLabel);
    rememberRecent({
      runId: data.run_id,
      scenarioId: data.delivery_id || "（未知场景）",
      mode: data.model_mode || "",
      time: data.created_at || ""
    });
  } catch (e) {
    setQueryStatus("查询失败：" + friendlyError(e).split("\n")[0], true);
  } finally {
    button.disabled = false;
  }
}

function onDetailSubmit(event) {
  event.preventDefault();
  var runId = queryRunId();
  if (runId === null) return;
  fetchAndRender("/api/runs/" + runId, "任务详情（GET /api/runs/{run_id}）",
    "正在读取任务详情…");
}

function onReportClick(event) {
  event.preventDefault();
  var runId = queryRunId();
  if (runId === null) return;
  fetchAndRender("/api/runs/" + runId + "/report", "核验报告（GET /api/runs/{run_id}/report）",
    "正在读取核验报告…");
}

/* ---------- 最近访问任务（仅浏览器 localStorage 访问索引） ---------- */

var RECENT_KEY = "deliverygate_recent_runs";
var RECENT_MAX = 10;

function loadRecent() {
  try {
    var raw = window.localStorage.getItem(RECENT_KEY);
    var parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    return []; // localStorage 不可用或数据损坏时静默降级
  }
}

function saveRecent(list) {
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  } catch (e) {
    /* 存储失败不影响其他功能 */
  }
}

/** 记录一次访问索引：只存 run_id / 场景 / 模式 / 时间，不存任何密钥。 */
function rememberRecent(entry) {
  var list = loadRecent().filter(function (r) { return r.runId !== entry.runId; });
  list.unshift(entry);
  if (list.length > RECENT_MAX) list = list.slice(0, RECENT_MAX);
  saveRecent(list);
  renderRecent();
}

function renderRecent() {
  var box = $("recent-container");
  box.textContent = "";
  var list = loadRecent();
  if (list.length === 0) {
    box.appendChild(el("p", { class: "empty-state" },
      "暂无最近访问记录。发起核验或查询任务后，这里会保存本浏览器的访问索引。"));
    return;
  }
  var ul = el("ul", { class: "recent-list" });
  list.forEach(function (r) {
    var li = el("li");
    var btn = el("button", { class: "recent-link", type: "button" }, "任务 #" + r.runId);
    btn.addEventListener("click", function () {
      $("run-id-input").value = String(r.runId);
      fetchAndRender("/api/runs/" + r.runId, "任务详情（来自最近访问）", "正在读取任务详情…");
    });
    li.appendChild(btn);
    li.appendChild(el("span", { class: "mono" }, r.scenarioId || "—"));
    if (r.mode) li.appendChild(modeBadge(r.mode));
    li.appendChild(el("span", { class: "recent-meta" }, r.time || ""));
    ul.appendChild(li);
  });
  box.appendChild(ul);
}

/* ---------- 初始化 ---------- */

function init() {
  $("run-form").addEventListener("submit", onRunSubmit);
  $("query-form").addEventListener("submit", onDetailSubmit);
  $("report-button").addEventListener("click", onReportClick);
  var modeRadios = document.querySelectorAll("input[name='mode']");
  for (var i = 0; i < modeRadios.length; i++) {
    modeRadios[i].addEventListener("change", onModeChange);
  }
  renderRecent();
  loadHealth();
  loadScenarios();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

