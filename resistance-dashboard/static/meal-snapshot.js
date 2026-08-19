(function () {
  function $(id) {
    return document.getElementById(id);
  }
  function civilDay(value) {
    return String(value || "").slice(0, 10);
  }
  function fmtNum(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return Math.round(Number(n)).toLocaleString();
  }
  function hasLoggedMacros(c) {
    if (!c || typeof c !== "object") return false;
    return (
      c.calories != null ||
      c.protein_g != null ||
      c.carbs_g != null ||
      c.fat_g != null
    );
  }
  function lastNutritionDate(data) {
    const store = (data && data.nutrition_store) || {};
    if (store.last_nutrition_date) return civilDay(store.last_nutrition_date);
    const dates = [];
    (((data && data.health) || {}).nutrition || []).forEach(function (n) {
      const d = civilDay(n.date);
      if (d) dates.push(d);
    });
    (store.food_logs || []).forEach(function (f) {
      const d = civilDay(f.date);
      if (d) dates.push(d);
    });
    dates.sort();
    return dates.length ? dates[dates.length - 1] : "";
  }
  function loggedConsumed(data) {
    const today = civilDay((data.meta && data.meta.local_today) || "");
    const store = (data && data.nutrition_store) || {};
    const coachCons =
      ((((data.coach || {}).today || {}).nutrition || {}).consumed) || null;
    const matchDay = function (c) {
      return hasLoggedMacros(c) && (!today || !c.date || civilDay(c.date) === today);
    };
    if (matchDay(store.today_consumed)) return store.today_consumed;
    if (matchDay(coachCons)) return coachCons;
    const day = (((data.health || {}).nutrition) || []).find(function (n) {
      return today && civilDay(n.date) === today;
    });
    if (hasLoggedMacros(day)) return day;
    const logs = (store.food_logs_today || []).filter(function (f) {
      return !today || !f.date || civilDay(f.date) === today;
    });
    if (!logs.length) return null;
    return {
      date: today,
      calories: logs.reduce(function (s, f) { return s + (Number(f.calories) || 0); }, 0),
      protein_g: logs.reduce(function (s, f) { return s + (Number(f.protein_g) || 0); }, 0),
      carbs_g: logs.reduce(function (s, f) { return s + (Number(f.carbs_g) || 0); }, 0),
      fat_g: logs.reduce(function (s, f) { return s + (Number(f.fat_g) || 0); }, 0),
      food_log_count: logs.length,
      source: "food_logs",
    };
  }
  function paint(data) {
    if (!data) return;
    const today = civilDay((data.meta && data.meta.local_today) || "");
    const store = (data && data.nutrition_store) || {};
    let logs = store.food_logs_today || [];
    if (today) {
      const dated = logs.filter(function (f) { return civilDay(f.date) === today; });
      if (dated.length) logs = dated;
    }
    const foods = $("today-logged-foods");
    if (foods) {
      if (!logs.length) {
        foods.innerHTML = "";
      } else {
        foods.innerHTML =
          '<div class="today-subh" style="font-size:0.8rem;margin-bottom:0.3rem">Logged today</div>' +
          '<ul style="margin:0;padding-left:1.1rem">' +
          logs
            .map(function (f) {
              const kcal = f.calories != null ? fmtNum(f.calories) + " kcal" : "— kcal";
              return "<li><strong>" + (f.name || "Food") + "</strong> · " + kcal + "</li>";
            })
            .join("") +
          "</ul>";
      }
    }
    const macros = $("today-macros");
    if (!macros) return;
    const cons = loggedConsumed(data) || {};
    const n = ((((data.coach || {}).today || {}).nutrition) || {});
    const nLogs =
      cons.food_log_count != null
        ? cons.food_log_count
        : n.food_log_count != null
        ? n.food_log_count
        : logs.length;
    if (!hasLoggedMacros(cons)) {
      macros.textContent =
        "local_today=" +
        (today || "—") +
        "; last_nutrition_date=" +
        (lastNutritionDate(data) || "—") +
        "; food_log_count=" +
        (nLogs || 0);
      return;
    }
    const tgt = n.targets || store.targets || {};
    const hasTargets = tgt.calories != null || tgt.protein_g != null;
    const rem = n.remaining || {};
    macros.innerHTML =
      "<strong>Logged so far</strong>" +
      (nLogs != null && nLogs !== ""
        ? " (" + nLogs + " meal log" + (nLogs === 1 ? "" : "s") + ")"
        : "") +
      ": " +
      fmtNum(cons.calories) +
      " kcal · P" +
      fmtNum(cons.protein_g) +
      " C" +
      fmtNum(cons.carbs_g) +
      " F" +
      fmtNum(cons.fat_g) +
      (hasTargets
        ? "<br/><strong>Remaining</strong>: " +
          fmtNum(rem.calories) +
          " kcal · P" +
          fmtNum(rem.protein_g) +
          " C" +
          fmtNum(rem.carbs_g) +
          " F" +
          fmtNum(rem.fat_g)
        : "");
  }
  const origFetch = window.fetch;
  window.fetch = function () {
    const req = arguments[0];
    const url = typeof req === "string" ? req : (req && req.url) || "";
    return origFetch.apply(this, arguments).then(function (res) {
      if (res && res.ok && String(url).indexOf("/api/dashboard") !== -1) {
        res
          .clone()
          .json()
          .then(function (data) {
            setTimeout(function () { paint(data); }, 40);
            setTimeout(function () { paint(data); }, 350);
          })
          .catch(function () {});
      }
      return res;
    });
  };
})();
