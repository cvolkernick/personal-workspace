/* Resistance training dashboard front-end */
(() => {
  const $ = (id) => document.getElementById(id);

  let state = null;
  let volumeChart = null;
  let strengthChart = null;
  let weightChart = null;
  let sleepChart = null;
  let caloriesChart = null;
  let macrosChart = null;
  let hydrationChart = null;
  let selectedExercise = null;
  /** Collapse open/closed — memory + sessionStorage so GT re-renders / soft reloads keep preference. */
  // v5: reset sticky open from v4 so Training stays default-collapsed on Lift
  // (same as Meal/Targets). User toggle still wins for the rest of the session.
  const COLLAPSE_STORAGE_KEY = "fitdash-collapse-v5";
  const COLLAPSE_DEFAULTS = {
    quests: false,
    targets: false,
    "meal-sum": false,
    lift: false,
  };
  function readCollapseOpen() {
    try {
      const raw = sessionStorage.getItem(COLLAPSE_STORAGE_KEY);
      if (!raw) return { ...COLLAPSE_DEFAULTS };
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return { ...COLLAPSE_DEFAULTS };
      return { ...COLLAPSE_DEFAULTS, ...parsed };
    } catch (_) {
      return { ...COLLAPSE_DEFAULTS };
    }
  }
  const collapseOpen = readCollapseOpen();
  function persistCollapseOpen() {
    try {
      sessionStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify(collapseOpen));
    } catch (_) {
      /* private mode / quota — in-memory still works this session */
    }
  }
  /** Intake vs burned chart + cumulative summary window (days). */
  const CAL_IN_OUT_SPAN_DAYS = 45;

  function todayISO() {
    const d = new Date();
    const z = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
  }

  function civilDay(value) {
    return String(value || "").slice(0, 10);
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
    (((data && data.health) || {}).nutrition || []).forEach((n) => {
      const d = civilDay(n.date);
      if (d) dates.push(d);
    });
    (store.food_logs || []).forEach((f) => {
      const d = civilDay(f.date);
      if (d) dates.push(d);
    });
    dates.sort();
    return dates.length ? dates[dates.length - 1] : "";
  }

  function loggedConsumedForToday(data) {
    const today = civilDay((data.meta && data.meta.local_today) || "");
    const store = (data && data.nutrition_store) || {};
    const coachCons = ((((data.coach || {}).today || {}).nutrition || {}).consumed) || null;
    const storeCons = store.today_consumed || null;
    const matchDay = (c) =>
      hasLoggedMacros(c) && (!today || !c.date || civilDay(c.date) === today);
    if (matchDay(storeCons)) return storeCons;
    if (matchDay(coachCons)) return coachCons;
    const day = (((data.health || {}).nutrition) || []).find(
      (n) => today && civilDay(n.date) === today
    );
    if (hasLoggedMacros(day)) return day;
    const logs = (store.food_logs_today || []).filter(
      (f) => !today || !f.date || civilDay(f.date) === today
    );
    if (!logs.length) return null;
    return {
      date: today,
      calories: logs.reduce((s, f) => s + (Number(f.calories) || 0), 0),
      protein_g: logs.reduce((s, f) => s + (Number(f.protein_g) || 0), 0),
      carbs_g: logs.reduce((s, f) => s + (Number(f.carbs_g) || 0), 0),
      fat_g: logs.reduce((s, f) => s + (Number(f.fat_g) || 0), 0),
      food_log_count: logs.length,
      source: "food_logs",
    };
  }

  function renderTodayLoggedFoods(data) {
    const box = $("today-logged-foods");
    const today = civilDay((data.meta && data.meta.local_today) || "");
    const store = (data && data.nutrition_store) || {};
    let logs = store.food_logs_today || [];
    if (today) {
      const dated = logs.filter((f) => civilDay(f.date) === today);
      if (dated.length) logs = dated;
    }
    if (!box) return logs;
    if (!logs.length) {
      box.innerHTML = "";
      return logs;
    }
    box.innerHTML =
      `<div class="today-subh" style="font-size:0.8rem;margin-bottom:0.3rem">Logged today</div>` +
      `<ul style="margin:0;padding-left:1.1rem">` +
      logs
        .map((f) => {
          const kcal = f.calories != null ? `${fmtNum(f.calories)} kcal` : "— kcal";
          return `<li><strong>${f.name || "Food"}</strong> · ${kcal}</li>`;
        })
        .join("") +
      `</ul>`;
    return logs;
  }

  function fmtNum(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return Math.round(n).toLocaleString();
  }

  window.__fitdashMealSnapshot = {
    civilDay,
    hasLoggedMacros,
    lastNutritionDate,
    loggedConsumedForToday,
    renderTodayLoggedFoods,
  };
})();
