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
  const COLLAPSE_STORAGE_KEY = "fitdash-collapse-v5";
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
      logs.map((f) => {
        const kcal = f.calories != null ? `${fmtNum(f.calories)} kcal` : "— kcal";
        return `<li><strong>${f.name || "Food"}</strong> · ${kcal}</li>`;
      }).join("") +
      `</ul>`;
    return logs;
  }
  function loggedConsumedForToday(data) {
    const today = civilDay((data.meta && data.meta.local_today) || "");
    const store = (data && data.nutrition_store) || {};
    const coachCons = ((((data.coach || {}).today || {}).nutrition || {}).consumed) || null;
    const storeCons = store.today_consumed || null;
    const matchDay = (c) => hasLoggedMacros(c) && (!today || !c.date || civilDay(c.date) === today);
    if (matchDay(storeCons)) return storeCons;
    if (matchDay(coachCons)) return coachCons;
    return null;
  }
})();
