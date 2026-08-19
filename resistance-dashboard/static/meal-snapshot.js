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
    ((((data && data.health) || {}).food_logs) || []).forEach(function (f) {
      const d = civilDay(f.date);
      if (d) dates.push(d);
    });
    dates.sort();
    return dates.length ? dates[dates.length - 1] : "";
  }
  function allFoodLogs(data) {
    const store = (data && data.nutrition_store) || {};
    const healthLogs = (((data && data.health) || {}).food_logs) || [];
    if ((store.food_logs || []).length) return store.food_logs;
    if (healthLogs.length) return healthLogs;
    if ((store.food_logs_recent || []).length) return store.food_logs_recent;
    return store.food_logs_today || [];
  }
  function logsForDay(logs, day) {
    if (!day) return [];
    return (logs || []).filter(function (f) {
      return civilDay(f.date) === day;
    });
  }
  function consumedFromLogs(logs, day) {
    if (!logs || !logs.length) return null;
    return {
      date: day,
      calories: logs.reduce(function (s, f) { return s + (Number(f.calories) || 0); }, 0),
      protein_g: logs.reduce(function (s, f) { return s + (Number(f.protein_g) || 0); }, 0),
      carbs_g: logs.reduce(function (s, f) { return s + (Number(f.carbs_g) || 0); }, 0),
      fat_g: logs.reduce(function (s, f) { return s + (Number(f.fat_g) || 0); }, 0),
      food_log_count: logs.length,
      source: "food_logs",
    };
  }
  function diagnosticLine(data, nLogs) {
    const today = civilDay((data.meta && data.meta.local_today) || "");
    return (
      "local_today=" +
      (today || "—") +
      "; last_nutrition_date=" +
      (lastNutritionDate(data) || "—") +
      "; food_log_count=" +
      (nLogs || 0)
    );
  }
  function collectDayLogs(data) {
    const today = civilDay((data.meta && data.meta.local_today) || "");
    const store = (data && data.nutrition_store) || {};
    const all = allFoodLogs(data);
    let logs = logsForDay(store.food_logs_today || [], today);
    if (!logs.length) logs = logsForDay(all, today);
    let day = today;
    let heading = "Logged today";
    let fallback = false;
    if (!logs.length) {
      const dates = all.map(function (f) { return civilDay(f.date); }).filter(Boolean);
      dates.sort();
      const latest = dates.length ? dates[dates.length - 1] : "";
      const candidates = [];
      const last = lastNutritionDate(data);
      if (last) candidates.push(last);
      if (latest && latest !== last) candidates.push(latest);
      for (let i = 0; i < candidates.length; i++) {
        const found = logsForDay(all, candidates[i]);
        if (found.length) {
          logs = found;
          day = candidates[i];
          heading = "Logged " + day;
          fallback = true;
          break;
        }
      }
    }
    return { logs: logs, day: day, heading: heading, fallback: fallback, allCount: all.length, today: today };
  }
  function loggedConsumed(data, picked) {
    const today = picked.today;
    const store = (data && data.nutrition_store) || {};
    const coachCons =
      ((((data.coach || {}).today || {}).nutrition || {}).consumed) || null;
    const matchDay = function (c, day) {
      return hasLoggedMacros(c) && (!day || !c.date || civilDay(c.date) === day);
    };
    if (matchDay(store.today_consumed, today)) return store.today_consumed;
    if (matchDay(coachCons, today)) return coachCons;
    const dayNut = (((data.health || {}).nutrition) || []).find(function (n) {
      return today && civilDay(n.date) === today;
    });
    if (hasLoggedMacros(dayNut)) return dayNut;
    if (!picked.fallback) {
      const fromToday = consumedFromLogs(picked.logs, today);
      if (fromToday) return fromToday;
    }
    if (picked.fallback && picked.logs.length) {
      const lastNut = (((data.health || {}).nutrition) || []).find(function (n) {
        return picked.day && civilDay(n.date) === picked.day;
      });
      if (hasLoggedMacros(lastNut)) return lastNut;
      return consumedFromLogs(picked.logs, picked.day);
    }
    return null;
  }
  function paint(data) {
    if (!data) return;
    const store = (data && data.nutrition_store) || {};
    const picked = collectDayLogs(data);
    const logs = picked.logs;
    const foods = $("today-logged-foods");
    if (foods) {
      if (!logs.length) {
        foods.textContent = diagnosticLine(data, picked.allCount);
      } else {
        foods.innerHTML =
          '<div class="today-subh" style="font-size:0.8rem;margin-bottom:0.3rem">' +
          picked.heading +
          "</div>" +
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
    const cons = loggedConsumed(data, picked) || {};
    const n = ((((data.coach || {}).today || {}).nutrition) || {});
    const nLogs =
      cons.food_log_count != null
        ? cons.food_log_count
        : n.food_log_count != null
        ? n.food_log_count
        : logs.length;
    if (!hasLoggedMacros(cons)) {
      if (!logs.length) macros.textContent = "";
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
            setTimeout(function () { paint(data); }, 800);
          })
          .catch(function () {});
      }
      return res;
    });
  };
})();
