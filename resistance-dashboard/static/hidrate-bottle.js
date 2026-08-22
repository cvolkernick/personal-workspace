(function () {
  function $(id) {
    return document.getElementById(id);
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
  function paint(data) {
    const el = $("hidrate-bottle-charge");
    if (!el) return;
    const bottle = bottleFrom(data);
    if (!bottle) {
      el.textContent = "Bottle —";
      el.removeAttribute("title");
      return;
    }
    const pct = fmtPercent(bottle.percent);
    if (bottle.available && pct !== "") {
      const name = String(bottle.name || "").trim();
      el.textContent = name ? name + " " + pct + "%" : "Bottle " + pct + "%";
      el.title = bottle.field ? "Hidrate " + bottle.field : "Hidrate bottle charge";
      return;
    }
    const status = String(bottle.status || "");
    el.textContent = "Bottle —";
    if (status === "unavailable") {
      el.title = "Hidrate bottle charge unavailable";
    } else if (status === "empty") {
      el.title = "No Hidrate bottles on this account";
    } else if (status === "missing_field") {
      el.title = "Hidrate Bottle has no charge field";
    } else if (status === "not_configured") {
      el.title = "Hidrate credentials not configured";
    } else {
      el.title = "Hidrate bottle charge unavailable";
    }
  }
  const origFetch = window.fetch;
  window.fetch = function () {
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
})();
