/**
 * Shared Orchestrator navigation for FCC surfaces.
 *
 * Mirrors Orchestra's openDomain flow:
 *  1) user gesture
 *  2) POST /api/open-orchestra — probe port 8790; start orchestra/server.py if down
 *  3) navigate to live URL (same-tab "return home" by default)
 */
(function (global) {
  "use strict";

  const FALLBACK_URL = "http://127.0.0.1:8790/";
  const API = "/api/open-orchestra";

  function publicizeUrl(url) {
    if (!url) return FALLBACK_URL;
    try {
      const pageHost = global.location && global.location.hostname;
      if (
        pageHost &&
        pageHost !== "127.0.0.1" &&
        pageHost !== "localhost" &&
        (url.includes("://127.0.0.1") || url.includes("://localhost"))
      ) {
        return url
          .replace("://127.0.0.1", "://" + pageHost)
          .replace("://localhost", "://" + pageHost);
      }
    } catch (_) {
      /* ignore */
    }
    return url;
  }

  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      if (!btn.dataset.idleLabel) btn.dataset.idleLabel = btn.textContent || "";
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
      btn.textContent = label || "Starting Orchestrator…";
    } else {
      btn.disabled = false;
      btn.removeAttribute("aria-busy");
      btn.textContent = btn.dataset.idleLabel || "← Orchestrator";
    }
  }

  function toast(msg, isError) {
    // Prefer FCC toast if present
    const el = document.getElementById("toast");
    if (el && typeof el.classList !== "undefined") {
      el.textContent = msg;
      el.classList.add("show");
      if (el._orchTimer) clearTimeout(el._orchTimer);
      el._orchTimer = setTimeout(() => el.classList.remove("show"), isError ? 5000 : 3200);
      return;
    }
    // Lightweight fallback
    let t = document.getElementById("orch-nav-toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "orch-nav-toast";
      t.setAttribute("role", "status");
      t.style.cssText =
        "position:fixed;bottom:1rem;right:1rem;z-index:99;max-width:22rem;" +
        "padding:0.55rem 0.75rem;border-radius:10px;font-size:0.85rem;font-weight:600;" +
        "border:1px solid #1e2a36;background:#15202c;color:#e7eef5;" +
        "box-shadow:0 8px 24px rgba(0,0,0,.35);display:none";
      document.body.appendChild(t);
    }
    t.style.display = "block";
    t.style.background = isError ? "#2a1515" : "#0f2419";
    t.style.borderColor = isError ? "#6b2e2e" : "#245c42";
    t.style.color = isError ? "#ff6b6b" : "#3dd68c";
    t.textContent = msg;
    if (t._timer) clearTimeout(t._timer);
    t._timer = setTimeout(() => {
      t.style.display = "none";
    }, isError ? 5000 : 3200);
  }

  /**
   * @param {object} [opts]
   * @param {HTMLElement} [opts.button]
   * @param {boolean} [opts.sameTab=true]  return navigation vs new tab
   * @param {string} [opts.fallbackUrl]
   */
  async function openOrchestrator(opts) {
    opts = opts || {};
    const btn = opts.button || null;
    const sameTab = opts.sameTab !== false;
    const fallback = publicizeUrl(opts.fallbackUrl || FALLBACK_URL);

    setBusy(btn, true, "Checking Orchestrator…");
    toast("Checking if Orchestrator is running…", false);

    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 25000);
      const res = await fetch(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ready_timeout: 20 }),
        signal: controller.signal,
        cache: "no-store",
      });
      clearTimeout(timer);
      const data = await res.json().catch(() => ({}));
      const url = publicizeUrl(data.url || fallback);

      if (!data.ok || !data.live) {
        const err = data.error || "Could not start Orchestrator";
        toast(err, true);
        setBusy(btn, false);
        // Last resort: try the URL anyway (server may still be coming up)
        if (sameTab) {
          setTimeout(() => {
            global.location.href = url;
          }, 1200);
        } else {
          global.open(url, "_blank", "noopener");
        }
        return data;
      }

      const note = data.already_running
        ? "Orchestrator is already running — opening…"
        : "Orchestrator started — opening…";
      toast(note, false);
      setBusy(btn, true, "Opening…");

      if (sameTab) {
        global.location.href = url;
      } else {
        global.open(url, "_blank", "noopener");
        setBusy(btn, false);
      }
      return data;
    } catch (e) {
      const msg =
        e && e.name === "AbortError"
          ? "Timed out waiting for Orchestrator (25s)."
          : "Orchestrator launch error: " + (e && e.message ? e.message : e);
      toast(msg, true);
      setBusy(btn, false);
      // Soft fallback navigate
      try {
        if (sameTab) global.location.href = fallback;
        else global.open(fallback, "_blank", "noopener");
      } catch (_) {
        /* ignore */
      }
      return { ok: false, error: msg };
    }
  }

  function wireButton(el) {
    if (!el || el.dataset.orchWired === "1") return;
    el.dataset.orchWired = "1";
    el.addEventListener("click", (ev) => {
      ev.preventDefault();
      openOrchestrator({
        button: el,
        sameTab: el.dataset.sameTab !== "0",
        fallbackUrl: el.getAttribute("href") || FALLBACK_URL,
      });
    });
  }

  function autoWire() {
    document
      .querySelectorAll("[data-open-orchestra], #nav-orchestra, #btn-orchestra")
      .forEach(wireButton);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoWire);
  } else {
    autoWire();
  }

  global.openOrchestrator = openOrchestrator;
  global.wireOrchestraButton = wireButton;
})(typeof window !== "undefined" ? window : globalThis);
