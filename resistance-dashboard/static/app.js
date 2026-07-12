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

  function todayISO() {
    // Browser local civil date (matches host TZ when you open dashboard on this Mac).
    const d = new Date();
    const z = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
  }

  /** Keep charts snappy on long series (e.g. 90d). */
  function downsamplePoints(points, maxPoints = 45) {
    if (!Array.isArray(points) || points.length <= maxPoints) return points || [];
    const out = [];
    const n = points.length;
    const step = (n - 1) / (maxPoints - 1);
    for (let i = 0; i < maxPoints; i++) {
      out.push(points[Math.round(i * step)]);
    }
    // ensure last point is exact end
    out[out.length - 1] = points[n - 1];
    return out;
  }

  function fmtNum(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return Math.round(n).toLocaleString();
  }

  function recoveryClass(label) {
    const l = (label || "").toLowerCase();
    if (l === "ready") return "ready";
    if (l === "moderate") return "moderate";
    if (l === "caution") return "caution";
    return "needs-rest";
  }

  function showAlert(msg, kind = "warn") {
    const box = $("alerts");
    const el = document.createElement("div");
    el.className = `alert ${kind}`;
    el.textContent = msg;
    box.appendChild(el);
  }

  function clearAlerts() {
    $("alerts").innerHTML = "";
  }

  function addExerciseRow(prefill = {}) {
    const wrap = $("exercise-rows");
    const row = document.createElement("div");
    row.className = "exercise-row";
    row.innerHTML = `
      <label>Exercise
        <input type="text" class="ex-name" required placeholder="e.g. DB Flat Press" value="${prefill.name || ""}" />
      </label>
      <label>Weight (lbs)
        <input type="number" class="ex-weight" required min="0" step="0.5" value="${prefill.weight_lbs ?? ""}" />
      </label>
      <label>Sets
        <input type="number" class="ex-sets" required min="1" step="1" value="${prefill.sets ?? 3}" />
      </label>
      <label>Reps
        <input type="number" class="ex-reps" required min="1" step="1" value="${prefill.reps ?? 10}" />
      </label>
      <button type="button" class="ex-remove" aria-label="Remove">✕</button>
    `;
    row.querySelector(".ex-remove").addEventListener("click", () => {
      if ($("exercise-rows").children.length > 1) row.remove();
    });
    wrap.appendChild(row);
  }

  function collectExercises() {
    return [...$("exercise-rows").querySelectorAll(".exercise-row")].map((row) => ({
      name: row.querySelector(".ex-name").value.trim(),
      weight_lbs: Number(row.querySelector(".ex-weight").value),
      sets: Number(row.querySelector(".ex-sets").value),
      reps: Number(row.querySelector(".ex-reps").value),
    }));
  }

  function destroyChart(c) {
    if (c) c.destroy();
  }

  function chartDefaults() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: "#8b9bb4" } },
      },
      scales: {
        x: {
          ticks: { color: "#8b9bb4", maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
          grid: { color: "rgba(45,58,79,0.6)" },
        },
        y: {
          ticks: { color: "#8b9bb4" },
          grid: { color: "rgba(45,58,79,0.6)" },
        },
      },
    };
  }

  /** Linear regression y = a + b*x over finite values; returns series aligned to values. */
  function linearTrend(values) {
    const pts = [];
    values.forEach((v, i) => {
      if (v != null && !Number.isNaN(Number(v))) pts.push({ x: i, y: Number(v) });
    });
    if (pts.length < 2) return values.map(() => null);
    const n = pts.length;
    const meanX = pts.reduce((s, p) => s + p.x, 0) / n;
    const meanY = pts.reduce((s, p) => s + p.y, 0) / n;
    let num = 0;
    let den = 0;
    pts.forEach((p) => {
      num += (p.x - meanX) * (p.y - meanY);
      den += (p.x - meanX) ** 2;
    });
    if (den === 0) return values.map(() => null);
    const b = num / den;
    const a = meanY - b * meanX;
    return values.map((_, i) => a + b * i);
  }

  /** Rolling mean over the last `window` finite points (trailing). */
  function rollingAverage(values, window) {
    const out = [];
    for (let i = 0; i < values.length; i++) {
      const slice = [];
      for (let j = Math.max(0, i - window + 1); j <= i; j++) {
        const v = values[j];
        if (v != null && !Number.isNaN(Number(v))) slice.push(Number(v));
      }
      out.push(slice.length ? slice.reduce((s, x) => s + x, 0) / slice.length : null);
    }
    return out;
  }

  function trendSlopePerDay(values) {
    const pts = [];
    values.forEach((v, i) => {
      if (v != null && !Number.isNaN(Number(v))) pts.push({ x: i, y: Number(v) });
    });
    if (pts.length < 2) return null;
    const n = pts.length;
    const meanX = pts.reduce((s, p) => s + p.x, 0) / n;
    const meanY = pts.reduce((s, p) => s + p.y, 0) / n;
    let num = 0;
    let den = 0;
    pts.forEach((p) => {
      num += (p.x - meanX) * (p.y - meanY);
      den += (p.x - meanX) ** 2;
    });
    if (den === 0) return null;
    return num / den;
  }

  function renderCharts(data) {
    const vol = data.volume_by_week || [];
    destroyChart(volumeChart);
    volumeChart = new Chart($("chart-volume"), {
      type: "bar",
      data: {
        labels: vol.map((v) => v.week_start),
        datasets: [
          {
            label: "Weekly volume (lb)",
            data: vol.map((v) => v.volume),
            backgroundColor: "rgba(61,156,240,0.55)",
            borderRadius: 6,
          },
        ],
      },
      options: chartDefaults(),
    });

    const exercises = data.top_exercises || [];
    if (!selectedExercise || !exercises.includes(selectedExercise)) {
      selectedExercise = exercises[0] || null;
    }
    const tabs = $("exercise-tabs");
    tabs.innerHTML = "";
    exercises.slice(0, 8).forEach((name) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = name;
      if (name === selectedExercise) b.classList.add("active");
      b.addEventListener("click", () => {
        selectedExercise = name;
        renderStrength(data);
        renderCharts(data);
      });
      tabs.appendChild(b);
    });
    renderStrength(data);

    const weights = downsamplePoints(
      [...((data.health && data.health.weight) || [])].sort((a, b) =>
        String(a.date).localeCompare(String(b.date))
      ),
      45
    );
    const weightVals = weights.map((w) => w.weight_lbs);
    const weightTrend = linearTrend(weightVals);
    const wSlope = trendSlopePerDay(weightVals);
    destroyChart(weightChart);
    weightChart = new Chart($("chart-weight"), {
      type: "line",
      data: {
        labels: weights.map((w) => w.date),
        datasets: [
          {
            label: "Weight (lb)",
            data: weightVals,
            borderColor: "#5ce1a8",
            backgroundColor: "rgba(92,225,168,0.12)",
            tension: 0.25,
            fill: true,
            pointRadius: 2,
            order: 2,
          },
          {
            label: "Trend",
            data: weightTrend,
            borderColor: "#3d9cf0",
            borderDash: [6, 4],
            borderWidth: 2,
            pointRadius: 0,
            fill: false,
            tension: 0,
            order: 1,
          },
        ],
      },
      options: chartDefaults(),
    });
    if ($("weight-trend-note")) {
      if (wSlope == null || weights.length < 2) {
        $("weight-trend-note").textContent = "Need more weigh-ins for a trend.";
      } else {
        const perWeek = wSlope * 7;
        const dir = perWeek > 0.05 ? "up" : perWeek < -0.05 ? "down" : "flat";
        $("weight-trend-note").textContent = `90d series · linear trend ${dir} (~${perWeek >= 0 ? "+" : ""}${perWeek.toFixed(2)} lb/week) · ${weights.length} points`;
      }
    }

    const sleep = downsamplePoints(
      [...((data.health && data.health.sleep) || [])].sort((a, b) =>
        String(a.date).localeCompare(String(b.date))
      ),
      45
    );
    const sleepVals = sleep.map((s) => s.sleep_hours);
    const sleepTrend = linearTrend(sleepVals);
    const sleepRoll7 = rollingAverage(sleepVals, 7);
    const sleepTarget = sleepVals.map(() => 8);
    const sSlope = trendSlopePerDay(sleepVals);
    const lastRoll =
      [...sleepRoll7].reverse().find((v) => v != null && !Number.isNaN(v)) ?? null;
    destroyChart(sleepChart);
    sleepChart = new Chart($("chart-sleep"), {
      type: "bar",
      data: {
        labels: sleep.map((s) => s.date),
        datasets: [
          {
            type: "bar",
            label: "Sleep (h)",
            data: sleepVals,
            backgroundColor: "rgba(240,180,41,0.45)",
            borderRadius: 4,
            order: 3,
          },
          {
            type: "line",
            label: "7d rolling avg",
            data: sleepRoll7,
            borderColor: "#3d9cf0",
            borderWidth: 2.5,
            pointRadius: 0,
            tension: 0.25,
            spanGaps: true,
            order: 1,
          },
          {
            type: "line",
            label: "Trend",
            data: sleepTrend,
            borderColor: "#f07178",
            borderDash: [6, 4],
            borderWidth: 2,
            pointRadius: 0,
            tension: 0,
            order: 2,
          },
          {
            type: "line",
            label: "8h goal",
            data: sleepTarget,
            borderColor: "rgba(92,225,168,0.85)",
            borderDash: [2, 4],
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0,
            order: 0,
          },
        ],
      },
      options: {
        ...chartDefaults(),
        scales: {
          ...chartDefaults().scales,
          y: {
            ...chartDefaults().scales.y,
            suggestedMin: 4,
            suggestedMax: 10,
          },
        },
      },
    });
    if ($("sleep-trend-note")) {
      if (lastRoll == null) {
        $("sleep-trend-note").textContent = "Need sleep nights for rolling average.";
      } else {
        const vsGoal = lastRoll >= 8 ? "at/above 8h goal" : "below 8h goal";
        const slopeTxt =
          sSlope == null
            ? ""
            : ` · trend ${sSlope >= 0 ? "+" : ""}${(sSlope * 7).toFixed(2)} h/week`;
        $("sleep-trend-note").textContent = `Latest 7d avg: ${lastRoll.toFixed(2)} h (${vsGoal})${slopeTxt} · ${sleep.length} nights`;
      }
    }

    const nutrition = downsamplePoints(
      [...((data.health && data.health.nutrition) || [])].sort((a, b) =>
        String(a.date).localeCompare(String(b.date))
      ),
      45
    );
    const burned = downsamplePoints(
      [...((data.health && data.health.calories_burned) || [])].sort((a, b) =>
        String(a.date).localeCompare(String(b.date))
      ),
      45
    );
    const hydration = downsamplePoints(
      [...((data.health && data.health.hydration) || [])].sort((a, b) =>
        String(a.date).localeCompare(String(b.date))
      ),
      45
    );
    const calLabels = [
      ...new Set([
        ...nutrition.map((n) => n.date),
        ...burned.map((b) => b.date),
      ]),
    ].sort();
    const intakeByDate = Object.fromEntries(
      nutrition.map((n) => [n.date, n.calories])
    );
    const burnedByDate = Object.fromEntries(
      burned.map((b) => [b.date, b.calories])
    );
    destroyChart(caloriesChart);
    if ($("chart-calories")) {
      caloriesChart = new Chart($("chart-calories"), {
        type: "line",
        data: {
          labels: calLabels,
          datasets: [
            {
              label: "Intake (kcal)",
              data: calLabels.map((d) => intakeByDate[d] ?? null),
              borderColor: "#5ce1a8",
              tension: 0.25,
              spanGaps: true,
              pointRadius: 3,
            },
            {
              label: "Burned (kcal)",
              data: calLabels.map((d) => burnedByDate[d] ?? null),
              borderColor: "#f07178",
              tension: 0.25,
              spanGaps: true,
              pointRadius: 3,
            },
          ],
        },
        options: chartDefaults(),
      });
    }

    destroyChart(macrosChart);
    if ($("chart-macros")) {
      // Chronological order; only days that have at least one macro value.
      const macroDays = downsamplePoints(
        [...nutrition]
          .filter(
            (n) =>
              n &&
              (n.protein_g != null || n.carbs_g != null || n.fat_g != null)
          )
          .sort((a, b) => String(a.date).localeCompare(String(b.date))),
        45
      );
      macrosChart = new Chart($("chart-macros"), {
        type: "bar",
        data: {
          labels: macroDays.map((n) => n.date),
          datasets: [
            {
              label: "Protein (g)",
              data: macroDays.map((n) => n.protein_g),
              backgroundColor: "rgba(61,156,240,0.65)",
              borderRadius: 4,
            },
            {
              label: "Carbs (g)",
              data: macroDays.map((n) => n.carbs_g),
              backgroundColor: "rgba(240,180,41,0.65)",
              borderRadius: 4,
            },
            {
              label: "Fat (g)",
              data: macroDays.map((n) => n.fat_g),
              backgroundColor: "rgba(240,113,120,0.55)",
              borderRadius: 4,
            },
          ],
        },
        options: chartDefaults(),
      });
    }

    destroyChart(hydrationChart);
    if ($("chart-hydration")) {
      hydrationChart = new Chart($("chart-hydration"), {
        type: "bar",
        data: {
          labels: hydration.map((h) => h.date),
          datasets: [
            {
              label: "Water (ml)",
              data: hydration.map((h) => h.water_ml),
              backgroundColor: "rgba(92,225,168,0.45)",
              borderRadius: 6,
            },
          ],
        },
        options: chartDefaults(),
      });
    }
  }

  function renderStrength(data) {
    const series =
      (selectedExercise && data.strength_trends && data.strength_trends[selectedExercise]) ||
      [];
    destroyChart(strengthChart);
    strengthChart = new Chart($("chart-strength"), {
      type: "line",
      data: {
        labels: series.map((p) => p.date),
        datasets: [
          {
            label: `${selectedExercise || "Exercise"} best load (lb)`,
            data: series.map((p) => p.best_working_weight),
            borderColor: "#3d9cf0",
            tension: 0.25,
            pointRadius: 3,
          },
          {
            label: "Est. 1RM (Epley)",
            data: series.map((p) => p.best_e1rm),
            borderColor: "#f0b429",
            borderDash: [5, 5],
            tension: 0.25,
            pointRadius: 2,
          },
        ],
      },
      options: chartDefaults(),
    });
  }

  function renderHistory(sessions) {
    const list = $("session-list");
    list.innerHTML = "";
    (sessions || []).slice(0, 40).forEach((s) => {
      const li = document.createElement("li");
      const exPreview = (s.exercises || [])
        .slice(0, 4)
        .map((e) => `${e.name} (${Math.round(e.volume)} vol)`)
        .join(" · ");
      li.innerHTML = `
        <div class="title">${s.session_type.toUpperCase()} · ${s.date}</div>
        <div class="meta">Volume ${fmtNum(s.volume)} lb · ${s.exercises.length} exercises</div>
        <div class="ex">${exPreview || "No parsed sets"}</div>
      `;
      list.appendChild(li);
    });
  }

  function renderInventory(store) {
    const list = $("inventory-list");
    if (!list) return;
    list.innerHTML = "";
    const items = ((store && store.inventory && store.inventory.ingredients) || []).slice();
    items.sort((a, b) => String(a.name).localeCompare(String(b.name)));
    if (!items.length) {
      list.innerHTML = `<li class="muted">No ingredients yet — add some above.</li>`;
      return;
    }
    items.forEach((ing) => {
      const li = document.createElement("li");
      const stock = ing.in_stock !== false;
      li.innerHTML = `
        <div class="title">${ing.name} ${stock ? "" : "<span class='muted'>(out)</span>"}</div>
        <div class="meta">${ing.category || "other"} · ${ing.serving_label || "1 serving"} ·
          ${fmtNum(ing.calories)} kcal · P${fmtNum(ing.protein_g)} C${fmtNum(ing.carbs_g)} F${fmtNum(ing.fat_g)}</div>
        <div class="actions" style="margin-top:0.35rem">
          <button type="button" class="btn-stock" data-id="${ing.id}" data-stock="${stock ? "0" : "1"}">
            ${stock ? "Mark out of stock" : "Mark in stock"}
          </button>
          <button type="button" class="btn-remove" data-id="${ing.id}">Remove</button>
        </div>
      `;
      list.appendChild(li);
    });
    list.querySelectorAll(".btn-remove").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        try {
          const res = await fetch("/api/inventory/remove", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id }),
          });
          const data = await res.json();
          if (!res.ok || !data.ok) throw new Error(data.error || res.status);
          showAlert(`Removed ${id}`, "ok");
          await loadDashboard();
        } catch (e) {
          showAlert(`Remove failed: ${e.message}`, "err");
        }
      });
    });
    list.querySelectorAll(".btn-stock").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        const in_stock = btn.getAttribute("data-stock") === "1";
        try {
          const res = await fetch("/api/inventory/stock", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id, in_stock }),
          });
          const data = await res.json();
          if (!res.ok || !data.ok) throw new Error(data.error || res.status);
          await loadDashboard();
        } catch (e) {
          showAlert(`Stock update failed: ${e.message}`, "err");
        }
      });
    });
  }

  function renderTargetsAndRemaining(store) {
    const t = (store && store.targets) || {};
    const c = (store && store.today_consumed) || {};
    if ($("tgt-cal")) {
      $("tgt-cal").value = t.calories ?? 2100;
      $("tgt-p").value = t.protein_g ?? 210;
      $("tgt-c").value = t.carbs_g ?? 180;
      $("tgt-f").value = t.fat_g ?? 55;
    }
    const rem = {
      calories: Math.max(0, (t.calories || 0) - (c.calories || 0)),
      protein_g: Math.max(0, (t.protein_g || 0) - (c.protein_g || 0)),
      carbs_g: Math.max(0, (t.carbs_g || 0) - (c.carbs_g || 0)),
      fat_g: Math.max(0, (t.fat_g || 0) - (c.fat_g || 0)),
    };
    if ($("remaining-macros")) {
      $("remaining-macros").innerHTML = `
        <strong>Today so far</strong> (local day ${c.date || "today"}, Google Health):
        ${fmtNum(c.calories)} kcal · P${fmtNum(c.protein_g)} C${fmtNum(c.carbs_g)} F${fmtNum(c.fat_g)}
        <br/>
        <strong>Remaining to target</strong>:
        ${fmtNum(rem.calories)} kcal · P${fmtNum(rem.protein_g)} C${fmtNum(rem.carbs_g)} F${fmtNum(rem.fat_g)}
      `;
    }
  }

  function renderExerciseCatalog(store) {
    const list = $("exercise-catalog-list");
    if (!list) return;
    list.innerHTML = "";
    const items = ((store && store.catalog && store.catalog.exercises) || []).slice();
    items.sort((a, b) => String(a.name).localeCompare(String(b.name)));
    if (!items.length) {
      list.innerHTML = `<li class="muted">No exercises in catalog yet.</li>`;
      return;
    }
    items.forEach((ex) => {
      const li = document.createElement("li");
      const avail = ex.available !== false;
      const muscles = (ex.primary_muscles || []).join(", ") || "—";
      const sessions = (ex.session_types || []).join("/");
      li.innerHTML = `
        <div class="title">${ex.name} ${avail ? "" : "<span class='muted'>(off)</span>"}</div>
        <div class="meta">${sessions || "?"} · ${ex.movement || "compound"} · ${muscles}
          · ${ex.default_sets || 3}×${ex.default_reps || 10}</div>
        <div class="actions" style="margin-top:0.35rem">
          <button type="button" class="btn-ex-avail" data-id="${ex.id}" data-avail="${avail ? "0" : "1"}">
            ${avail ? "Disable in plans" : "Enable in plans"}
          </button>
        </div>
      `;
      list.appendChild(li);
    });
    list.querySelectorAll(".btn-ex-avail").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        const available = btn.getAttribute("data-avail") === "1";
        try {
          const res = await fetch("/api/workout/exercise/available", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id, available }),
          });
          const data = await res.json();
          if (!res.ok || !data.ok) throw new Error(data.error || res.status);
          showAlert(`Exercise ${available ? "enabled" : "disabled"}`, "ok");
          await loadDashboard(false);
        } catch (e) {
          showAlert(`Update failed: ${e.message}`, "err");
        }
      });
    });
  }

  function renderWorkoutGoals(store) {
    const g = (store && store.goals) || {};
    if ($("wg-count")) $("wg-count").value = g.exercises_per_session ?? 5;
    if ($("wg-rest")) $("wg-rest").value = g.rest_if_recovery_below ?? 40;
    if ($("wg-split")) $("wg-split").value = g.split || "ppl";
  }

  function renderWorkoutPlan(plan) {
    const box = $("workout-plan-result");
    if (!box) return;
    if (!plan) {
      box.innerHTML = "";
      return;
    }
    let html = `<p class="muted">${plan.message || ""}</p>`;
    if (plan.is_rest_day) {
      html += `<p><strong>Rest day</strong> — recovery below threshold.</p>`;
      box.innerHTML = html;
      return;
    }
    const st = (plan.session_type || "").toUpperCase();
    html += `<p><strong>${st || "Session"}</strong> · ${(plan.exercises || []).length} lifts</p>`;
    const items = plan.exercises || [];
    if (!items.length) {
      html += `<p class="muted">No exercises planned — expand the catalog or enable exercises.</p>`;
    } else {
      html += `<ul class="session-list" style="margin-top:0.5rem">`;
      items.forEach((ex) => {
        const rx = ex.prescription || {};
        const w =
          rx.weight_lbs != null && rx.weight_lbs !== ""
            ? `${rx.weight_lbs} lb`
            : "BW / choose load";
        const muscles = (ex.primary_muscles || []).join(", ");
        const last = ex.last
          ? `Last ${ex.last.date}: ${ex.last.weight_lbs} lb × ${ex.last.sets}×${ex.last.reps}`
          : "No prior log";
        html += `<li>
          <div class="title">${ex.name}</div>
          <div class="meta"><strong>${w} × ${rx.sets || "?"} × ${rx.reps || "?"}</strong>
            · ${ex.movement || ""} · ${muscles}</div>
          <div class="meta muted" style="font-size:0.85rem">${ex.rationale || last}</div>
        </li>`;
      });
      html += `</ul>`;
    }
    const ctx = plan.context || {};
    if (ctx.last_session_type != null || ctx.days_since_last != null) {
      html += `<p class="muted" style="margin-top:0.75rem;font-size:0.85rem">
        Context: last=${ctx.last_session_type || "—"} · days since log=${ctx.days_since_last ?? "—"}
        · catalog pool=${ctx.pool_for_session ?? "—"}
      </p>`;
    }
    box.innerHTML = html;
  }

  function renderMealPlan(plan) {
    const box = $("meal-plan-result");
    if (!box) return;
    if (!plan) {
      box.innerHTML = "";
      return;
    }
    const meals = plan.meals || [];
    let html = `<p class="muted">${plan.message || ""}</p>`;
    html += `<p><strong>Planned add</strong>: ${fmtNum(plan.planned_totals?.calories)} kcal ·
      P${fmtNum(plan.planned_totals?.protein_g)} C${fmtNum(plan.planned_totals?.carbs_g)} F${fmtNum(plan.planned_totals?.fat_g)}</p>`;
    html += `<p class="muted">After plan remaining: ${fmtNum(plan.remaining_after_plan?.calories)} kcal ·
      P${fmtNum(plan.remaining_after_plan?.protein_g)}</p>`;
    if (!meals.length) {
      html += `<p class="muted">No items planned.</p>`;
    } else {
      meals.forEach((m) => {
        html += `<div style="margin-top:0.6rem;border-top:1px solid var(--border);padding-top:0.5rem">
          <div class="title">${m.label}</div>
          <div class="meta">${fmtNum(m.totals?.calories)} kcal · P${fmtNum(m.totals?.protein_g)}</div>
          <ul style="margin:0.3rem 0 0;padding-left:1.1rem">`;
        (m.items || []).forEach((it) => {
          html += `<li>${it.name} · ${it.serving_label} · ${fmtNum(it.calories)} kcal · P${fmtNum(it.protein_g)}</li>`;
        });
        html += `</ul></div>`;
      });
    }
    box.innerHTML = html;
  }

  function render(data) {
    state = data;
    clearAlerts();
    $("stat-sessions").textContent = data.session_count ?? "—";
    $("stat-volume").textContent = fmtNum(data.total_volume);
    const latestW =
      data.health && data.health.weight && data.health.weight.length
        ? data.health.weight[data.health.weight.length - 1].weight_lbs
        : null;
    $("stat-weight").textContent = latestW != null ? `${latestW.toFixed(1)} lb` : "—";

    const rec = data.recovery || {};
    $("stat-recovery").textContent = rec.label || "—";
    $("recovery-badge").innerHTML = `<span class="badge ${recoveryClass(rec.label)}">${rec.label || "—"} · ${rec.score ?? "—"}</span>`;
    const reasons = $("recovery-reasons");
    reasons.innerHTML = "";
    (rec.reasons || []).forEach((r) => {
      const li = document.createElement("li");
      li.textContent = r;
      reasons.appendChild(li);
    });

    const meta = data.meta || {};
    const loadMs = meta.load_ms != null ? ` · ${meta.load_ms}ms` : "";
    const cache = meta.cache || {};
    const hCache = cache.health || {};
    const gCache = cache.github || {};
    const cacheBits = [];
    if (hCache.used_cache) {
      const age = hCache.age_sec != null ? `${Math.round(hCache.age_sec / 60)}m old` : "cached";
      cacheBits.push(`health ${age}`);
    } else if (hCache.refreshed) {
      cacheBits.push("health refreshed");
    }
    if (gCache.used_cache) {
      const age = gCache.age_sec != null ? `${Math.round(gCache.age_sec / 60)}m old` : "cached";
      cacheBits.push(`github ${age}`);
    } else if (gCache.refreshed) {
      cacheBits.push("github refreshed");
    }
    const ttlMin = meta.cache_ttl_sec ? Math.round(meta.cache_ttl_sec / 60) : 60;
    const cacheNote = cacheBits.length
      ? ` · cache: ${cacheBits.join(", ")} (ttl ${ttlMin}m; Refresh forces pull)`
      : ` · cache ttl ${ttlMin}m`;
    const tzBit = meta.timezone ? ` · tz ${meta.timezone}` : "";
    const todayBit = meta.local_today ? ` · today ${meta.local_today}` : "";
    $("meta-line").textContent =
      `source=${meta.source || "?"} · generated ${meta.generated_at || ""}${loadMs}${cacheNote}${todayBit}${tzBit}`;

    const nutrition = (data.health && data.health.nutrition) || [];
    const latestN = nutrition.length ? nutrition[nutrition.length - 1] : null;
    if ($("stat-calories")) {
      $("stat-calories").textContent =
        latestN && latestN.calories != null ? fmtNum(latestN.calories) : "—";
      $("stat-protein").textContent =
        latestN && latestN.protein_g != null ? `${fmtNum(latestN.protein_g)} g` : "—";
      $("stat-carbs").textContent =
        latestN && latestN.carbs_g != null ? `${fmtNum(latestN.carbs_g)} g` : "—";
      $("stat-fat").textContent =
        latestN && latestN.fat_g != null ? `${fmtNum(latestN.fat_g)} g` : "—";
    }

    if (data.health && data.health.error) {
      showAlert(`Google Health: ${data.health.error}`, "warn");
      $("health-note").textContent =
        "Some Google Health streams failed or need extra OAuth scopes (nutrition / activity). Recovery still uses available data.";
    } else if (!(data.health && data.health.weight && data.health.weight.length)) {
      $("health-note").textContent = "No weight samples returned for the recent window.";
    } else {
      $("health-note").textContent = `Google Health connected · ${data.health.weight.length} weight pts, ${(data.health.sleep || []).length} sleep nights.`;
    }
    if ($("nutrition-note")) {
      const n = nutrition.length;
      const h = ((data.health && data.health.hydration) || []).length;
      const b = ((data.health && data.health.calories_burned) || []).length;
      if (!n && !h && !b) {
        $("nutrition-note").textContent =
          "No nutrition/hydration yet — re-connect Google Health to grant nutrition + activity scopes, and log food/water in Fitbit/Google Health.";
      } else {
        $("nutrition-note").textContent = `Nutrition days: ${n} · hydration days: ${h} · burned-calorie days: ${b}`;
      }
    }

    if (meta.error) {
      showAlert(`Lift source note: ${meta.error}`, "warn");
    }

    renderCharts(data);
    renderHistory(data.sessions || []);
    renderInventory(data.nutrition_store);
    renderTargetsAndRemaining(data.nutrition_store);
    // Auto meal plan is computed server-side on every dashboard load
    if (data.nutrition_store && data.nutrition_store.meal_plan) {
      renderMealPlan(data.nutrition_store.meal_plan);
    }
    renderExerciseCatalog(data.workout_store);
    renderWorkoutGoals(data.workout_store);
    if (data.workout_store && data.workout_store.plan) {
      renderWorkoutPlan(data.workout_store.plan);
    }
  }

  async function loadDashboard(forceRefresh = false) {
    $("btn-refresh").disabled = true;
    clearAlerts();
    const meta = $("meta-line");
    const started = Date.now();
    if (meta) {
      meta.textContent = forceRefresh
        ? "Refreshing Google Health + GitHub (forced)…"
        : "Loading dashboard (local + cache)…";
    }
    const tick = setInterval(() => {
      if (!meta) return;
      const sec = Math.round((Date.now() - started) / 1000);
      meta.textContent = forceRefresh
        ? `Refreshing remotes… ${sec}s`
        : `Loading… ${sec}s (uses 1h cache for Health/GitHub)`;
    }, 500);
    try {
      const url = forceRefresh ? "/api/dashboard?refresh=1" : "/api/dashboard";
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      // Soft errors (partial data) live under meta.error — still render.
      if (data.error && !data.sessions && !data.meta) {
        throw new Error(data.error);
      }
      render(data);
      if (data.meta && data.meta.error) {
        showAlert(`Partial load: ${data.meta.error}`, "warn");
      }
    } catch (e) {
      clearAlerts();
      showAlert(`Failed to load dashboard: ${e.message}`, "err");
      if (meta) meta.textContent = `Load failed: ${e.message}`;
    } finally {
      clearInterval(tick);
      $("btn-refresh").disabled = false;
    }
  }

  async function submitWorkout(ev) {
    ev.preventDefault();
    const status = $("log-status");
    status.textContent = "Saving…";
    $("btn-save").disabled = true;
    const body = {
      session_type: $("session_type").value,
      date: $("log-date").value,
      notes: $("log-notes").value,
      exercises: collectExercises(),
    };
    try {
      const res = await fetch("/api/workouts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      status.textContent = `Saved to ${data.write.path} · verified=${data.write.verified_on_readback}`;
      showAlert("Workout logged and re-read successfully.", "ok");
      await loadDashboard();
    } catch (e) {
      status.textContent = "";
      showAlert(`Log failed: ${e.message}`, "err");
    } finally {
      $("btn-save").disabled = false;
    }
  }

  async function submitIngredient(ev) {
    ev.preventDefault();
    const status = $("ing-status");
    if (status) status.textContent = "Saving…";
    const body = {
      name: $("ing-name").value.trim(),
      category: $("ing-category").value,
      serving_label: $("ing-serving").value.trim() || "1 serving",
      calories: Number($("ing-cal").value),
      protein_g: Number($("ing-p").value),
      carbs_g: Number($("ing-c").value),
      fat_g: Number($("ing-f").value),
      in_stock: true,
    };
    try {
      const res = await fetch("/api/inventory/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      if (status) status.textContent = `Saved ${body.name}`;
      showAlert(`Inventory updated: ${body.name}`, "ok");
      $("ing-name").value = "";
      await loadDashboard();
    } catch (e) {
      if (status) status.textContent = "";
      showAlert(`Inventory save failed: ${e.message}`, "err");
    }
  }

  async function submitTargets(ev) {
    ev.preventDefault();
    const status = $("targets-status");
    if (status) status.textContent = "Saving…";
    const body = {
      calories: Number($("tgt-cal").value),
      protein_g: Number($("tgt-p").value),
      carbs_g: Number($("tgt-c").value),
      fat_g: Number($("tgt-f").value),
    };
    try {
      const res = await fetch("/api/targets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      if (status) status.textContent = "Targets saved";
      showAlert("Daily targets saved", "ok");
      await loadDashboard();
    } catch (e) {
      if (status) status.textContent = "";
      showAlert(`Targets save failed: ${e.message}`, "err");
    }
  }

  async function generatePlan() {
    const btn = $("btn-generate-plan");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch("/api/meal-plan/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      renderMealPlan(data.plan);
      showAlert("Rest-of-day meal plan generated", "ok");
    } catch (e) {
      showAlert(`Meal plan failed: ${e.message}`, "err");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function generateWorkoutPlan(sessionType) {
    const btn = $("btn-generate-workout");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch("/api/workout-plan/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sessionType ? { session_type: sessionType } : {}),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      renderWorkoutPlan(data.plan);
      showAlert(
        data.plan && data.plan.is_rest_day
          ? "Rest day suggested"
          : `Workout plan: ${(data.plan && data.plan.session_type) || "session"}`,
        "ok"
      );
    } catch (e) {
      showAlert(`Workout plan failed: ${e.message}`, "err");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function submitExerciseCatalog(ev) {
    ev.preventDefault();
    const status = $("excat-status");
    const body = {
      name: $("excat-name").value.trim(),
      session_types: [$("excat-session").value],
      primary_muscles: ($("excat-muscle").value || "other")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      movement: $("excat-movement").value,
      default_sets: Number($("excat-sets").value) || 3,
      default_reps: Number($("excat-reps").value) || 10,
      available: true,
    };
    try {
      const res = await fetch("/api/workout/exercise", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      if (status) status.textContent = "Saved";
      showAlert(`Exercise saved: ${body.name}`, "ok");
      $("excat-name").value = "";
      await loadDashboard(false);
    } catch (e) {
      if (status) status.textContent = "";
      showAlert(`Exercise save failed: ${e.message}`, "err");
    }
  }

  async function submitWorkoutGoals(ev) {
    ev.preventDefault();
    const status = $("workout-goals-status");
    const body = {
      split: $("wg-split").value,
      exercises_per_session: Number($("wg-count").value) || 5,
      rest_if_recovery_below: Number($("wg-rest").value) || 40,
      rotation: ["push", "pull", "legs"],
      prefer_compounds_first: true,
      progression: "double_progression",
    };
    try {
      const res = await fetch("/api/workout/goals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      if (status) status.textContent = "Goals saved";
      showAlert("Training goals saved", "ok");
      await loadDashboard(false);
    } catch (e) {
      if (status) status.textContent = "";
      showAlert(`Goals save failed: ${e.message}`, "err");
    }
  }

  /** @type {{role: string, content: string}[]} */
  let askHistory = [];

  function renderAskMessages() {
    const box = $("ask-messages");
    if (!box) return;
    box.innerHTML = "";
    for (const turn of askHistory) {
      const el = document.createElement("div");
      el.className = `ask-msg ${turn.role === "user" ? "user" : "assistant"}`;
      const role = document.createElement("span");
      role.className = "ask-role";
      role.textContent = turn.role === "user" ? "You" : "Grok";
      const body = document.createElement("div");
      body.textContent = turn.content;
      el.appendChild(role);
      el.appendChild(body);
      box.appendChild(el);
    }
    box.scrollTop = box.scrollHeight;
  }

  async function refreshAskAuthStatus() {
    const el = $("ask-auth-status");
    if (!el) return;
    try {
      const res = await fetch("/api/ask/status");
      const data = await res.json();
      if (data.ok) {
        const src =
          data.source === "supergrok_session"
            ? "SuperGrok session"
            : data.source === "xai_api_key"
              ? "XAI_API_KEY"
              : data.source || "auth";
        const who = data.email ? ` (${data.email})` : "";
        el.textContent = `Ready · ${src}${who} · model ${data.model || "default"}`;
      } else {
        el.textContent = data.error || "Grok auth not ready — run `grok login`";
      }
    } catch (e) {
      el.textContent = `Could not check Grok auth: ${e.message}`;
    }
  }

  async function submitAsk(ev) {
    ev.preventDefault();
    const input = $("ask-question");
    const status = $("ask-status");
    const btn = $("btn-ask");
    const box = $("ask-messages");
    const question = (input && input.value || "").trim();
    if (!question) return;

    askHistory.push({ role: "user", content: question });
    renderAskMessages();
    if (box) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    if (input) input.value = "";
    if (btn) btn.disabled = true;

    const started = Date.now();
    const tick = setInterval(() => {
      if (!status) return;
      const sec = Math.round((Date.now() - started) / 1000);
      status.textContent =
        `Thinking… ${sec}s — loading your fitness data, then Grok. ` +
        `Reply appears in the chat box above (usually 30–90s; up to ~2 min).`;
    }, 500);
    if (status) {
      status.textContent =
        "Thinking… 0s — loading your fitness data, then Grok. Reply appears in the chat box above.";
    }

    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          history: askHistory.slice(0, -1).slice(-8),
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      askHistory.push({ role: "assistant", content: data.answer || "(empty reply)" });
      renderAskMessages();
      if (box) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
      const sec = Math.round((Date.now() - started) / 1000);
      const bits = [`${sec}s`];
      if (data.model) bits.push(data.model);
      if (data.auth_source) bits.push(data.auth_source);
      if (data.usage && data.usage.total_tokens != null) {
        bits.push(`${data.usage.total_tokens} tokens`);
      }
      if (status) status.textContent = "Done · " + bits.join(" · ");
    } catch (e) {
      askHistory.push({
        role: "assistant",
        content: `Error: ${e.message}\n\nIf the SuperGrok session expired, run \`grok login\` in a terminal and retry.`,
      });
      renderAskMessages();
      if (status) status.textContent = "";
      showAlert(`Ask failed: ${e.message}`, "err");
      await refreshAskAuthStatus();
    } finally {
      clearInterval(tick);
      if (btn) btn.disabled = false;
    }
  }

  function clearAskChat() {
    askHistory = [];
    renderAskMessages();
    const status = $("ask-status");
    if (status) status.textContent = "";
  }

  function init() {
    $("log-date").value = todayISO();
    addExerciseRow();
    $("btn-add-ex").addEventListener("click", () => addExerciseRow());
    $("log-form").addEventListener("submit", submitWorkout);
    $("btn-refresh").addEventListener("click", () => loadDashboard(true));
    $("btn-focus-log").addEventListener("click", () => {
      $("log-card").scrollIntoView({ behavior: "smooth", block: "start" });
      $("session_type").focus();
    });
    if ($("ingredient-form")) {
      $("ingredient-form").addEventListener("submit", submitIngredient);
    }
    if ($("targets-form")) {
      $("targets-form").addEventListener("submit", submitTargets);
    }
    if ($("btn-generate-plan")) {
      $("btn-generate-plan").addEventListener("click", generatePlan);
    }
    if ($("exercise-form")) {
      $("exercise-form").addEventListener("submit", submitExerciseCatalog);
    }
    if ($("workout-goals-form")) {
      $("workout-goals-form").addEventListener("submit", submitWorkoutGoals);
    }
    if ($("btn-generate-workout")) {
      $("btn-generate-workout").addEventListener("click", () => generateWorkoutPlan());
    }
    if ($("btn-force-session-push")) {
      $("btn-force-session-push").addEventListener("click", () => generateWorkoutPlan("push"));
    }
    if ($("btn-force-session-pull")) {
      $("btn-force-session-pull").addEventListener("click", () => generateWorkoutPlan("pull"));
    }
    if ($("btn-force-session-legs")) {
      $("btn-force-session-legs").addEventListener("click", () => generateWorkoutPlan("legs"));
    }
    if ($("ask-form")) {
      $("ask-form").addEventListener("submit", submitAsk);
    }
    if ($("btn-ask-clear")) {
      $("btn-ask-clear").addEventListener("click", clearAskChat);
    }
    refreshAskAuthStatus();
    loadDashboard();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
