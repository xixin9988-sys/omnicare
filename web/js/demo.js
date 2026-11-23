/* OmniCare 60s Demo — dependency-free client for /api/v1/demo/*.
 *
 * The page is driven entirely by the real demo API: the manifest shapes the layout,
 * every step response supplies the banner/timeline data, and the replay endpoint is
 * the only source of the "verified" badge. When the API is absent (plain static
 * hosting or file://) the page shows a clearly labelled layout preview and never
 * simulates a run or an approval. All server payloads are rendered through DOM
 * nodes and textContent; the page never injects API data as HTML.
 */

"use strict";

const API_BASE = "/api/v1";
const STEP_KEYS = ["logistics", "refund", "escalate", "approve"];
const DEFAULT_DURATION = 60;
const DEFAULT_LABELS = {
  logistics: "查物流",
  refund: "申请退款",
  escalate: "转人工",
  approve: "批准放行",
};
const DEFAULT_HINTS = {
  logistics: "MCP 只读工具",
  refund: "业务写入 · 受策略约束",
  escalate: "人工接管审批",
  approve: "人工审批",
};
const AUTO_PLAN = [
  [1, "logistics", 1000],
  [2, "refund", 6000],
  [3, "escalate", 18000],
  [4, "approve", 34000],
];
const AUTO_REPLAY_AT = 42000;
const ORDER_STATUS_TEXT = {
  created: "已创建",
  pending_payment: "待支付",
  paid: "已支付",
  shipped: "已发货",
  delivered: "已签收",
  refunded: "已退款",
  cancelled: "已取消",
};
const REFUND_STATUS_TEXT = {
  pending_review: "待人工复核",
  approved: "已批准",
  rejected: "已拒绝",
  settled: "已结算",
};
const STEP_STATUS_TEXT = {
  pending: "待执行",
  ok: "完成",
  blocked: "已拦截",
  pending_review: "待人工",
  approved: "已放行",
  error: "失败",
};
const ERROR_TITLES = {
  demo_disabled: "Demo 未启用",
  run_not_found: "运行不存在",
  run_superseded: "本轮已被新运行取代",
  step_out_of_order: "步骤顺序不正确",
  invalid_step_state: "步骤状态无效",
  demo_rate_limited: "请求过于频繁",
  network_error: "无法连接演示 API",
  bad_json: "响应格式异常",
  bad_response: "响应缺少必要字段",
  replay_unverified: "回放校验失败",
};
const ERROR_HINTS = {
  demo_disabled: "服务端以 demo_disabled 关闭了演示：设置 DEMO_MODE=true 后重启 API 即可开启。",
  run_not_found: "运行记录可能已被重置，点击「重置」后重新开始。",
  run_superseded: "当前运行已被另一轮 Demo 取代，请重新开始。",
  step_out_of_order: "请按 查物流 → 申请退款 → 转人工 → 批准放行 的顺序执行。",
  demo_rate_limited: "演示接口按客户端限流（约 30 次/分钟），请稍等片刻再试。",
  network_error: "页面通过 /api/v1 调用演示接口，请确认 API 已启动并同源访问本页。",
  replay_unverified: "签名与持久化事件不匹配，事件可能被修改；请重新开始一次 Demo。",
  bad_json: "接口返回的不是 JSON，可能访问到了静态文件服务。",
};

/* The layout sample used before/without the API. It mirrors the manifest contract
   exactly so the static page still renders sensibly for screenshot review. */
const PREVIEW_MANIFEST = {
  title: "OmniCare 60 秒 Demo",
  subtitle: "Agent 通过 MCP 调用工具，治理策略拦下超额退款，人工审批放行，每一步都留下可验证的签名。",
  order: {
    id: "ORD-001",
    total_amount: "120000.00",
    status: "shipped",
    carrier: "顺丰速运",
    tracking_no: "SF-ORD-001",
  },
  amount: "100000.00",
  duration_seconds: 60,
  mcp: {
    name: "OmniCare Business Tools",
    transport: "streamable-http",
    endpoint: "/mcp",
    tools: [
      {
        name: "check_logistics",
        label: "查物流",
        description: "按订单号查询承运商与最新物流状态，只读。",
        read_only: true,
        arguments: [{ name: "order_id", type: "string", required: false, description: "订单号" }],
      },
      {
        name: "request_refund",
        label: "申请退款",
        description: "为订单发起退款申请，受治理策略约束。",
        read_only: false,
        arguments: [
          { name: "order_id", type: "string", required: true, description: "订单号" },
          { name: "amount", type: "decimal", required: true, description: "退款金额" },
          { name: "reason", type: "string", required: false, description: "退款原因" },
        ],
      },
    ],
  },
  steps: [
    { index: 1, key: "logistics", label: "查物流", hint: "MCP 只读工具", narration: "正在通过 MCP 查询 ORD-001 的物流信息…" },
    { index: 2, key: "refund", label: "申请退款", hint: "业务写入 · 受策略约束", narration: "Agent 发起 100000.00 元退款，治理策略正在裁决…" },
    { index: 3, key: "escalate", label: "转人工", hint: "人工接管审批", narration: "退款已被隔离，正在转人工复核…" },
    { index: 4, key: "approve", label: "批准放行", hint: "人工审批", narration: "demo_operator 正在审批退款…" },
  ],
};

const PARAMS = new URLSearchParams(window.location.search);
const EMBED = PARAMS.get("embed") === "1";
const AUTOPLAY = PARAMS.get("autoplay") === "1";
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const state = {
  mode: "loading", // loading | live | preview | disabled | error
  manifest: null,
  mcp: null,
  order: null,
  amount: null,
  duration: DEFAULT_DURATION,
  stepMeta: [],
  timeline: [],
  expanded: new Set(),
  toolViews: [],
  runId: null,
  sessionId: null,
  steps: {},
  inFlight: null,
  busy: false,
  auto: true,
  autoTimers: [],
  startedAt: 0,
  clockTimer: null,
  replay: null,
  verified: false,
};

const els = {};

function cacheEls() {
  const byId = (id) => document.getElementById(id);
  Object.assign(els, {
    mcpTitle: byId("mcp-title"),
    mcpTransport: document.querySelector(".mcp-meta code"),
    mcpEndpoint: byId("mcp-endpoint"),
    mcpConn: byId("mcp-conn"),
    mcpConnText: byId("mcp-conn-text"),
    toolList: byId("tool-list"),
    humanRow: byId("human-row"),
    humanState: byId("human-state"),
    runChip: byId("run-chip"),
    userMessage: byId("user-message"),
    orderId: byId("order-id"),
    orderStatus: byId("order-status"),
    orderTotal: byId("order-total"),
    orderAmount: byId("order-amount"),
    orderLogistics: byId("order-logistics"),
    stepChips: byId("step-chips"),
    banner: byId("banner"),
    bannerFlag: byId("banner-flag"),
    bannerTitle: byId("banner-title"),
    bannerSub: byId("banner-sub"),
    bannerBody: byId("banner-body"),
    progressTime: byId("progress-time"),
    progressPhase: byId("progress-phase"),
    progressTrack: byId("progress-track"),
    progressFill: byId("progress-fill"),
    progressTicks: byId("progress-ticks"),
    narration: byId("narration"),
    heroLede: byId("hero-lede"),
    btnStart: byId("btn-start"),
    btnReset: byId("btn-reset"),
    autoToggle: byId("auto-toggle"),
    btnLogistics: byId("btn-logistics"),
    btnRefund: byId("btn-refund"),
    btnEscalate: byId("btn-escalate"),
    btnApprove: byId("btn-approve"),
    btnReplay: byId("btn-replay"),
    stepCaption: byId("step-caption"),
    verified: byId("verified"),
    verifiedText: byId("verified-text"),
    timeline: byId("timeline"),
    timelineFoot: byId("timeline-foot"),
  });
}

/* ------------------------------------------------------------------ helpers */
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function codeNode(text, className) {
  return node("code", className, text);
}

function clearNode(target) {
  while (target.firstChild) target.removeChild(target.firstChild);
}

function money(value, fallback) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return fallback === undefined ? "—" : fallback;
  return "¥" + amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function plainAmount(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return String(value === undefined || value === null ? "" : value);
  return String(Math.round(amount * 100) / 100).replace(/\.0+$/, "");
}

function clockText(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  return String(minutes).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
}

function timeText(iso) {
  if (!iso) return "--:--:--";
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return "--:--:--";
  return moment.toLocaleTimeString("zh-CN", { hour12: false });
}

function shortId(value) {
  const text = String(value || "");
  if (!text) return "—";
  return text.length > 12 ? text.slice(0, 8) : text;
}

function shortSignature(signature) {
  if (!signature || typeof signature !== "object") return "—";
  const base = String(signature.short || signature.value || "");
  if (!base) return "—";
  return base.length > 10 ? base.slice(0, 4) + "…" + base.slice(-4) : base;
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, REDUCED_MOTION ? Math.min(ms, 30) : ms));
}

function stepMeta(key) {
  return (
    state.stepMeta.find((meta) => meta.key === key) || {
      key,
      index: STEP_KEYS.indexOf(key) + 1,
      label: DEFAULT_LABELS[key] || key,
      hint: DEFAULT_HINTS[key] || "",
      narration: "",
    }
  );
}

function allStepsDone() {
  return STEP_KEYS.every((key) => Boolean(state.steps[key]));
}

class ApiError extends Error {
  constructor(status, code, message) {
    super(message || "请求失败");
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function api(path, options) {
  const init = { method: (options && options.method) || "GET", cache: "no-store", headers: { Accept: "application/json" } };
  let response;
  try {
    response = await fetch(API_BASE + path, init);
  } catch (error) {
    throw new ApiError(0, "network_error", "无法连接演示 API。");
  }
  let payload = null;
  let text = "";
  try {
    text = await response.text();
  } catch (error) {
    text = "";
  }
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      payload = null;
    }
  }
  if (!response.ok) {
    if (payload === null) {
      throw new ApiError(
        response.status,
        "static_" + response.status,
        "接口返回了非 JSON 响应（HTTP " + response.status + "）。"
      );
    }
    const detail = typeof payload.detail === "object" ? payload.detail : null;
    const code = detail && detail.code ? String(detail.code) : "http_" + response.status;
    const message = detail && detail.message ? String(detail.message) : "请求失败（HTTP " + response.status + "）。";
    throw new ApiError(response.status, code, message);
  }
  if (payload === null) {
    throw new ApiError(response.status, "bad_json", "服务端返回了非 JSON 响应。");
  }
  return payload;
}

/* ------------------------------------------------------------------ clock */
function elapsedMs() {
  return state.startedAt ? Math.max(0, performance.now() - state.startedAt) : 0;
}

function startClock() {
  stopClock();
  state.clockTimer = window.setInterval(tickClock, 120);
  tickClock();
}

function stopClock() {
  if (state.clockTimer) {
    window.clearInterval(state.clockTimer);
    state.clockTimer = null;
  }
}

function tickClock() {
  if (!state.startedAt) return;
  const elapsed = Math.min(elapsedMs() / 1000, state.duration);
  const percent = Math.min(100, (elapsed / state.duration) * 100);
  els.progressFill.style.width = percent.toFixed(2) + "%";
  els.progressTime.textContent = clockText(elapsed) + " / " + clockText(state.duration);
  els.progressTrack.setAttribute("aria-valuenow", String(Math.round(percent)));
  if (elapsed >= state.duration) {
    if (allStepsDone() && state.replay) setPhase("Demo 完成");
    else if (!allStepsDone()) setPhase("60 秒到");
    stopClock();
  }
}

function resetClock() {
  stopClock();
  state.startedAt = 0;
  els.progressFill.style.width = "0%";
  els.progressTime.textContent = "00:00 / " + clockText(state.duration);
  els.progressTrack.setAttribute("aria-valuenow", "0");
  setPhase("等待开始");
}

function renderTicks() {
  clearNode(els.progressTicks);
  const marks = AUTO_PLAN.map((entry) => ({ at: entry[2], label: DEFAULT_LABELS[entry[1]] }));
  marks.push({ at: AUTO_REPLAY_AT, label: "回放" });
  for (const mark of marks) {
    const tick = node("span", "tick");
    tick.style.left = Math.min(100, (mark.at / 1000 / state.duration) * 100).toFixed(2) + "%";
    tick.title = mark.label + " · " + mark.at / 1000 + "s";
    els.progressTicks.append(tick);
  }
}

/* ------------------------------------------------------------------ shell state */
function setConn(mode, text) {
  els.mcpConn.dataset.state = mode;
  els.mcpConnText.textContent = text;
}

function setPhase(text) {
  els.progressPhase.textContent = text;
}

function narrate(text, tone) {
  els.narration.textContent = text;
  els.narration.dataset.state = tone || "idle";
}

function setVerified(mode, text) {
  els.verified.dataset.state = mode;
  els.verifiedText.textContent = text;
}

function setHuman(mode, text) {
  els.humanRow.dataset.state = mode;
  els.humanState.textContent = text;
}

/* ------------------------------------------------------------------ banner */
function setBanner(mode, flag, title, sub, body) {
  els.banner.dataset.state = mode;
  els.bannerFlag.textContent = flag;
  els.bannerTitle.textContent = title;
  els.bannerSub.textContent = sub;
  clearNode(els.bannerBody);
  if (Array.isArray(body)) {
    for (const item of body) els.bannerBody.append(item);
  }
}

function fieldCell(label, value) {
  const cell = node("div", "field");
  cell.append(node("span", "field-k", label));
  const strong = node("span", "field-v");
  if (value instanceof window.Node) strong.append(value);
  else strong.textContent = value === undefined || value === null || value === "" ? "—" : String(value);
  cell.append(strong);
  return cell;
}

function fieldGrid(fields) {
  const grid = node("div", "field-grid");
  for (const [label, value] of fields) grid.append(fieldCell(label, value));
  return grid;
}

function bannerNote(text) {
  return node("p", "banner-note", text);
}

function neutralBanner(flag, title, sub) {
  setBanner("neutral", flag, title, sub, []);
}

function renderBlockedBanner(payload) {
  const policy = payload && typeof payload.policy === "object" && payload.policy ? payload.policy : {};
  const data = payload && typeof payload.data === "object" && payload.data ? payload.data : {};
  const amount = data.amount !== undefined ? data.amount : state.amount;
  const body = [];

  const policyFields = node("div", "field-grid");
  policyFields.append(fieldCell("规则 ID", codeNode(policy.rule_id || "—", "field-code")));
  policyFields.append(fieldCell("阈值", money(policy.threshold)));
  policyFields.append(fieldCell("本次申请", money(amount)));
  const reasonCell = fieldCell("原因码", codeNode(policy.reason_code || "—", "field-code"));
  reasonCell.classList.add("field-wide");
  policyFields.append(reasonCell);
  body.push(policyFields);

  if (policy.rule_id || policy.rule_label) {
    const rule = node("div", "rule-card");
    const head = node("div", "rule-head");
    head.append(node("span", "rule-hit", "命中规则"));
    head.append(codeNode(policy.rule_id || "high-value-refund", "rule-id"));
    const matched = Array.isArray(policy.matched_rules) ? policy.matched_rules : [];
    head.append(node("span", "rule-badge", "matched · " + (matched.length || 1)));
    rule.append(head);
    rule.append(node("p", "rule-label", policy.rule_label || "高额退款必须人工审批"));
    rule.append(
      node(
        "p",
        "rule-meta",
        "匹配规则 " +
          (matched.length ? matched.join(", ") : policy.rule_id || "high-value-refund") +
          " · 阈值 " +
          money(policy.threshold) +
          " · 本次 " +
          money(amount)
      )
    );
    body.push(rule);
  }

  body.push(bannerNote("退款请求已被隔离（quarantine），不会创建退款单，也不会扣减订单金额；必须由人工审批后才可放行。"));

  setBanner(
    "blocked",
    "POLICY BLOCKED",
    (payload && payload.title) || "策略拦截",
    (policy.detail && String(policy.detail)) || "治理策略判定该退款金额超过自动放行阈值，已转人工复核。",
    body
  );
}

function renderReviewBanner(payload) {
  const data = payload && typeof payload.data === "object" && payload.data ? payload.data : {};
  const refund = payload && typeof payload.refund === "object" && payload.refund ? payload.refund : {};
  const refundId = refund.id || data.refund_id || "—";
  setBanner(
    "review",
    "HUMAN REVIEW",
    (payload && payload.title) || "已转人工复核",
    "退款申请已创建为待审状态，等待 demo_operator 人工审批；在高额规则下，人工接管是放行的唯一路径。",
    [
      fieldGrid([
        ["退款单", codeNode(refundId, "field-code")],
        ["金额", money(refund.amount !== undefined ? refund.amount : data.amount)],
        ["状态", REFUND_STATUS_TEXT[refund.status || data.status] || refund.status || data.status || "待人工复核"],
        ["审批人", refund.decided_by || "待指派"],
      ]),
    ]
  );
}

function renderApprovedBanner(payload) {
  const data = payload && typeof payload.data === "object" && payload.data ? payload.data : {};
  const refund = payload && typeof payload.refund === "object" && payload.refund ? payload.refund : {};
  const audit = payload && typeof payload.audit === "object" && payload.audit ? payload.audit : {};
  const refundId = refund.id || data.refund_id || "—";
  const actor = audit.actor || refund.decided_by || data.decided_by || "demo_operator";
  const transition = (audit.from_status || "pending_review") + " → " + (audit.to_status || "approved");
  els.orderAmount.dataset.decided = "approved";
  setBanner(
    "approved",
    "APPROVED",
    (payload && payload.title) || "人工审批通过 · 放行",
    "审批通过，退款放行并写入审计链；右侧时间线可逐步核对签名与 provenance。",
    [
      fieldGrid([
        ["退款单", codeNode(refundId, "field-code")],
        ["金额", money(refund.amount !== undefined ? refund.amount : data.amount)],
        ["退款状态", REFUND_STATUS_TEXT[refund.status || data.status] || refund.status || "已批准"],
        ["审批人", actor],
        ["审计迁移", transition],
        ["审计 ID", codeNode(audit.id || "—", "field-code")],
      ]),
      bannerNote(
        state.auto
          ? "自动演示将在约 42s 请求回放：服务端会重新计算四步 HMAC 并返回 verified。"
          : "点击「回放」：服务端会重新计算四步 HMAC 并校验 verified。"
      ),
    ]
  );
}

/* ------------------------------------------------------------------ render: order / tools / chips / timeline */
function renderOrderCard() {
  const order = state.order || {};
  els.orderId.textContent = order.id || "—";
  els.orderStatus.textContent = ORDER_STATUS_TEXT[order.status] || order.status || "—";
  els.orderTotal.textContent = money(order.total_amount);
  els.orderAmount.textContent = money(state.amount);
  els.orderAmount.dataset.decided = "pending";
  const logistics = [order.carrier, order.tracking_no].filter(Boolean).join(" · ");
  els.orderLogistics.textContent = logistics || "—";
  els.userMessage.textContent =
    "给 " + (order.id || "ORD-001") + " 退 " + plainAmount(state.amount) + " 元。";
}

function normaliseArguments(raw) {
  if (Array.isArray(raw)) {
    const list = [];
    for (const item of raw) {
      if (typeof item === "string") list.push({ name: item, type: "", required: false });
      else if (item && typeof item === "object" && item.name) {
        list.push({
          name: String(item.name),
          type: item.type ? String(item.type) : "",
          required: item.required === true,
        });
      }
    }
    return list;
  }
  if (raw && typeof raw === "object") {
    return Object.keys(raw).map((key) => ({ name: key, type: "", required: false }));
  }
  return [];
}

function renderTools() {
  const tools = state.mcp && Array.isArray(state.mcp.tools) ? state.mcp.tools : [];
  clearNode(els.toolList);
  state.toolViews = [];
  if (!tools.length) {
    els.toolList.append(node("li", "panel-note", "manifest 未提供 MCP 工具列表。"));
    return;
  }
  tools.forEach((tool) => {
    const item = node("li", "tool");
    item.dataset.state = "idle";
    item.dataset.tool = String(tool.name || "");

    const head = node("div", "tool-head");
    head.append(node("span", "tool-dot"));
    head.append(codeNode(tool.name || "tool", "tool-name"));
    head.append(node("span", "tool-tag" + (tool.read_only === false ? " is-write" : ""), tool.read_only === false ? "业务写入" : "只读"));
    item.append(head);

    item.append(node("p", "tool-label", tool.label || tool.name || ""));
    if (tool.description) item.append(node("p", "tool-desc", String(tool.description)));

    const args = normaliseArguments(tool.arguments);
    if (args.length) {
      const argWrap = node("div", "tool-args");
      for (const arg of args) {
        const chip = node("span", "arg" + (arg.required ? "" : " is-optional"));
        chip.append(codeNode(arg.name));
        if (arg.type) chip.append(node("span", "arg-type", arg.type));
        chip.title = (arg.required ? "必填" : "可选") + (arg.name ? " · " + arg.name : "");
        argWrap.append(chip);
      }
      item.append(argWrap);
    }

    const foot = node("div", "tool-foot");
    const stateEl = node("span", "tool-state", "待调用");
    const callsEl = node("span", "tool-calls", "0 次调用");
    foot.append(stateEl, callsEl);
    item.append(foot);

    els.toolList.append(item);
    state.toolViews.push({ root: item, name: String(tool.name || ""), stateEl, callsEl, calls: 0 });
  });
}

function renderChips() {
  clearNode(els.stepChips);
  state.stepMeta.forEach((meta, index) => {
    const payload = state.steps[meta.key];
    let chipState = "pending";
    let statusText = "待执行";
    if (state.inFlight === meta.key && !payload) {
      chipState = "active";
      statusText = "执行中";
    } else if (payload) {
      const status = String(payload.status || "");
      if (status === "ok") {
        chipState = "done";
        statusText = "完成";
      } else if (status === "blocked") {
        chipState = "blocked";
        statusText = "已拦截";
      } else if (status === "pending_review") {
        chipState = "review";
        statusText = "待人工";
      } else if (status === "approved") {
        chipState = "approved";
        statusText = "已放行";
      } else {
        chipState = "active";
        statusText = STEP_STATUS_TEXT[status] || "执行中";
      }
    }
    const chip = node("li", "chip");
    chip.dataset.state = chipState;
    chip.dataset.key = meta.key;
    chip.dataset.index = String(index + 1);
    chip.append(node("span", "chip-index", String(meta.index || index + 1)));
    chip.append(node("span", "chip-label", meta.label));
    chip.append(node("span", "chip-status", statusText));
    els.stepChips.append(chip);
  });
}

function hasTimelineData(entry) {
  return Boolean(entry && (entry.signature || entry.provenance || entry.title));
}

function buildTimelineDetails(entry, expanded) {
  const wrap = node("div", "tl-details");
  wrap.hidden = !expanded;
  if (entry.summary) wrap.append(node("p", "tl-summary", entry.summary));
  const rows = [];
  if (entry.signature) {
    rows.push(["签名算法", entry.signature.alg || "HMAC-SHA256"]);
    rows.push(["完整签名", entry.signature.value || "—"]);
    if (entry.signature.short) rows.push(["短签名", entry.signature.short]);
  }
  const provenance = entry.provenance;
  if (provenance && typeof provenance === "object") {
    rows.push(["来源", provenance.source_ref || "—"]);
    if (provenance.policy_version) rows.push(["策略版本", provenance.policy_version]);
    rows.push(["事件 ID", provenance.event_id || "—"]);
    rows.push(["订单", provenance.order_id || "—"]);
    if (provenance.tool) rows.push(["工具", provenance.tool]);
    if (provenance.refund_id) rows.push(["退款单", provenance.refund_id]);
  }
  if (rows.length) {
    const list = node("dl", "tl-kv");
    for (const [key, value] of rows) {
      list.append(node("dt", "", key));
      list.append(node("dd", "", value));
    }
    wrap.append(list);
  }
  if (entry.signature || provenance) {
    const raw = JSON.stringify({ signature: entry.signature || null, provenance: provenance || null }, null, 2);
    wrap.append(node("pre", "tl-json", raw));
  }
  return wrap;
}

function renderTimeline() {
  clearNode(els.timeline);
  state.timeline.forEach((entry, index) => {
    const payload = state.steps[entry.key];
    let rowState = "pending";
    if (state.inFlight === entry.key && !payload) rowState = "active";
    else if (entry.status === "ok") rowState = "done";
    else if (entry.status === "blocked") rowState = "blocked";
    else if (entry.status === "pending_review") rowState = "review";
    else if (entry.status === "approved") rowState = "approved";
    else if (entry.status === "error") rowState = "error";

    const row = node("li", "tl");
    row.dataset.state = rowState;
    row.dataset.key = entry.key;
    row.append(node("span", "tl-node", String(index + 1)));

    const body = node("div", "tl-body");
    const head = node("div", "tl-head");
    head.append(node("span", "tl-time", entry.at ? timeText(entry.at) : "--:--:--"));
    head.append(node("span", "tl-label", entry.label || DEFAULT_LABELS[entry.key] || entry.key));

    const status = node("span", "tl-status", STEP_STATUS_TEXT[entry.status] || "待执行");
    status.dataset.state = entry.status || "pending";
    head.append(status);

    const expanded = state.expanded.has(entry.key);
    const toggle = node("button", "tl-toggle", expanded ? "收起" : "详情");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    toggle.disabled = !hasTimelineData(entry);
    toggle.addEventListener("click", () => toggleTimelineDetails(entry.key, toggle));
    head.append(toggle);

    body.append(head);
    body.append(node("p", "tl-title", entry.title || stepMeta(entry.key).hint || "等待执行"));
    const meta = node("div", "tl-meta");
    meta.append(codeNode(entry.signature ? "sig " + shortSignature(entry.signature) : "sig —", "tl-sig"));
    meta.append(codeNode(entry.provenance && entry.provenance.source_ref ? entry.provenance.source_ref : "provenance —", "tl-src"));
    body.append(meta);

    if (hasTimelineData(entry)) body.append(buildTimelineDetails(entry, expanded));

    row.append(body);
    els.timeline.append(row);
  });
}

function toggleTimelineDetails(key, button) {
  const row = button.closest(".tl");
  if (!row) return;
  const details = row.querySelector(".tl-details");
  if (!details) return;
  const expanded = state.expanded.has(key);
  if (expanded) state.expanded.delete(key);
  else state.expanded.add(key);
  details.hidden = expanded;
  button.setAttribute("aria-expanded", String(!expanded));
  button.textContent = expanded ? "详情" : "收起";
}

function resetTimeline() {
  state.expanded = new Set();
  state.timeline = state.stepMeta.map((meta) => ({
    key: meta.key,
    label: meta.label,
    status: "pending",
    title: "",
    summary: "",
    at: "",
    signature: null,
    provenance: null,
  }));
  renderTimeline();
  els.timelineFoot.textContent = "签名算法 HMAC-SHA256 · 完整签名与 provenance JSON 见每步详情。";
  setVerified("idle", "待回放");
}

function updateTimelineEntry(key, patch) {
  const entry = state.timeline.find((item) => item.key === key);
  if (!entry) return;
  Object.assign(entry, patch);
}

/* ------------------------------------------------------------------ manifest */
function applyManifest(raw) {
  const manifest = raw && typeof raw === "object" ? raw : {};
  state.manifest = manifest;

  state.duration =
    Number.isFinite(Number(manifest.duration_seconds)) && Number(manifest.duration_seconds) > 0
      ? Number(manifest.duration_seconds)
      : DEFAULT_DURATION;
  state.order =
    manifest.order && typeof manifest.order === "object" ? manifest.order : PREVIEW_MANIFEST.order;
  state.amount = manifest.amount !== undefined && manifest.amount !== null ? manifest.amount : state.order.total_amount;
  state.mcp =
    manifest.mcp && typeof manifest.mcp === "object" ? manifest.mcp : PREVIEW_MANIFEST.mcp;

  const manifestSteps = Array.isArray(manifest.steps) ? manifest.steps : [];
  state.stepMeta = STEP_KEYS.map((key, index) => {
    const source =
      manifestSteps.find((step) => step && step.key === key) || manifestSteps[index] || {};
    return {
      key,
      index: index + 1,
      label: source.label || DEFAULT_LABELS[key],
      hint: source.hint || DEFAULT_HINTS[key],
      narration: source.narration || "",
    };
  });

  document.title = String(manifest.title || "OmniCare 60 秒 Demo") + " · Synthelix";
  if (manifest.subtitle) els.heroLede.textContent = String(manifest.subtitle);

  els.mcpTitle.textContent = state.mcp.name || "OmniCare Business Tools";
  if (els.mcpTransport) els.mcpTransport.textContent = state.mcp.transport || "streamable-http";
  els.mcpEndpoint.textContent = state.mcp.endpoint || "/mcp";

  renderOrderCard();
  renderTools();
  renderChips();
  resetTimeline();
  renderTicks();
  resetClock();
}

/* ------------------------------------------------------------------ modes */
function finishMode(mode) {
  state.mode = mode;
  document.body.dataset.mode = mode;
  syncControls();
}

function setupLive() {
  finishMode("live");
  setConn("live", "已连接");
  setHuman("idle", "待触发");
  neutralBanner(
    "READY",
    "准备就绪",
    "点击「开始 60 秒 Demo」，Agent 将通过 MCP 真实调用工具，并把每一步写入签名事件。"
  );
  narrate("准备就绪。点击开始，Agent 将通过 MCP 真实调用工具。", "idle");
  setPhase("等待开始");
}

function setupPreview() {
  finishMode("preview");
  setConn("preview", "静态预览");
  setHuman("idle", "待触发");
  setBanner(
    "neutral",
    "STATIC PREVIEW",
    "静态预览 · 未连接 API",
    "当前页面仅展示布局，未检测到 /api/v1/demo/manifest。通过 FastAPI 的 /ui/demo.html 打开即可运行真实的 60 秒 Demo；这里不会模拟任何运行或审批。",
    []
  );
  narrate("静态预览：未连接演示 API，操作按钮已禁用。", "review");
  setPhase("静态预览");
  rewriteHomeLinks();
}

function setupDisabled(message) {
  finishMode("disabled");
  setConn("offline", "未启用");
  setHuman("idle", "待触发");
  setBanner(
    "error",
    "DEMO DISABLED",
    "Demo 未启用",
    "服务端返回 demo_disabled：演示接口已关闭。" +
      (message ? "（" + String(message) + "）" : "") +
      "设置 DEMO_MODE=true 后重启 API 即可开启。",
    [bannerNote("演示模式关闭时页面仍可访问，但不会调用任何步骤接口，也不会伪造审批结果。")]
  );
  narrate("Demo 未启用：请设置 DEMO_MODE=true 后重启服务。", "error");
  setPhase("未启用");
}

function setupUnavailable(error) {
  finishMode("error");
  setConn("offline", "加载失败");
  setBanner(
    "error",
    "LOAD ERROR",
    "无法加载 Demo",
    (error && error.message) || "读取演示 manifest 失败。",
    [bannerNote("请确认 API 已启动且 /api/v1/demo/manifest 可访问，然后刷新页面。")]
  );
  narrate("无法加载 Demo manifest，请检查 API 服务。", "error");
  setPhase("加载失败");
}

function rewriteHomeLinks() {
  document.querySelectorAll("[data-console-home]").forEach((link) => {
    link.setAttribute("href", "./index.html");
  });
}

/* ------------------------------------------------------------------ controls */
function syncControls() {
  const live = state.mode === "live";
  const busy = state.busy;
  const started = Boolean(state.runId);
  const nextKey = STEP_KEYS.find((key) => !state.steps[key]) || null;

  els.btnStart.disabled = !live || busy || (started && !allStepsDone());
  els.btnReset.disabled = !live || !started || busy;
  els.autoToggle.disabled = !live;

  STEP_KEYS.forEach((key, index) => {
    const button = els["btn" + key.charAt(0).toUpperCase() + key.slice(1)];
    if (!button) return;
    const previousDone = index === 0 || Boolean(state.steps[STEP_KEYS[index - 1]]);
    button.disabled = !live || busy || !started || Boolean(state.steps[key]) || !previousDone;
    button.classList.toggle("is-next", !button.disabled && key === nextKey);
  });

  els.btnReplay.disabled = !live || busy || !started || !state.steps.approve;

  els.autoToggle.checked = live ? state.auto : false;

  let caption = "开始后可逐步执行";
  if (state.mode === "preview") caption = "静态预览：API 不可用";
  else if (state.mode === "disabled") caption = "服务端未开启 Demo";
  else if (state.mode === "loading") caption = "正在连接演示 API…";
  else if (state.mode === "error") caption = "加载失败，请刷新重试";
  else if (!started) caption = "开始后可逐步执行";
  else if (busy && state.inFlight && state.inFlight !== "replay" && state.inFlight !== "start") {
    caption = "正在执行：" + stepMeta(state.inFlight).label;
  } else if (busy && state.inFlight === "replay") caption = "回放校验中…";
  else if (allStepsDone()) caption = state.replay ? "四步已完成 · 回放已校验" : "四步已完成 · 可回放";
  else if (nextKey) {
    const meta = stepMeta(nextKey);
    caption = "下一步：" + meta.label + (meta.hint ? " · " + meta.hint : "");
  }
  els.stepCaption.textContent = caption;
}

/* ------------------------------------------------------------------ run lifecycle */
function clearAutoTimers() {
  state.autoTimers.forEach((timer) => window.clearTimeout(timer));
  state.autoTimers = [];
}

function scheduleAuto() {
  clearAutoTimers();
  if (!state.runId || !state.auto || state.mode !== "live") return;
  const elapsed = elapsedMs();
  for (const [, key, at] of AUTO_PLAN) {
    if (state.steps[key]) continue;
    const wait = Math.max(0, at - elapsed);
    state.autoTimers.push(window.setTimeout(() => execStep(key), wait));
  }
  if (!state.replay) {
    const wait = Math.max(0, AUTO_REPLAY_AT - elapsed);
    state.autoTimers.push(
      window.setTimeout(() => {
        if (state.steps.approve && !state.busy) runReplay();
      }, wait)
    );
  }
}

function resetRunState() {
  clearAutoTimers();
  state.runId = null;
  state.sessionId = null;
  state.steps = {};
  state.inFlight = null;
  state.replay = null;
  state.verified = false;
  els.runChip.hidden = true;
  els.runChip.textContent = "RUN · —";
  state.toolViews.forEach((view) => {
    view.root.dataset.state = "idle";
    view.stateEl.textContent = "待调用";
    view.calls = 0;
    view.callsEl.textContent = "0 次调用";
  });
  setHuman("idle", "待触发");
  els.orderAmount.dataset.decided = "pending";
  renderOrderCard();
  renderChips();
  resetTimeline();
  resetClock();
  setVerified("idle", "待回放");
}

function resetAll() {
  clearAutoTimers();
  resetRunState();
  finishMode(state.mode === "live" ? "live" : state.mode);
  neutralBanner(
    "READY",
    "准备就绪",
    "点击「开始 60 秒 Demo」，Agent 将通过 MCP 真实调用工具，并把每一步写入签名事件。"
  );
  narrate("已重置界面。再次开始会创建一次新的运行。", "idle");
  setPhase("等待开始");
  syncControls();
}

async function startRun() {
  if (state.mode !== "live" || state.busy) return;
  if (state.runId && !allStepsDone()) return;
  resetRunState();
  state.busy = true;
  state.inFlight = "start";
  setPhase("创建运行");
  narrate("正在创建 Demo 运行：服务端会重置 ORD-001 的演示数据…", "running");
  neutralBanner("STARTING", "正在创建运行", "服务端正在准备 ORD-001 的可重置演示数据。");
  syncControls();
  try {
    const payload = await api("/demo/runs", { method: "POST" });
    if (!payload || !payload.run_id) throw new ApiError(200, "bad_response", "运行响应缺少 run_id。");
    state.runId = String(payload.run_id);
    state.sessionId = payload.session_id || null;
    if (payload.order && typeof payload.order === "object") state.order = payload.order;
    if (payload.amount !== undefined && payload.amount !== null) state.amount = payload.amount;
    renderOrderCard();
    els.runChip.hidden = false;
    els.runChip.textContent = "RUN · " + shortId(state.runId);
    neutralBanner(
      "RUNNING",
      "运行已创建",
      "运行 " + shortId(state.runId) + " 已启动，Agent 正在通过 MCP 调用工具。"
    );
    narrate(
      stepMeta("logistics").narration || "正在通过 MCP 查询 " + (state.order.id || "ORD-001") + " 的物流信息…",
      "running"
    );
    setPhase("调用工具");
    state.startedAt = performance.now();
    startClock();
    if (state.auto) scheduleAuto();
  } catch (error) {
    showError(error);
  } finally {
    state.busy = false;
    state.inFlight = null;
    syncControls();
  }
}

/* ------------------------------------------------------------------ steps */
function toolIndexForStep(key) {
  if (!state.toolViews.length) return -1;
  const pattern = key === "logistics" ? /logistic/i : key === "refund" || key === "escalate" ? /refund/i : null;
  if (!pattern) return -1;
  const match = state.toolViews.findIndex((view) => pattern.test(view.name));
  if (match >= 0) return match;
  if (state.toolViews.length > 1) return key === "logistics" ? 0 : 1;
  return 0;
}

function markTool(key, mode, text, countCall) {
  const index = toolIndexForStep(key);
  if (index < 0 || !state.toolViews[index]) return;
  const view = state.toolViews[index];
  view.root.dataset.state = mode;
  view.stateEl.textContent = text;
  if (countCall) {
    view.calls += 1;
    view.callsEl.textContent = view.calls + " 次调用";
  }
}

function applyStep(key, payload) {
  const data = payload && typeof payload === "object" ? payload : {};
  state.steps[key] = data;
  const status = String(data.status || "");
  updateTimelineEntry(key, {
    label: stepMeta(key).label,
    status: status || "ok",
    title: data.title || "",
    at: (data.event && data.event.created_at) || data.at || "",
    signature: data.signature || null,
    provenance: data.provenance || null,
  });

  if (key === "logistics") {
    markTool(key, "ok", "已返回", true);
    const info = data.data && typeof data.data === "object" ? data.data : {};
    neutralBanner(
      "STEP 1 / 4",
      "物流已返回",
      [info.carrier, info.tracking_no, info.status].filter(Boolean).join(" · ") +
        "。下一步：申请退款。"
    );
    narrate(
      "物流已返回：" +
        [info.carrier, info.tracking_no].filter(Boolean).join(" · ") +
        "。" +
        (state.auto ? "继续执行退款申请…" : "点击「申请退款」继续。"),
      "running"
    );
    setPhase("物流已返回");
  } else if (key === "refund") {
    markTool(key, "blocked", "已拦截", true);
    renderBlockedBanner(data);
    narrate(
      "治理策略拦截：高额退款必须人工审批，退款请求被隔离，未扣款。" +
        (state.auto ? "继续转人工…" : "点击「转人工」继续。"),
      "blocked"
    );
    setPhase("策略拦截");
  } else if (key === "escalate") {
    markTool(key, "ok", "已创建待审单", true);
    renderReviewBanner(data);
    const refund = data.refund && typeof data.refund === "object" ? data.refund : {};
    narrate(
      "退款 " +
        (refund.id || "—") +
        " 已转为待人工复核。" +
        (state.auto ? "等待人工审批放行…" : "点击「批准放行」完成人工审批。"),
      "review"
    );
    setPhase("待人工审批");
    setHuman("waiting", "待审批");
  } else if (key === "approve") {
    renderApprovedBanner(data);
    setHuman("done", "已批准");
    const refund = data.refund && typeof data.refund === "object" ? data.refund : {};
    narrate(
      "人工审批通过：退款 " +
        (refund.id || "—") +
        " 已放行并写入审计。" +
        (state.auto ? "即将回放四步签名…" : "点击「回放」核对签名与 provenance。"),
      "approved"
    );
    setPhase("已放行");
  }

  renderChips();
  renderTimeline();
  syncControls();
}

async function execStep(key) {
  if (state.mode !== "live" || state.busy || !state.runId) return;
  if (!STEP_KEYS.includes(key) || state.steps[key]) return;
  const index = STEP_KEYS.indexOf(key);
  for (let i = 0; i < index; i += 1) {
    if (!state.steps[STEP_KEYS[i]]) return;
  }

  state.busy = true;
  state.inFlight = key;
  markTool(key, "running", "执行中", false);
  setPhase("执行：" + stepMeta(key).label);
  narrate(
    stepMeta(key).narration || "正在执行第 " + (index + 1) + " 步：" + stepMeta(key).label + "…",
    "running"
  );
  renderChips();
  renderTimeline();
  syncControls();

  try {
    const payload = await api("/demo/runs/" + encodeURIComponent(state.runId) + "/steps/" + key, {
      method: "POST",
    });
    state.busy = false;
    state.inFlight = null;
    applyStep(key, payload);
    if (state.auto) scheduleAuto();
  } catch (error) {
    state.busy = false;
    state.inFlight = null;
    markTool(key, "blocked", "请求失败", false);
    updateTimelineEntry(key, { status: "error", title: "调用失败" });
    renderChips();
    renderTimeline();
    showError(error);
  } finally {
    state.busy = false;
    state.inFlight = null;
    syncControls();
  }
}

/* ------------------------------------------------------------------ replay */
function mergeReplay(payload) {
  state.replay = payload;
  state.verified = payload.verified === true;
  const replaySteps = Array.isArray(payload.steps) ? payload.steps : [];
  const byKey = new Map();
  replaySteps.forEach((step, index) => {
    if (!step) return;
    const key = STEP_KEYS.includes(step.key) ? step.key : STEP_KEYS[index];
    if (key) byKey.set(key, step);
  });
  state.timeline = state.stepMeta.map((meta) => {
    const step = byKey.get(meta.key);
    if (!step) {
      const existing = state.timeline.find((entry) => entry.key === meta.key);
      return existing || {
        key: meta.key,
        label: meta.label,
        status: "pending",
        title: "",
        summary: "",
        at: "",
        signature: null,
        provenance: null,
      };
    }
    return {
      key: meta.key,
      label: step.label || meta.label,
      status: step.status || "ok",
      title: step.title || "",
      summary: step.summary || "",
      at: step.at || "",
      signature: step.signature || null,
      provenance: step.provenance || null,
    };
  });
}

async function animateReplay() {
  const rows = Array.from(els.timeline.querySelectorAll(".tl"));
  if (!rows.length) return;
  if (REDUCED_MOTION) {
    rows.forEach((row) => row.classList.add("is-replaying"));
    await delay(120);
    rows.forEach((row) => row.classList.remove("is-replaying"));
    return;
  }
  for (const row of rows) {
    row.classList.add("is-replaying");
    await delay(560);
    row.classList.remove("is-replaying");
    await delay(90);
  }
}

async function runReplay() {
  if (state.mode !== "live" || state.busy || !state.runId || !state.steps.approve) return;
  clearAutoTimers();
  state.busy = true;
  state.inFlight = "replay";
  setPhase("回放校验");
  narrate("正在请求服务端回放：重新计算四步 HMAC 并校验签名与 provenance…", "running");
  setVerified("checking", "校验中");
  syncControls();
  try {
    const payload = await api("/demo/runs/" + encodeURIComponent(state.runId) + "/replay");
    mergeReplay(payload);
    renderTimeline();
    await animateReplay();
    const count = Array.isArray(payload.steps) ? payload.steps.length : 0;
    if (payload.verified === true) {
      setVerified("ok", "已验证 · HMAC-SHA256");
      els.timelineFoot.textContent =
        "回放完成：" +
        count +
        " 步签名校验通过 · HMAC-SHA256 · 完整签名与 provenance JSON 见每步详情。";
      narrate("回放完成：四步签名全部校验通过，时间线与 provenance 可核对。", "approved");
      setPhase("回放完成");
    } else {
      setVerified("fail", "校验失败");
      els.timelineFoot.textContent = "回放完成：至少一步签名不匹配，事件可能被篡改。";
      showError({ code: "replay_unverified", message: "回放签名校验未通过（verified=false）。" });
    }
  } catch (error) {
    setVerified("fail", "校验失败");
    showError(error);
  } finally {
    state.busy = false;
    state.inFlight = null;
    syncControls();
  }
}

/* ------------------------------------------------------------------ errors */
function showError(error) {
  const code = error && error.code ? String(error.code) : "unknown_error";
  const title = ERROR_TITLES[code] || "调用失败";
  const message = (error && error.message) || "未知错误。";
  clearAutoTimers();
  setPhase("错误");
  narrate(title + "：" + message, "error");
  const body = [
    fieldGrid([
      ["错误码", codeNode(code, "field-code")],
      ["HTTP", error && error.status ? String(error.status) : "—"],
    ]),
    bannerNote(ERROR_HINTS[code] || "可重试当前步骤，或点击「重置」后重新开始。"),
  ];
  setBanner("error", "ERROR", title, message, body);
}

/* ------------------------------------------------------------------ init */
function wireEvents() {
  els.btnStart.addEventListener("click", () => startRun());
  els.btnReset.addEventListener("click", () => resetAll());
  els.btnLogistics.addEventListener("click", () => execStep("logistics"));
  els.btnRefund.addEventListener("click", () => execStep("refund"));
  els.btnEscalate.addEventListener("click", () => execStep("escalate"));
  els.btnApprove.addEventListener("click", () => execStep("approve"));
  els.btnReplay.addEventListener("click", () => runReplay());
  els.autoToggle.addEventListener("change", () => {
    state.auto = els.autoToggle.checked;
    if (state.auto && state.runId) scheduleAuto();
    else clearAutoTimers();
  });
}

async function init() {
  cacheEls();
  if (EMBED) document.body.classList.add("is-embed");
  state.auto = els.autoToggle.checked;
  wireEvents();
  applyManifest(PREVIEW_MANIFEST);
  finishMode("loading");
  setConn("connecting", "连接中");
  narrate("正在连接演示 API 并读取 manifest…", "idle");

  try {
    const manifest = await api("/demo/manifest");
    applyManifest(manifest);
    setupLive();
    if (AUTOPLAY) {
      state.auto = true;
      els.autoToggle.checked = true;
      syncControls();
      window.setTimeout(() => startRun(), 420);
    }
  } catch (error) {
    if (error && error.code === "demo_disabled") {
      applyManifest(PREVIEW_MANIFEST);
      setupDisabled(error.message);
    } else if (
      error &&
      (error.code === "network_error" ||
        error.code === "bad_json" ||
        error.code === "static_404" ||
        error.code === "static_405")
    ) {
      applyManifest(PREVIEW_MANIFEST);
      setupPreview();
    } else {
      applyManifest(PREVIEW_MANIFEST);
      setupUnavailable(error);
    }
  }
}

init();
