/* OmniCare console — Synthelix edition.
   A dependency-free, white-canvas event console. The WebSocket stream comes from
   /api/v1/ws/monitor; when the socket is unavailable (static preview, JWT mode without an
   admin token, network hiccup) the page falls back to a clearly labelled demo stream. */

const ENDPOINT = "/api/v1/ws/monitor";
const MAX_ROWS = 40;

const els = {
  conn: document.getElementById("conn"),
  connText: document.getElementById("conn-text"),
  stream: document.getElementById("stream"),
  meta: document.getElementById("stream-meta"),
  empty: document.getElementById("stream-empty"),
  runs: document.getElementById("stat-runs"),
  blocked: document.getElementById("stat-blocked"),
  memory: document.getElementById("stat-memory"),
  tools: document.getElementById("stat-tools"),
  tokens: document.getElementById("stat-tokens"),
};

const counters = { events: 0, runs: 0, blocked: 0, memory: 0, tools: 0, tokens: 0 };

const EVENT_LABELS = {
  request_received: "请求进入",
  route_selected: "路由选择",
  agent_started: "Agent 启动",
  memory_retrieval_started: "记忆检索",
  memory_retrieved: "记忆命中",
  memory_written: "记忆写入",
  tool_started: "工具调用",
  tool_completed: "工具完成",
  tool_denied: "工具拦截",
  tool_failed: "工具失败",
  governance_blocked: "治理阻断",
  output_quarantined: "输出隔离",
  refund_pending_review: "退款待审",
  run_completed: "运行完成",
};

const BLOCKED_TYPES = new Set([
  "governance_blocked",
  "tool_denied",
  "output_quarantined",
  "input_governed",
]);

function labelFor(type) {
  return EVENT_LABELS[type] || type.replaceAll("_", " ");
}

function kindFor(type, status) {
  if (BLOCKED_TYPES.has(type) || String(status || "").toLowerCase() === "blocked") return "blocked";
  if (type.includes("memory")) return "memory";
  if (type.includes("tool") || type.includes("refund")) return "tool";
  return "system";
}

const DOT_CLASS = {
  system: "dot-ink",
  memory: "dot-violet",
  tool: "dot-brown",
  blocked: "dot-red",
};

function statusFor(kind, status) {
  const normalised = String(status || "").toLowerCase();
  if (kind === "blocked") return { text: "已阻断", value: "blocked" };
  if (normalised === "completed") return { text: "完成", value: "ok" };
  if (normalised === "running") return { text: "进行中", value: "running" };
  if (normalised === "failed") return { text: "失败", value: "blocked" };
  return { text: "记录", value: "info" };
}

function detailFor(event) {
  const payload = event.payload || {};
  const parts = [];
  if (payload.intent) parts.push("意图 " + payload.intent);
  if (payload.tool) parts.push("工具 " + payload.tool);
  if (payload.reason_code) parts.push(String(payload.reason_code));
  if (payload.memory_type) parts.push(String(payload.memory_type));
  if (typeof payload.accepted === "number") parts.push("接受 " + payload.accepted + " 条");
  if (typeof payload.count === "number") parts.push(payload.count + " 条记忆");
  if (payload.amount) parts.push("¥" + payload.amount);
  if (typeof payload.total_tokens === "number") parts.push(payload.total_tokens + " tokens");
  if (typeof payload.latency_ms === "number") parts.push(payload.latency_ms + " ms");
  return parts.join(" · ");
}

function timeFor(event) {
  const raw = event.created_at || event.time;
  const moment = raw ? new Date(raw) : new Date();
  const date = Number.isNaN(moment.getTime()) ? new Date() : moment;
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function bump(counter, amount) {
  counters[counter] += amount;
  const el = els[counter];
  if (el) el.textContent = counter === "tokens" ? counters.tokens.toLocaleString("en-US") : String(counters[counter]);
}

function account(event, kind) {
  const type = String(event.event_type || event.type || "").toLowerCase();
  if (type === "run_completed") {
    bump("runs", 1);
    const tokens = Number((event.payload || {}).total_tokens || 0);
    if (Number.isFinite(tokens) && tokens > 0) bump("tokens", Math.round(tokens));
  }
  if (kind === "blocked") bump("blocked", 1);
  if (kind === "memory" && ["memory_retrieved", "memory_written"].includes(type)) bump("memory", 1);
  if (kind === "tool" && type === "tool_started") bump("tools", 1);
}

function renderRow(event) {
  const type = String(event.event_type || event.type || "agent_event").toLowerCase();
  const kind = kindFor(type, event.status);
  const status = statusFor(kind, event.status);
  const agent = String(event.agent || event.agent_name || "supervisor");
  const detail = detailFor(event);

  const row = document.createElement("li");
  row.className = "stream-row";

  const time = document.createElement("span");
  time.className = "stream-time";
  time.textContent = timeFor(event);

  const dot = document.createElement("span");
  dot.className = "dot " + (DOT_CLASS[kind] || "dot-ink");
  dot.setAttribute("aria-hidden", "true");

  const body = document.createElement("div");
  body.className = "stream-body";

  const title = document.createElement("p");
  title.className = "stream-title";
  title.textContent = labelFor(type) + " · " + agent;

  const detailEl = document.createElement("p");
  detailEl.className = "stream-detail";
  detailEl.textContent = detail || "事件已记录";

  body.append(title, detailEl);

  const state = document.createElement("span");
  state.className = "stream-state";
  state.dataset.status = status.value;
  state.textContent = status.text;

  row.append(time, dot, body, state);

  els.stream.prepend(row);
  while (els.stream.children.length > MAX_ROWS) {
    els.stream.lastElementChild.remove();
  }

  counters.events += 1;
  if (els.empty) els.empty.hidden = true;
  if (els.meta) {
    els.meta.textContent = "最后一条 " + timeFor(event) + " · " + counters.events + " 条";
  }
}

function handleEvent(event) {
  if (!event || typeof event !== "object") return;
  const type = String(event.event_type || event.type || "").toLowerCase();
  const kind = kindFor(type, event.status);
  account(event, kind);
  renderRow(event);
}

function setConn(state, text) {
  if (!els.conn) return;
  els.conn.dataset.state = state;
  if (els.connText) els.connText.textContent = text;
}

/* ------------------------------------------------------------------ demo */
const DEMO_EVENTS = [
  {
    event_type: "request_received",
    agent: "supervisor",
    status: "running",
    payload: { message_chars: 32 },
  },
  {
    event_type: "route_selected",
    agent: "supervisor",
    status: "completed",
    payload: { intent: "logistics", target_agent: "logistics_agent" },
  },
  {
    event_type: "memory_retrieved",
    agent: "nanomemory",
    status: "completed",
    payload: { accepted: 3, rejected: 1 },
  },
  {
    event_type: "tool_started",
    agent: "logistics",
    status: "running",
    payload: { tool: "get_logistics" },
  },
  {
    event_type: "tool_completed",
    agent: "logistics",
    status: "completed",
    payload: { tool: "get_logistics", latency_ms: 42 },
  },
  {
    event_type: "governance_blocked",
    agent: "mem_guard",
    status: "blocked",
    payload: { reason_code: "refund_exceeds_authoritative_limit" },
  },
  {
    event_type: "memory_written",
    agent: "nanomemory",
    status: "completed",
    payload: { memory_type: "preference" },
  },
  {
    event_type: "run_completed",
    agent: "logistics_agent",
    status: "completed",
    payload: { total_tokens: 684, latency_ms: 312 },
  },
];

let demoTimer = null;

function startDemo(reason) {
  if (demoTimer) return;
  setConn("demo", "演示模式");
  if (els.meta) els.meta.textContent = "演示数据 · " + reason;
  let index = 0;
  demoTimer = window.setInterval(() => {
    handleEvent({ ...DEMO_EVENTS[index % DEMO_EVENTS.length], created_at: new Date().toISOString() });
    index += 1;
  }, 1600);
  handleEvent({ ...DEMO_EVENTS[0], created_at: new Date().toISOString() });
}

/* ------------------------------------------------------------------ socket */
function socketUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const params = new URLSearchParams(location.search);
  const query = new URLSearchParams();
  for (const key of ["token", "admin_key"]) {
    const value = params.get(key);
    if (value) query.set(key, value);
  }
  const search = query.toString();
  return proto + "://" + location.host + ENDPOINT + (search ? "?" + search : "");
}

function connect() {
  setConn("connecting", "连接中");
  if (location.protocol === "file:") {
    startDemo("本地预览");
    return;
  }

  let socket;
  try {
    socket = new WebSocket(socketUrl());
  } catch {
    startDemo("无法建立连接");
    return;
  }

  socket.addEventListener("open", () => {
    if (demoTimer) {
      window.clearInterval(demoTimer);
      demoTimer = null;
    }
    setConn("live", "实时连接");
    if (els.meta) els.meta.textContent = "实时事件流";
  });

  socket.addEventListener("message", (message) => {
    try {
      handleEvent(JSON.parse(message.data));
    } catch {
      /* malformed frames are ignored; the console never renders raw payloads */
    }
  });

  socket.addEventListener("error", () => startDemo("连接异常"));
  socket.addEventListener("close", () => {
    if (!demoTimer) startDemo("连接已关闭");
  });
}

connect();
document.body.dataset.ready = "1";
