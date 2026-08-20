(function () {
  function $(id) {
    return document.getElementById(id);
  }
  function setText(id, text) {
    const el = $(id);
    if (el) el.textContent = text || "";
  }
  const UNOFFICIAL =
    "Unofficial Grok-CLI client (not an xAI-published FitDash app). Needs SuperGrok or X Premium+. If inference returns 403 after a good login, that is xAI entitlement gating, not a FitDash bug.";

  let pollTimer = null;
  let pollDeadline = 0;

  function stopPoll() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function renderStatus(data) {
    const line = $("supergrok-status");
    const ask = $("ask-auth-status");
    if (!data) return;
    let text = "";
    if (data.connected) {
      text = "SuperGrok connected" + (data.email ? " · " + data.email : "");
    } else if (data.source === "xai_api_key" && data.ok) {
      text = "Preview fallback · shared XAI_API_KEY (no per-user SuperGrok)";
    } else {
      text = data.error || "Connect SuperGrok to generate today's meal/workout plan.";
    }
    if (line) line.textContent = text;
    if (ask && !ask.dataset.supergrokLock) {
      ask.textContent = text;
    }
  }

  async function refreshStatus() {
    try {
      const res = await fetch("/api/ask/status");
      const data = await res.json();
      if (res.status === 401) return data;
      renderStatus(data);
      return data;
    } catch (e) {
      setText("supergrok-status", "Could not check SuperGrok: " + (e.message || e));
      return null;
    }
  }

  async function startConnect() {
    stopPoll();
    setText("supergrok-code", "");
    setText("supergrok-url", "Starting device login…");
    try {
      const res = await fetch("/api/ask/grok/start", { method: "POST" });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || data.message || "HTTP " + res.status);
      }
      const uri = data.verification_uri_complete || data.verification_uri || "";
      setText("supergrok-url", uri);
      setText("supergrok-code", data.user_code || "");
      const link = $("supergrok-open");
      if (link) {
        link.href = uri;
        link.hidden = !uri;
      }
      const interval = Math.max(3, Number(data.interval) || 5) * 1000;
      pollDeadline = Date.now() + Math.max(30, Number(data.expires_in) || 1800) * 1000;
      schedulePoll(interval);
    } catch (e) {
      setText("supergrok-url", "");
      setText("supergrok-code", "");
      setText("supergrok-status", e.message || String(e));
    }
  }

  function schedulePoll(interval) {
    stopPoll();
    pollTimer = setTimeout(function () {
      pollOnce(interval);
    }, interval);
  }

  async function pollOnce(interval) {
    if (Date.now() > pollDeadline) {
      setText("supergrok-status", "Device code expired. Start Connect SuperGrok again.");
      return;
    }
    try {
      const res = await fetch("/api/ask/grok/poll");
      const data = await res.json();
      if (data.status === "approved") {
        setText("supergrok-code", "");
        setText("supergrok-url", "");
        const link = $("supergrok-open");
        if (link) link.hidden = true;
        renderStatus({ connected: true, email: data.email, ok: true, source: "supergrok_session" });
        return;
      }
      if (data.status === "denied") {
        setText("supergrok-status", "Authorization denied.");
        return;
      }
      if (data.status === "expired") {
        setText("supergrok-status", "Device code expired. Start Connect SuperGrok again.");
        return;
      }
      schedulePoll(interval);
    } catch (e) {
      schedulePoll(interval);
    }
  }

  async function disconnect() {
    stopPoll();
    try {
      const res = await fetch("/api/ask/grok/disconnect", { method: "POST" });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || "HTTP " + res.status);
      }
      renderStatus({ ok: false, connected: false, error: "Connect SuperGrok to generate today's meal/workout plan." });
    } catch (e) {
      setText("supergrok-status", e.message || String(e));
    }
  }

  async function generatePlans() {
    const meal = $("today-meal");
    const lift = $("today-workout");
    const status = $("supergrok-status");
    if (status) status.textContent = "Generating today's meal/workout via SuperGrok…";
    try {
      const res = await fetch("/api/ask/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const data = await res.json();
      if (data.meal && meal) {
        meal.textContent = data.meal.message || "";
      }
      if (data.workout && lift) {
        lift.textContent = data.workout.message || "";
      }
      if (!data.ok) {
        if (status) status.textContent = data.error || "Connect SuperGrok to generate today's meal/workout plan.";
        return;
      }
      if (status) status.textContent = "Plans generated via SuperGrok.";
    } catch (e) {
      if (status) status.textContent = e.message || String(e);
    }
  }

  function bind() {
    const note = $("supergrok-note");
    if (note) note.textContent = UNOFFICIAL;
    const help = $("ask-grok-help");
    if (help) {
      help.textContent =
        "Unofficial Grok-CLI client. Connect SuperGrok in More (tokens stay sealed in Turso). " +
        "Needs SuperGrok or X Premium+. 403 after a good login is xAI entitlement gating, not a FitDash bug.";
    }
    const btn = $("btn-connect-supergrok");
    if (btn) btn.addEventListener("click", startConnect);
    const disc = $("btn-disconnect-supergrok");
    if (disc) disc.addEventListener("click", disconnect);
    const gen = $("btn-generate-workout");
    if (gen) {
      gen.addEventListener(
        "click",
        function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          generatePlans();
        },
        true
      );
    }
    refreshStatus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
