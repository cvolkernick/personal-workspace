/**
 * FitDash #263: Hidrate bottle charge as a mini sleep-style battery.
 * Overlay only — does not change Hidrate API parsing or Recovery sleep math.
 */
(function (root) {
  "use strict";

  function $(id) {
    return root.document ? root.document.getElementById(id) : null;
  }

  function bottleFrom(data) {
    if (!data || typeof data !== "object") return null;
    if (data.hidrate_bottle && typeof data.hidrate_bottle === "object") {
      return data.hidrate_bottle;
    }
    const bars = data.hydration_bars;
    if (bars && bars.bottle && typeof bars.bottle === "object") {
      return bars.bottle;
    }
    return null;
  }

  function fmtPercent(n) {
    if (n == null || n === "") return "";
    const num = Number(n);
    if (!Number.isFinite(num)) return "";
    if (Number.isInteger(num)) return String(num);
    return String(Math.round(num * 10) / 10);
  }

  function clampPct(n) {
    if (n == null || n === "") return null;
    const num = Number(n);
    if (!Number.isFinite(num)) return null;
    return Math.min(100, Math.max(0, num));
  }

  /** Sleep-battery awake bands from rt_dashboard/sleep_battery.py — reuse, don't invent. */
  function fillLevel(pct) {
    if (pct < 25) return "critical";
    if (pct < 50) return "low";
    if (pct < 85) return "ok";
    return "full";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderMiniHtml(name, pct) {
    const hasPct = pct != null;
    const fillPct = hasPct ? Math.round(pct) : 0;
    const level = hasPct ? fillLevel(pct) : "";
    const label = hasPct ? fillPct + "%" : "—";
    const aria = hasPct
      ? "Bottle charge " + fillPct + " percent"
      : "Bottle charge unavailable";
    const fillClass = "sb-fill" + (level ? " " + level : "");
    return (
      '<span class="hbb-row">' +
      '<span class="hbb-name">' +
      escapeHtml(name) +
      "</span>" +
      '<span class="sb-shell hbb-shell" aria-label="' +
      escapeHtml(aria) +
      '">' +
      '<span class="sb-fill-wrap hbb-fill-wrap">' +
      '<span class="' +
      fillClass +
      '" style="height:' +
      fillPct +
      '%"></span>' +
      '<span class="sb-label hbb-label"><span class="sb-big">' +
      label +
      "</span></span>" +
      "</span></span></span>"
    );
  }

  function emptyTitle(status) {
    if (status === "unavailable") return "Hidrate bottle charge unavailable";
    if (status === "empty") return "No Hidrate bottles on this account";
    if (status === "missing_field") return "Hidrate Bottle has no charge field";
    if (status === "not_configured") return "Hidrate credentials not configured";
    return "Hidrate bottle charge unavailable";
  }

  function paint(data, target) {
    const el = target || $("hidrate-bottle-charge");
    if (!el) return el;
    const bottle = bottleFrom(data);
    if (!bottle) {
      el.innerHTML = renderMiniHtml("Bottle", null);
      if (el.removeAttribute) el.removeAttribute("title");
      return el;
    }
    const pct = clampPct(bottle.percent);
    if (bottle.available && pct != null) {
      const name = String(bottle.name || "").trim() || "Bottle";
      el.innerHTML = renderMiniHtml(name, pct);
      el.title = bottle.field ? "Hidrate " + bottle.field : "Hidrate bottle charge";
      return el;
    }
    el.innerHTML = renderMiniHtml("Bottle", null);
    el.title = emptyTitle(String(bottle.status || ""));
    return el;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      bottleFrom: bottleFrom,
      fmtPercent: fmtPercent,
      clampPct: clampPct,
      fillLevel: fillLevel,
      renderMiniHtml: renderMiniHtml,
      paint: paint,
    };
  }

  if (root.document && root.fetch) {
    const origFetch = root.fetch;
    root.fetch = function () {
      const args = Array.prototype.slice.call(arguments);
      const url = typeof args[0] === "string" ? args[0] : (args[0] && args[0].url) || "";
      return origFetch.apply(this, args).then(function (res) {
        if (res && res.ok && String(url).indexOf("/api/dashboard") !== -1) {
          res
            .clone()
            .json()
            .then(function (data) {
              setTimeout(function () {
                paint(data);
              }, 40);
            })
            .catch(function () {});
        }
        return res;
      });
    };
  }
})(typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : this);
