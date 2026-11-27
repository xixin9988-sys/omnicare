/* OmniCare chat surface.
 *
 * States are explicit, not implicit:
 *   connecting -> ready -> sending -> ready
 *                        \-> unauthorized / forbidden / rate_limited / unavailable / invalid
 * Every failure renders a banner with a recovery action; the composer is never left silently
 * broken. Feedback is bound to the memory trace id returned with each answer.
 */
(function () {
  "use strict";

  var params = new URLSearchParams(window.location.search);
  var state = {
    sessionId: null,
    busy: false,
    user: params.get("user") || "demo_user_001",
    token: params.get("token") || "",
    retryAt: 0
  };

  var el = {
    status: document.getElementById("status"),
    banner: document.getElementById("banner"),
    bannerTitle: document.getElementById("banner-title"),
    bannerDetail: document.getElementById("banner-detail"),
    bannerRetry: document.getElementById("banner-retry"),
    log: document.getElementById("log"),
    empty: document.getElementById("empty"),
    typing: document.getElementById("typing"),
    form: document.getElementById("form"),
    message: document.getElementById("message"),
    send: document.getElementById("send")
  };

  function ApiError(status, code, message, retryAfter) {
    this.status = status;
    this.code = code;
    this.message = message;
    this.retryAfter = retryAfter;
  }

  function authHeaders() {
    var headers = { "Content-Type": "application/json" };
    if (state.token) {
      headers.Authorization = "Bearer " + state.token;
    } else {
      headers["X-User-Id"] = state.user;
    }
    return headers;
  }

  async function api(path, options) {
    var response;
    try {
      response = await fetch(path, Object.assign({ headers: authHeaders() }, options || {}));
    } catch (error) {
      throw new ApiError(0, "network_error", "网络连接失败，请检查服务是否在运行。");
    }
    var body = null;
    try {
      body = await response.json();
    } catch (error) {
      body = null;
    }
    if (!response.ok) {
      var detail = body && body.detail ? body.detail : {};
      var code = detail.code || (body && body.code) || "http_error";
      var message = detail.message || (body && body.message) || "请求失败（HTTP " + response.status + "）。";
      if (Array.isArray(detail)) {
        message = "请求参数不合法。";
      }
      throw new ApiError(response.status, code, message, response.headers.get("Retry-After"));
    }
    return body;
  }

  function setStatus(kind, text) {
    el.status.dataset.state = kind;
    el.status.textContent = text;
  }

  function setBusy(busy) {
    state.busy = busy;
    el.message.disabled = busy;
    el.send.disabled = busy;
    el.send.textContent = busy ? "发送中…" : "发送";
  }

  function showBanner(kind, title, detail, retry) {
    el.banner.hidden = false;
    el.banner.dataset.kind = kind;
    el.bannerTitle.textContent = title;
    el.bannerDetail.textContent = detail || "";
    if (retry) {
      el.bannerRetry.hidden = false;
      el.bannerRetry.onclick = retry;
    } else {
      el.bannerRetry.hidden = true;
      el.bannerRetry.onclick = null;
    }
  }

  function hideBanner() {
    el.banner.hidden = true;
  }

  function hideEmpty() {
    el.empty.hidden = true;
  }

  function appendMessage(role, text) {
    hideEmpty();
    var item = document.createElement("li");
    item.className = "chat-msg";
    item.dataset.role = role;
    var bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.textContent = text;
    item.appendChild(bubble);
    el.log.appendChild(item);
    el.log.scrollTop = el.log.scrollHeight;
    return item;
  }

  // A missing trace id usually means the supervisor emitted memory_degraded: the answer is
  // still valid (it came from the business database), but it did not use long-term memory.
  function traceBadge(traceId, degraded) {
    var badge = document.createElement("span");
    badge.className = "chat-trace";
    if (traceId) {
      badge.textContent = "trace " + traceId.slice(0, 18);
      badge.title = traceId;
    } else {
      badge.textContent = "无记忆追踪（降级）";
      badge.dataset.degraded = "1";
    }
    if (degraded) {
      badge.dataset.degraded = "1";
    }
    return badge;
  }

  function appendAssistant(reply, traceId, blocked) {
    var item = appendMessage("assistant", reply);
    var meta = document.createElement("div");
    meta.className = "chat-meta";
    meta.appendChild(traceBadge(traceId, !traceId));
    if (blocked) {
      var flag = document.createElement("span");
      flag.textContent = "治理阻断";
      flag.style.color = "var(--red)";
      meta.appendChild(flag);
    }
    if (traceId) {
      meta.appendChild(feedbackGroup(traceId));
    }
    item.appendChild(meta);
  }

  function button(label, className) {
    var node = document.createElement("button");
    node.type = "button";
    node.textContent = label;
    if (className) node.className = className;
    return node;
  }

  function feedbackGroup(traceId) {
    var group = document.createElement("div");
    group.className = "chat-feedback";
    var up = button("👍 有帮助");
    var down = button("👎 不准确");
    up.setAttribute("aria-label", "这条回答有帮助");
    down.setAttribute("aria-label", "这条回答不准确");
    up.addEventListener("click", function () {
      submitFeedback(traceId, "up", "helpful", group);
    });
    down.addEventListener("click", function () {
      showReasons(traceId, group);
    });
    group.append(up, down);
    return group;
  }

  function showReasons(traceId, group) {
    var reasons = [
      ["wrong_memory", "答非所问"],
      ["outdated", "信息过时"],
      ["fabricated", "编造信息"],
      ["other", "其他"]
    ];
    var row = document.createElement("div");
    row.className = "chat-reasons";
    reasons.forEach(function (pair) {
      var chip = button(pair[1]);
      chip.addEventListener("click", function () {
        submitFeedback(traceId, "down", pair[0], group);
      });
      row.appendChild(chip);
    });
    group.replaceChildren(row);
  }

  async function submitFeedback(traceId, verdict, reason, group) {
    var buttons = group.querySelectorAll("button");
    buttons.forEach(function (node) {
      node.disabled = true;
    });
    try {
      await api("/api/v1/feedback", {
        method: "POST",
        body: JSON.stringify({ trace_id: traceId, verdict: verdict, reason: reason })
      });
      var done = document.createElement("span");
      done.className = "chat-feedback-done";
      done.textContent = verdict === "up" ? "已记录：有帮助" : "已记录：待改进";
      group.replaceChildren(done);
    } catch (error) {
      group.replaceChildren(feedbackGroup(traceId));
      showBanner("warning", "反馈未记录", error.message);
    }
  }

  async function ensureSession(force) {
    if (state.sessionId && !force) return state.sessionId;
    var created = await api("/api/v1/sessions", { method: "POST", body: "{}" });
    state.sessionId = created.session_id;
    return state.sessionId;
  }

  function secondsUntilRetry() {
    return Math.max(0, Math.ceil((state.retryAt - Date.now()) / 1000));
  }

  function handleError(error, retry) {
    hideEmpty();
    if (error.status === 401) {
      setStatus("error", "未登录");
      showBanner("error", "登录状态无效（401）", "请使用带 token 的链接重新进入，例如 /ui/chat.html?token=…");
      return;
    }
    if (error.status === 403) {
      setStatus("error", "无权限");
      showBanner("error", "没有权限（403）", "当前账号不能执行该操作，请联系管理员。");
      return;
    }
    if (error.status === 429) {
      var retryAfter = Number(error.retryAfter || 30);
      state.retryAt = Date.now() + retryAfter * 1000;
      setStatus("error", "限流中");
      showBanner(
        "warning",
        "请求过于频繁（429）",
        "记忆网关按租户限流；请在 " + retryAfter + " 秒后重试。",
        function () {
          if (secondsUntilRetry() > 0) return;
          retry();
        }
      );
      return;
    }
    if (error.status === 0 || error.status >= 500) {
      setStatus("error", "服务不可用");
      showBanner("error", "服务暂时不可用", error.message + " 记忆不可用时会降级为无记忆回答。", retry);
      return;
    }
    if (error.status === 404) {
      showBanner("warning", "会话已失效", "正在为你重新建立会话。", retry);
      return;
    }
    showBanner("warning", "请求未完成", error.message, retry);
  }

  async function requestReply(text, retryAllowed) {
    if (state.busy) return;
    var wait = secondsUntilRetry();
    if (wait > 0) {
      showBanner("warning", "请稍后再试", "还需等待 " + wait + " 秒（记忆网关限流）。");
      return;
    }
    hideBanner();
    setBusy(true);
    setStatus("live", "思考中…");
    el.typing.hidden = false;
    try {
      var sessionId = await ensureSession(false);
      var payload = { session_id: sessionId, message: text };
      var data;
      try {
        data = await api("/api/v1/chat", { method: "POST", body: JSON.stringify(payload) });
      } catch (error) {
        if (error.status === 404 && error.code === "session_not_found" && retryAllowed) {
          await ensureSession(true);
          data = await api("/api/v1/chat", {
            method: "POST",
            body: JSON.stringify({ session_id: state.sessionId, message: text })
          });
        } else {
          throw error;
        }
      }
      appendAssistant(data.reply, data.trace_id, data.status === "blocked");
      setStatus("live", "连接正常");
      hideBanner();
    } catch (error) {
      handleError(error, function () {
        requestReply(text, false);
      });
    } finally {
      el.typing.hidden = true;
      setBusy(false);
      el.message.focus();
    }
  }

  async function submit() {
    var text = el.message.value.trim();
    if (!text || state.busy) return;
    appendMessage("user", text);
    el.message.value = "";
    autoGrow();
    await requestReply(text, true);
  }

  function autoGrow() {
    el.message.style.height = "auto";
    el.message.style.height = Math.min(160, el.message.scrollHeight) + "px";
  }

  el.form.addEventListener("submit", function (event) {
    event.preventDefault();
    submit();
  });

  el.message.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  });

  el.message.addEventListener("input", autoGrow);

  Array.prototype.forEach.call(
    document.querySelectorAll("#suggestions button"),
    function (node) {
      node.addEventListener("click", function () {
        el.message.value = node.dataset.prompt || node.textContent || "";
        submit();
      });
    }
  );

  // Surface auth/connectivity problems before the user types anything.
  ensureSession(false)
    .then(function () {
      setStatus("live", "会话就绪");
      el.message.focus();
    })
    .catch(function (error) {
      handleError(error, function () {
        window.location.reload();
      });
    });

  document.body.dataset.ready = "1";
})();
