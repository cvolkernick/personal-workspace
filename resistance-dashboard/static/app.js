/* Resistance training dashboard front-end */
(() => {
  const $ = (id) => document.getElementById(id);

  let state = null;
  /** Inventory row currently in inline edit. Null means display-only (no write). */
  let inventoryEditId = null;
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
  const CAL_IN_OUT_SPAN_DAYS = 60;

  function todayISO() {
    // Browser local civil date (matches host TZ when you open dashboard on this Mac).
    const d = new Date();
    const z = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
  }

  /** Fill missing civil days with 0h sleep (sleep debt). End = today. */
  function fillSleepCalendarDays(points, windowDays = 90) {
    const by = {};
    (points || []).forEach((s) => {
      if (!s || !s.date) return;
      const d = String(s.date).slice(0, 10);
      by[d] = (by[d] || 0) + (Number(s.sleep_hours) || 0);
    });
    const end = new Date();
    end.setHours(0, 0, 0, 0);
    const z = (n) => String(n).padStart(2, "0");
    const iso = (dt) =>
      `${dt.getFullYear()}-${z(dt.getMonth() + 1)}-${z(dt.getDate())}`;
    const out = [];
    for (let i = windowDays - 1; i >= 0; i--) {
      const d = new Date(end);
      d.setDate(d.getDate() - i);
      const key = iso(d);
      out.push({
        date: key,
        sleep_hours: by[key] != null ? by[key] : 0,
        source: by[key] != null ? "logged" : "implied_zero",
      });
    }
    return out;
  }

  /** Fill missing civil days with 0 ml water so unlogged days appear on the chart. */
  function fillHydrationCalendarDays(points, windowDays = 90) {
    const by = {};
    (points || []).forEach((h) => {
      if (!h || !h.date) return;
      const d = String(h.date).slice(0, 10);
      by[d] = (by[d] || 0) + (Number(h.water_ml) || 0);
    });
    const end = new Date();
    end.setHours(0, 0, 0, 0);
    const z = (n) => String(n).padStart(2, "0");
    const iso = (dt) =>
      `${dt.getFullYear()}-${z(dt.getMonth() + 1)}-${z(dt.getDate())}`;
    const out = [];
    for (let i = windowDays - 1; i >= 0; i--) {
      const d = new Date(end);
      d.setDate(d.getDate() - i);
      const key = iso(d);
      out.push({
        date: key,
        water_ml: by[key] != null ? by[key] : 0,
        source: by[key] != null ? "logged" : "implied_zero",
      });
    }
    return out;
  }

  /**
   * Daily water target from body weight (heuristic).
   * 35 ml/kg ≈ common athletic baseline (roughly 0.5 oz/lb). Not individualized
   * for heat/sweat; good enough for a chart guide line.
   */
  function hydrationTargetMlFromLbs(weightLbs) {
    const lbs = Number(weightLbs);
    if (!Number.isFinite(lbs) || lbs <= 0) return null;
    const kg = lbs / 2.2046226218;
    return Math.round(kg * 35);
  }

  /** Forward-fill last known weight onto each calendar date (lbs). */
  function weightLbsSeriesForDates(weightPoints, dates) {
    const sorted = (weightPoints || [])
      .map((w) => ({
        date: String(w.date || "").slice(0, 10),
        lbs: Number(w.weight_lbs != null ? w.weight_lbs : w.lbs),
      }))
      .filter((w) => w.date && Number.isFinite(w.lbs) && w.lbs > 0)
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    let j = 0;
    let last = null;
    // seed last with any weight on or before first date
    if (sorted.length && dates.length) {
      const first = dates[0];
      for (const w of sorted) {
        if (w.date <= first) last = w.lbs;
        else break;
      }
    }
    const out = [];
    for (const d of dates) {
      while (j < sorted.length && sorted[j].date <= d) {
        last = sorted[j].lbs;
        j += 1;
      }
      out.push(last);
    }
    return out;
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

  /** ~3500 kcal ≈ 1 lb tissue rule-of-thumb for energy vs scale alignment. */
  const KCAL_PER_LB = 3500;

  /**
   * Compare logged cumulative intake−burned to observed weight change in the same window.
   * Returns null if not enough data.
   */
  function energyWeightAlignment({
    cumDeltaKcal,
    pairDays,
    weights,
    windowStart,
    windowEnd,
    goalHint,
  }) {
    if (pairDays == null || pairDays < 5 || cumDeltaKcal == null) return null;
    const inWin = (weights || [])
      .map((w) => ({
        date: String(w.date || "").slice(0, 10),
        lbs: Number(w.weight_lbs),
      }))
      .filter(
        (w) =>
          w.date &&
          !Number.isNaN(w.lbs) &&
          w.date >= windowStart &&
          w.date <= windowEnd
      )
      .sort((a, b) => a.date.localeCompare(b.date));
    if (inWin.length < 2) return null;
    const first = inWin[0];
    const last = inWin[inWin.length - 1];
    // Prefer span of at least ~7 days between weigh-ins
    const spanMs =
      new Date(last.date + "T12:00:00").getTime() -
      new Date(first.date + "T12:00:00").getTime();
    if (spanMs < 5 * 86400000) return null;

    const actualLb = last.lbs - first.lbs;
    const expectedLb = cumDeltaKcal / KCAL_PER_LB;
    // Residual: scale moved more up (or less down) than energy balance implies
    const residualLb = actualLb - expectedLb;
    const absExp = Math.abs(expectedLb);
    const absAct = Math.abs(actualLb);
    const absRes = Math.abs(residualLb);

    // Same direction if both near zero, or product positive, or both small
    const bothNearFlat = absExp < 0.4 && absAct < 0.4;
    const sameSign =
      bothNearFlat ||
      (expectedLb === 0 && absAct < 0.5) ||
      expectedLb * actualLb > 0;
    // Align if residual small absolute OR relative to expected change
    const aligned =
      absRes <= 1.25 || (absExp >= 0.75 && absRes / absExp <= 0.55);

    let status = "mixed";
    if (aligned && (sameSign || bothNearFlat)) status = "aligned";
    else if (!sameSign && absRes >= 1.0) status = "divergent";
    else if (absRes >= 1.5) status = "offset";

    // Goal: cut / gain / recomp from notes or energy direction
    const hint = String(goalHint || "").toLowerCase();
    let goal = "recomp";
    if (/cut|deficit|loss|lean/.test(hint)) goal = "cut";
    else if (/bulk|surplus|gain|mass/.test(hint)) goal = "gain";
    else if (cumDeltaKcal < -1500) goal = "cut";
    else if (cumDeltaKcal > 1500) goal = "gain";

    const advice = [];
    if (status === "aligned") {
      advice.push(
        "Logged energy balance and scale change roughly line up for this window — good calibration of intake/burn tracking."
      );
      if (goal === "cut" && actualLb > -0.3) {
        advice.push(
          "For fat loss, deepen the deficit slightly (or improve adherence) — scale is nearly flat despite a logged deficit."
        );
      } else if (goal === "gain" && actualLb < 0.3) {
        advice.push(
          "For mass gain, add a small surplus — scale is flat despite a logged surplus/near balance."
        );
      }
    } else {
      // Scale down more (or up less) than energy implies → residual negative
      if (residualLb <= -1.0) {
        advice.push(
          "Scale dropped more (or rose less) than the logged calorie balance suggests."
        );
        advice.push(
          "Check: under-logged food, overestimated burn, or water/glycogen noise. If logging is solid and the goal is a cut, you may not need a deeper deficit."
        );
      } else if (residualLb >= 1.0) {
        advice.push(
          "Scale held or rose more than the logged calorie balance suggests."
        );
        advice.push(
          "Common fixes: tighten food logging (oils, drinks, bites), treat wearable burn as an estimate, reduce weekend surplus. If goal is a cut, increase the true deficit (lower intake or more NEAT)."
        );
        if (goal === "gain") {
          advice.push(
            "If bulk is the goal and weight is rising faster than planned, trim surplus slightly."
          );
        }
      } else if (!sameSign) {
        advice.push(
          "Energy balance and weight moved in opposite directions — treat this window as noisy; recheck after more consistent weigh-ins."
        );
      }
    }
    advice.push(
      "Rule of thumb only (~3,500 kcal ≈ 1 lb); short windows and water weight can dominate."
    );

    return {
      status,
      actualLb,
      expectedLb,
      residualLb,
      first,
      last,
      goal,
      advice,
    };
  }

  function recoveryClass(label) {
    const l = (label || "").toLowerCase();
    if (l === "ready") return "ready";
    if (l === "moderate") return "moderate";
    if (l === "caution") return "caution";
    return "needs-rest";
  }

  function fmtBatteryWhen(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16);
      return d.toLocaleString(undefined, {
        weekday: "short",
        hour: "numeric",
        minute: "2-digit",
      });
    } catch (_) {
      return "—";
    }
  }

  function renderSleepBatteryMini(battery) {
    const el = $("sleep-battery-panel");
    if (!el) return;
    const b = battery || {};
    const pct = Math.min(
      100,
      Math.max(0, Number(b.pct_charged ?? b.pct_of_target) || 0)
    );
    const level = String(b.level || "critical").toLowerCase();
    const mode = b.mode || "no_data";
    const awakeBudget = Number(b.awake_budget_hours) || 15;
    const hoursAwake = Number(b.hours_awake) || 0;
    const untilEmpty = Number(b.hours_until_empty) || 0;
    const sleepTgt = Number(b.sleep_target_hours || b.target_hours) || 8;

    let subLabel = "remaining";
    if (mode === "sleeping") subLabel = "charging";
    else if (mode === "no_data") subLabel = "no data";
    else if (pct <= 0) subLabel = "empty · sleep";

    const tip =
      b.summary ||
      "Charge at wake scales with last night vs 8h target (capped ≤2h earlier empty) · 9h around sleep (30m wind-down + 30m onset) · 15h awake · empty = start wind-down";
    el.title = tip;

    // Compact awake-window timeline (wake → bedtime empty)
    let timelineHtml = "";
    if (mode === "awake" && awakeBudget > 0) {
      const usedPct = Math.min(100, (hoursAwake / awakeBudget) * 100);
      timelineHtml = `
        <div class="sb-timeline" aria-hidden="true">
          <div class="sb-timeline-used" style="width:${usedPct.toFixed(1)}%"></div>
        </div>
        <div class="sb-timeline-labels">
          <span>wake</span>
          <span>bedtime</span>
        </div>`;
    }

    el.innerHTML = `
      <div class="sb-panel-head">
        <span class="sb-panel-title">Sleep battery</span>
        <span class="sb-panel-hint muted">full at wake · ${awakeBudget}h awake</span>
      </div>
      <div class="sb-panel-body">
        <div class="sb-shell" aria-label="Sleep battery ${pct.toFixed(0)} percent">
          <div class="sb-fill-wrap">
            <div class="sb-fill ${level}" style="width:${pct.toFixed(0)}%"></div>
            <div class="sb-label">
              <span class="sb-big">${mode === "no_data" ? "—" : `${pct.toFixed(0)}%`}</span>
              <span class="sb-sub">${subLabel}</span>
            </div>
          </div>
        </div>
        <div class="sb-side">
          <div class="sb-stats">
            <div class="sb-stat">
              <div class="sb-stat-label">Charge</div>
              <div class="sb-stat-value">${mode === "no_data" ? "—" : `${pct.toFixed(0)}%`}</div>
            </div>
            <div class="sb-stat">
              <div class="sb-stat-label">Awake</div>
              <div class="sb-stat-value">${hoursAwake.toFixed(1)}h / ${awakeBudget}h</div>
            </div>
            <div class="sb-stat">
              <div class="sb-stat-label">Until empty</div>
              <div class="sb-stat-value">${untilEmpty.toFixed(1)}h</div>
            </div>
            <div class="sb-stat">
              <div class="sb-stat-label">Bedtime</div>
              <div class="sb-stat-value">${fmtBatteryWhen(b.empty_at)}</div>
            </div>
          </div>
          ${timelineHtml}
        </div>
      </div>
      <p class="sb-summary muted">${b.summary || "Sync Google Health sleep to charge the battery."}</p>
      <p class="sb-meta muted">${
        mode === "no_data"
          ? "No sleep cycle yet"
          : `~${sleepTgt}h sleep · ${Number(b.sleep_around_hours || sleepTgt + 1).toFixed(0)}h around sleep${
              b.last_wake_at ? ` · woke ${fmtBatteryWhen(b.last_wake_at)}` : ""
            }${
              b.last_sleep_hours != null
                ? ` · last cycle ${Number(b.last_sleep_hours).toFixed(1)}h`
                : ""
            }`
      }</p>`;
  }

  /** Auto-dismiss delays (ms). Errors stay longer; 0 = until dismissed. */
  const ALERT_TTL = { ok: 5000, warn: 8000, err: 12000 };

  function showAlert(msg, kind = "warn") {
    const box = $("alerts");
    if (!box) return;
    const el = document.createElement("div");
    el.className = `alert ${kind}`;
    el.setAttribute("role", kind === "err" ? "alert" : "status");

    const text = document.createElement("span");
    text.className = "alert-text";
    text.textContent = msg;

    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "alert-dismiss";
    dismiss.setAttribute("aria-label", "Dismiss");
    dismiss.textContent = "✕";
    dismiss.addEventListener("click", () => {
      el.remove();
      if (el._ttlTimer) clearTimeout(el._ttlTimer);
    });

    el.appendChild(text);
    el.appendChild(dismiss);
    box.appendChild(el);

    // Cap stack so runaway actions don't bury the page
    while (box.children.length > 5) {
      const first = box.firstElementChild;
      if (first && first._ttlTimer) clearTimeout(first._ttlTimer);
      first.remove();
    }

    const ttl = ALERT_TTL[kind] != null ? ALERT_TTL[kind] : ALERT_TTL.warn;
    if (ttl > 0) {
      el._ttlTimer = setTimeout(() => {
        el.classList.add("alert-fade");
        setTimeout(() => el.remove(), 320);
      }, ttl);
    }
  }

  function clearAlerts() {
    const box = $("alerts");
    if (!box) return;
    Array.from(box.children).forEach((el) => {
      if (el._ttlTimer) clearTimeout(el._ttlTimer);
    });
    box.innerHTML = "";
  }

  function addSetRow(setsWrap, prefill = {}) {
    const row = document.createElement("div");
    row.className = "set-row";
    row.innerHTML = `
      <label>Weight (lbs)
        <input type="number" class="set-weight" required min="0" step="0.5" inputmode="decimal" value="${prefill.weight_lbs ?? ""}" />
      </label>
      <label>Reps
        <input type="number" class="set-reps" required min="1" step="1" inputmode="numeric" value="${prefill.reps ?? 10}" />
      </label>
      <label>Sets
        <input type="number" class="set-sets" required min="1" step="1" inputmode="numeric" value="${prefill.sets ?? 1}" />
      </label>
      <button type="button" class="set-remove" aria-label="Remove set">✕</button>
    `;
    row.querySelector(".set-remove").addEventListener("click", () => {
      if (setsWrap.querySelectorAll(".set-row").length > 1) row.remove();
    });
    setsWrap.appendChild(row);
  }

  /**
   * One exercise card with multiple set groups.
   * Saves as: Name: 50 lbs x 1 x 10, 45 lbs x 1 x 8  (matches existing logs)
   * prefill: { name, sets: [{weight_lbs, sets, reps}, ...] } or flat weight/sets/reps
   */
  function addExerciseRow(prefill = {}) {
    const wrap = $("exercise-rows");
    const card = document.createElement("div");
    card.className = "exercise-card";

    let setPrefills = [];
    if (Array.isArray(prefill.sets) && prefill.sets.length) {
      setPrefills = prefill.sets;
    } else if (prefill.weight_lbs != null || prefill.reps != null) {
      setPrefills = [
        {
          weight_lbs: prefill.weight_lbs,
          sets: prefill.sets ?? 3,
          reps: prefill.reps ?? 10,
        },
      ];
    } else {
      setPrefills = [{ weight_lbs: "", sets: 1, reps: 10 }];
    }

    card.innerHTML = `
      <div class="exercise-card-head">
        <label class="ex-name-label">Exercise
          <input type="text" class="ex-name" required placeholder="e.g. DB Flat Press" value="${prefill.name || ""}" />
        </label>
        <button type="button" class="ex-remove" aria-label="Remove exercise">Remove</button>
      </div>
      <div class="set-rows"></div>
      <div class="exercise-card-actions">
        <button type="button" class="btn-add-set">+ Set</button>
        <span class="muted set-hint">Different weights? Add a set per load. Same load ×3 → set Sets=3. PR is auto-tagged from history on save.</span>
      </div>
    `;

    const setsWrap = card.querySelector(".set-rows");
    setPrefills.forEach((s) => addSetRow(setsWrap, s));

    card.querySelector(".btn-add-set").addEventListener("click", () => {
      const last = setsWrap.querySelector(".set-row:last-child");
      const pref = last
        ? {
            weight_lbs: last.querySelector(".set-weight").value,
            sets: 1,
            reps: last.querySelector(".set-reps").value || 10,
          }
        : { sets: 1, reps: 10 };
      addSetRow(setsWrap, pref);
      setsWrap.querySelector(".set-row:last-child .set-weight")?.focus();
    });

    card.querySelector(".ex-remove").addEventListener("click", () => {
      if ($("exercise-rows").children.length > 1) card.remove();
    });

    wrap.appendChild(card);
  }

  function collectExercises() {
    return [...$("exercise-rows").querySelectorAll(".exercise-card")].map((card) => {
      const name = card.querySelector(".ex-name").value.trim();
      const sets = [...card.querySelectorAll(".set-row")].map((row) => ({
        weight_lbs: Number(row.querySelector(".set-weight").value),
        sets: Number(row.querySelector(".set-sets").value) || 1,
        reps: Number(row.querySelector(".set-reps").value),
      }));
      return { name, sets };
    });
  }

  function destroyChart(c) {
    if (c) c.destroy();
  }

  /** Charts often measure wrong while their panel is display:none — fix on tab show. */
  function resizeAllCharts() {
    [
      volumeChart,
      strengthChart,
      weightChart,
      sleepChart,
      caloriesChart,
      macrosChart,
      hydrationChart,
    ].forEach((c) => {
      try {
        if (c && typeof c.resize === "function") c.resize();
      } catch (_) {
        /* ignore */
      }
    });
  }

  function prefersReducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (_) {
      return false;
    }
  }

  function chartDefaults() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: prefersReducedMotion() ? false : undefined,
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
    // Daily volume = last 90 calendar days, one bar per day (0 if no session).
    const vol = data.volume_by_day || [];
    const volLabels = vol.map((v) => v.date);
    const volVals = vol.map((v) => v.volume);
    const volTrend = linearTrend(volVals);
    const vSlope = trendSlopePerDay(volVals);
    destroyChart(volumeChart);
    volumeChart = new Chart($("chart-volume"), {
      data: {
        labels: volLabels,
        datasets: [
          {
            type: "bar",
            label: "Daily volume (lb)",
            data: volVals,
            backgroundColor: "rgba(61,156,240,0.55)",
            borderRadius: 4,
            order: 2,
          },
          {
            type: "line",
            label: "Trend",
            data: volTrend,
            borderColor: "#f0b429",
            borderDash: [6, 4],
            borderWidth: 2.5,
            pointRadius: 0,
            tension: 0,
            order: 1,
          },
        ],
      },
      options: {
        ...chartDefaults(),
        scales: {
          ...chartDefaults().scales,
          x: {
            ...chartDefaults().scales.x,
            ticks: {
              ...chartDefaults().scales.x.ticks,
              maxTicksLimit: 12,
              maxRotation: 0,
            },
          },
        },
      },
    });
    if ($("volume-trend-note")) {
      const trained = volVals.filter((v) => v > 0).length;
      const total = volVals.reduce((s, v) => s + (Number(v) || 0), 0);
      if (vSlope == null || trained < 2) {
        $("volume-trend-note").textContent =
          `Last ${vol.length} days · ${trained} training days · total ${fmtNum(total)} lb`;
      } else {
        const dir = vSlope > 0 ? "up" : vSlope < 0 ? "down" : "flat";
        $("volume-trend-note").textContent =
          `Last ${vol.length} days · ${trained} training days · total ${fmtNum(total)} lb · ` +
          `trend ${dir} (~${vSlope >= 0 ? "+" : ""}${Math.round(vSlope * 7).toLocaleString()} lb/week)`;
      }
    }

    const exercises = data.top_exercises || [];
    if (!selectedExercise || !exercises.includes(selectedExercise)) {
      selectedExercise = exercises[0] || null;
    }
    // Compact picker (select + prev/next) keeps strength card height near volume chart
    const sel = $("exercise-select");
    if (sel) {
      sel.innerHTML = "";
      exercises.forEach((name) => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        if (name === selectedExercise) opt.selected = true;
        sel.appendChild(opt);
      });
      if (!sel.dataset.bound) {
        sel.dataset.bound = "1";
        sel.addEventListener("change", () => {
          selectedExercise = sel.value || null;
          if (state) renderStrength(state);
        });
      }
    }
    const stepExercise = (dir) => {
      if (!exercises.length) return;
      let i = exercises.indexOf(selectedExercise);
      if (i < 0) i = 0;
      i = (i + dir + exercises.length) % exercises.length;
      selectedExercise = exercises[i];
      if (sel) sel.value = selectedExercise;
      if (state) renderStrength(state);
    };
    const prev = $("exercise-prev");
    const next = $("exercise-next");
    if (prev && !prev.dataset.bound) {
      prev.dataset.bound = "1";
      prev.addEventListener("click", () => stepExercise(-1));
    }
    if (next && !next.dataset.bound) {
      next.dataset.bound = "1";
      next.addEventListener("click", () => stepExercise(1));
    }
    // Legacy tabs container kept hidden for any residual CSS
    const tabs = $("exercise-tabs");
    if (tabs) tabs.innerHTML = "";
    if ($("strength-trend-note")) {
      $("strength-trend-note").textContent = exercises.length
        ? `${exercises.length} exercises by log frequency — use ‹ › or the menu`
        : "No exercises with strength history yet.";
    }
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
    // Current body-weight goal from More → Daily targets (optional guide line)
    const weightGoalRaw =
      (data.nutrition_store &&
        data.nutrition_store.targets &&
        data.nutrition_store.targets.weight_goal_lbs) ??
      null;
    const weightGoal =
      weightGoalRaw != null &&
      Number.isFinite(Number(weightGoalRaw)) &&
      Number(weightGoalRaw) > 0
        ? Number(weightGoalRaw)
        : null;
    const weightGoalLine =
      weightGoal != null && weights.length
        ? weights.map(() => weightGoal)
        : null;
    const weightDatasets = [
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
    ];
    if (weightGoalLine) {
      weightDatasets.push({
        label: `Goal (${weightGoal.toFixed(1)} lb)`,
        data: weightGoalLine,
        borderColor: "rgba(192, 132, 252, 0.95)",
        borderDash: [2, 3],
        borderWidth: 2.5,
        pointRadius: 0,
        fill: false,
        tension: 0,
        order: 0,
        spanGaps: true,
      });
    }
    // Include goal in Y domain so a far target still draws inside the plot
    const weightOpts = chartDefaults();
    const finiteW = weightVals.filter((v) => v != null && Number.isFinite(Number(v))).map(Number);
    if (weightGoal != null) finiteW.push(weightGoal);
    if (finiteW.length) {
      const lo = Math.min(...finiteW);
      const hi = Math.max(...finiteW);
      const pad = Math.max(1.5, (hi - lo) * 0.12 || 2);
      weightOpts.scales = {
        ...weightOpts.scales,
        y: {
          ...weightOpts.scales.y,
          suggestedMin: lo - pad,
          suggestedMax: hi + pad,
        },
      };
    }
    destroyChart(weightChart);
    weightChart = new Chart($("chart-weight"), {
      type: "line",
      data: {
        labels: weights.map((w) => w.date),
        datasets: weightDatasets,
      },
      options: weightOpts,
    });
    if ($("weight-trend-note")) {
      const bits = [];
      if (wSlope == null || weights.length < 2) {
        bits.push("Need more weigh-ins for a trend.");
      } else {
        const perWeek = wSlope * 7;
        const dir = perWeek > 0.05 ? "up" : perWeek < -0.05 ? "down" : "flat";
        bits.push(
          `90d series · linear trend ${dir} (~${perWeek >= 0 ? "+" : ""}${perWeek.toFixed(2)} lb/week) · ${weights.length} points`
        );
      }
      if (weightGoal != null) {
        const lastW =
          [...weightVals].reverse().find((v) => v != null && Number.isFinite(v)) ??
          null;
        if (lastW != null) {
          const gap = lastW - weightGoal;
          bits.push(
            `goal ${weightGoal.toFixed(1)} lb · ${gap >= 0 ? "+" : ""}${gap.toFixed(1)} lb vs last`
          );
        } else {
          bits.push(`goal ${weightGoal.toFixed(1)} lb`);
        }
      } else {
        bits.push("no goal line yet — set Weight goal (lb) under More → Daily targets, then Save");
      }
      $("weight-trend-note").textContent = bits.join(" · ");
    }

    // Prefer server-expanded calendar series (unlogged nights = 0h).
    // Chart span: last 90 calendar days (zeros for unlogged nights) — same window as volume/weight/hydration.
    const sleepRaw = [...((data.health && data.health.sleep) || [])].sort((a, b) =>
      String(a.date).localeCompare(String(b.date))
    );
    const sleepFilled = fillSleepCalendarDays(sleepRaw, 90);
    const sleep = downsamplePoints(sleepFilled, 90);
    const sleepVals = sleep.map((s) => Number(s.sleep_hours) || 0);
    const sleepTrend = linearTrend(sleepVals);
    const sleepRoll7 = rollingAverage(sleepVals, 7);
    const sleepTarget = sleepVals.map(() => 8);
    const sSlope = trendSlopePerDay(sleepVals);
    const lastRoll =
      [...sleepRoll7].reverse().find((v) => v != null && !Number.isNaN(v)) ?? null;
    const zeroNights = sleepVals.filter((v) => v <= 0).length;
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
            backgroundColor: sleepVals.map((v) =>
              v <= 0 ? "rgba(240,113,120,0.55)" : "rgba(240,180,41,0.45)"
            ),
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
          x: {
            ...chartDefaults().scales.x,
            ticks: {
              ...chartDefaults().scales.x.ticks,
              maxTicksLimit: 12,
              maxRotation: 0,
            },
          },
          y: {
            ...chartDefaults().scales.y,
            suggestedMin: 0,
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
        const zeroTxt =
          zeroNights > 0
            ? ` · ${zeroNights} night(s) with no log counted as 0h`
            : "";
        $("sleep-trend-note").textContent = `Latest 7d avg: ${lastRoll.toFixed(2)} h (${vsGoal})${slopeTxt}${zeroTxt} · ${sleep.length} calendar days`;
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
    // Calendar-complete hydration: unlogged days = 0 ml (same idea as sleep).
    const hydrationRaw = [
      ...((data.health && data.health.hydration) || []),
    ].sort((a, b) => String(a.date).localeCompare(String(b.date)));
    const hydrationFilled = fillHydrationCalendarDays(hydrationRaw, 90);
    const hydration = downsamplePoints(hydrationFilled, 90);
    // Intake vs burned: 60d rolling window (demo: hide pre-window inflated/corrupt burned).
    // Macro split reuses its own 90d axis below.
    const calSpanDays = CAL_IN_OUT_SPAN_DAYS;
    const calEnd = new Date();
    calEnd.setHours(0, 0, 0, 0);
    const calLabels = [];
    for (let i = calSpanDays - 1; i >= 0; i--) {
      const d = new Date(calEnd);
      d.setDate(d.getDate() - i);
      const z = (n) => String(n).padStart(2, "0");
      calLabels.push(
        `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`
      );
    }
    // Full series mapped onto the intake/burned civil axis.
    const nutritionAll = [...((data.health && data.health.nutrition) || [])].sort(
      (a, b) => String(a.date).localeCompare(String(b.date))
    );
    const burnedAll = [
      ...((data.health && data.health.calories_burned) || []),
    ].sort((a, b) => String(a.date).localeCompare(String(b.date)));
    const intakeByDate = Object.fromEntries(
      nutritionAll.map((n) => [n.date, n.calories])
    );
    const burnedByDate = Object.fromEntries(
      burnedAll.map((b) => [b.date, b.calories])
    );
    const intakeSeries = calLabels.map((d) => {
      const v = intakeByDate[d];
      return v == null || Number.isNaN(Number(v)) ? null : Number(v);
    });
    const burnedSeries = calLabels.map((d) => {
      const v = burnedByDate[d];
      return v == null || Number.isNaN(Number(v)) ? null : Number(v);
    });
    destroyChart(caloriesChart);
    if ($("chart-calories")) {
      // Shade band between intake & burned: green surplus, red deficit.
      const surplusDeficitFill = {
        id: "surplusDeficitFill",
        beforeDatasetsDraw(chart) {
          const { ctx, chartArea, scales } = chart;
          if (!chartArea) return;
          const metaIn = chart.getDatasetMeta(0);
          const metaBurn = chart.getDatasetMeta(1);
          if (!metaIn?.data?.length || !metaBurn?.data?.length) return;

          const yScale = scales.y;
          const pts = [];
          for (let i = 0; i < metaIn.data.length; i++) {
            const pin = metaIn.data[i];
            const pburn = metaBurn.data[i];
            const vin = intakeSeries[i];
            const vburn = burnedSeries[i];
            if (
              vin == null ||
              vburn == null ||
              !pin ||
              !pburn ||
              pin.skip ||
              pburn.skip
            ) {
              pts.push(null);
              continue;
            }
            pts.push({
              x: pin.x,
              yIn: yScale.getPixelForValue(vin),
              yBurn: yScale.getPixelForValue(vburn),
              surplus: vin > vburn,
              equal: vin === vburn,
            });
          }

          const fillSeg = (a, b, surplus) => {
            ctx.save();
            ctx.beginPath();
            ctx.moveTo(a.x, a.yIn);
            ctx.lineTo(b.x, b.yIn);
            ctx.lineTo(b.x, b.yBurn);
            ctx.lineTo(a.x, a.yBurn);
            ctx.closePath();
            ctx.fillStyle = surplus
              ? "rgba(92, 225, 168, 0.28)"
              : "rgba(240, 113, 120, 0.28)";
            ctx.fill();
            ctx.restore();
          };

          for (let i = 0; i < pts.length - 1; i++) {
            const a = pts[i];
            const b = pts[i + 1];
            if (!a || !b || a.equal && b.equal) continue;

            // Both sides same regime → simple quad
            if (a.surplus === b.surplus && !a.equal && !b.equal) {
              fillSeg(a, b, a.surplus);
              continue;
            }

            // Crossing: split at interpolated intersection in pixel space
            const dA = a.yIn - a.yBurn;
            const dB = b.yIn - b.yBurn;
            if (dA === 0 && dB === 0) continue;
            if (dA * dB > 0 && !a.equal && !b.equal) {
              fillSeg(a, b, a.surplus);
              continue;
            }
            const tc = dA === dB ? 0.5 : dA / (dA - dB);
            const t = Math.min(1, Math.max(0, tc));
            const yCross =
              a.yIn + (b.yIn - a.yIn) * t; // equal at true zero-crossing approx
            const cross = {
              x: a.x + (b.x - a.x) * t,
              yIn: yCross,
              yBurn: yCross,
            };
            if (!a.equal) fillSeg(a, cross, a.surplus);
            if (!b.equal) fillSeg(cross, b, b.surplus);
          }
        },
      };

      caloriesChart = new Chart($("chart-calories"), {
        type: "line",
        data: {
          labels: calLabels,
          datasets: [
            {
              label: "Intake (kcal)",
              data: intakeSeries,
              borderColor: "#5ce1a8",
              backgroundColor: "rgba(92, 225, 168, 0.15)",
              tension: 0.25,
              spanGaps: true,
              pointRadius: 3,
              order: 1,
            },
            {
              label: "Burned (kcal)",
              data: burnedSeries,
              borderColor: "#f07178",
              backgroundColor: "rgba(240, 113, 120, 0.15)",
              tension: 0.25,
              spanGaps: true,
              pointRadius: 3,
              order: 1,
            },
          ],
        },
        options: {
          ...chartDefaults(),
          plugins: {
            ...chartDefaults().plugins,
            legend: {
              labels: {
                color: "#8b9bb4",
                generateLabels(chart) {
                  const defaults = Chart.defaults.plugins.legend.labels.generateLabels(chart);
                  return defaults.concat([
                    {
                      text: "Surplus (intake > burned)",
                      fillStyle: "rgba(92, 225, 168, 0.45)",
                      strokeStyle: "rgba(92, 225, 168, 0.8)",
                      lineWidth: 0,
                      hidden: false,
                      datasetIndex: -1,
                    },
                    {
                      text: "Deficit (intake < burned)",
                      fillStyle: "rgba(240, 113, 120, 0.45)",
                      strokeStyle: "rgba(240, 113, 120, 0.8)",
                      lineWidth: 0,
                      hidden: false,
                      datasetIndex: -1,
                    },
                  ]);
                },
              },
            },
          },
        },
        plugins: [surplusDeficitFill],
      });
    }

    destroyChart(macrosChart);
    if ($("chart-macros")) {
      // 90d civil axis (aligned with body weight; independent of intake/burned span).
      // Map logged nutrition onto each day; null split when no macros that day.
      const macroSpanDays = 90;
      const macroEnd = new Date();
      macroEnd.setHours(0, 0, 0, 0);
      const macroLabels = [];
      for (let i = macroSpanDays - 1; i >= 0; i--) {
        const d = new Date(macroEnd);
        d.setDate(d.getDate() - i);
        const z = (n) => String(n).padStart(2, "0");
        macroLabels.push(
          `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`
        );
      }
      const macroByDate = Object.fromEntries(
        nutritionAll
          .filter(
            (n) =>
              n &&
              (n.protein_g != null || n.carbs_g != null || n.fat_g != null)
          )
          .map((n) => [String(n.date).slice(0, 10), n])
      );
      const macroDays = macroLabels.map((d) => {
        const n = macroByDate[d];
        if (!n) {
          return {
            date: d,
            protein_g: null,
            carbs_g: null,
            fat_g: null,
          };
        }
        return {
          date: d,
          protein_g: n.protein_g,
          carbs_g: n.carbs_g,
          fat_g: n.fat_g,
        };
      });
      const splits = macroDays.map((n) => {
        if (
          n.protein_g == null &&
          n.carbs_g == null &&
          n.fat_g == null
        ) {
          return { p: null, c: null, f: null, grams: { p: 0, c: 0, f: 0 } };
        }
        const p = Number(n.protein_g) || 0;
        const c = Number(n.carbs_g) || 0;
        const f = Number(n.fat_g) || 0;
        const pK = p * 4;
        const cK = c * 4;
        const fK = f * 9;
        const tot = pK + cK + fK;
        if (tot <= 0) return { p: null, c: null, f: null, grams: { p, c, f } };
        return {
          p: Math.round((pK / tot) * 1000) / 10,
          c: Math.round((cK / tot) * 1000) / 10,
          f: Math.round((fK / tot) * 1000) / 10,
          grams: { p, c, f },
        };
      });
      const pPct = splits.map((s) => s.p);
      const cPct = splits.map((s) => s.c);
      const fPct = splits.map((s) => s.f);
      const rollWin = 7;
      const pRoll = rollingAverage(pPct, rollWin).map((v) =>
        v == null ? null : Math.round(v * 10) / 10
      );
      const cRoll = rollingAverage(cPct, rollWin).map((v) =>
        v == null ? null : Math.round(v * 10) / 10
      );
      const fRoll = rollingAverage(fPct, rollWin).map((v) =>
        v == null ? null : Math.round(v * 10) / 10
      );
      const base = chartDefaults();
      macrosChart = new Chart($("chart-macros"), {
        data: {
          labels: macroDays.map((n) => n.date),
          datasets: [
            {
              type: "bar",
              label: "Protein %",
              data: pPct,
              backgroundColor: "rgba(61,156,240,0.45)",
              borderRadius: 2,
              stack: "macros",
              spanGaps: false,
              yAxisID: "y",
              order: 2,
            },
            {
              type: "bar",
              label: "Carbs %",
              data: cPct,
              backgroundColor: "rgba(240,180,41,0.45)",
              borderRadius: 2,
              stack: "macros",
              spanGaps: false,
              yAxisID: "y",
              order: 2,
            },
            {
              type: "bar",
              label: "Fat %",
              data: fPct,
              backgroundColor: "rgba(240,113,120,0.4)",
              borderRadius: 2,
              stack: "macros",
              spanGaps: false,
              yAxisID: "y",
              order: 2,
            },
            {
              type: "line",
              label: "Protein 7d avg",
              data: pRoll,
              borderColor: "#3d9cf0",
              borderWidth: 2.5,
              pointRadius: 0,
              tension: 0.25,
              spanGaps: true,
              yAxisID: "y2",
              order: 1,
            },
            {
              type: "line",
              label: "Carbs 7d avg",
              data: cRoll,
              borderColor: "#f0b429",
              borderWidth: 2.5,
              pointRadius: 0,
              tension: 0.25,
              spanGaps: true,
              yAxisID: "y2",
              order: 1,
            },
            {
              type: "line",
              label: "Fat 7d avg",
              data: fRoll,
              borderColor: "#f07178",
              borderWidth: 2.5,
              pointRadius: 0,
              tension: 0.25,
              spanGaps: true,
              yAxisID: "y2",
              order: 1,
            },
          ],
        },
        options: {
          ...base,
          scales: {
            x: {
              ...base.scales.x,
              stacked: true,
            },
            y: {
              ...base.scales.y,
              stacked: true,
              min: 0,
              max: 100,
              ticks: {
                ...base.scales.y.ticks,
                callback: (v) => `${v}%`,
              },
              title: {
                display: true,
                text: "Daily split",
                color: "#8b9bb4",
                font: { size: 11 },
              },
            },
            // Separate axis so rolling lines are not stacked on the bars.
            y2: {
              display: false,
              min: 0,
              max: 100,
              stacked: false,
              grid: { drawOnChartArea: false },
            },
          },
          plugins: {
            ...base.plugins,
            tooltip: {
              callbacks: {
                label(ctx) {
                  const i = ctx.dataIndex;
                  const label = ctx.dataset.label || "";
                  const pct = ctx.parsed.y;
                  if (pct == null || Number.isNaN(pct)) return label;
                  if (label.includes("7d avg")) {
                    return `${label}: ${pct}%`;
                  }
                  const key = ["p", "c", "f"][ctx.datasetIndex];
                  const s = splits[i];
                  const g = s?.grams?.[key];
                  const name = label.replace(" %", "");
                  return `${name}: ${pct}%${g != null ? ` (${Math.round(g)} g)` : ""}`;
                },
              },
            },
          },
        },
      });
      if ($("macros-note")) {
        const note = $("macros-note");
        // Last civil day on a 90d axis is often empty (today not logged yet) —
        // use the most recent day that actually has a macro split.
        let last = null;
        let lastIdx = -1;
        for (let i = splits.length - 1; i >= 0; i--) {
          if (splits[i] && splits[i].p != null) {
            last = splits[i];
            lastIdx = i;
            break;
          }
        }
        const lastDate =
          lastIdx >= 0 && macroDays[lastIdx]
            ? macroDays[lastIdx].date
            : null;
        const lastRoll = (() => {
          for (let i = pRoll.length - 1; i >= 0; i--) {
            if (pRoll[i] != null && cRoll[i] != null && fRoll[i] != null) {
              return { p: pRoll[i], c: cRoll[i], f: fRoll[i] };
            }
          }
          return null;
        })();
        // Period averages over the same days shown on the chart
        let sumPg = 0;
        let sumCg = 0;
        let sumFg = 0;
        let nDays = 0;
        splits.forEach((s) => {
          if (!s || s.p == null) return;
          sumPg += Number(s.grams.p) || 0;
          sumCg += Number(s.grams.c) || 0;
          sumFg += Number(s.grams.f) || 0;
          nDays += 1;
        });
        if (!last || last.p == null) {
          note.innerHTML =
            `<p class="chart-summary-empty">Calorie share from protein / carbs / fat (4 / 4 / 9 kcal per gram). Lines = 7-day rolling avg %.</p>`;
        } else {
          const totK = sumPg * 4 + sumCg * 4 + sumFg * 9;
          const pctP =
            nDays > 0 && totK > 0
              ? Math.round((sumPg * 4 * 1000) / totK) / 10
              : null;
          const pctC =
            nDays > 0 && totK > 0
              ? Math.round((sumCg * 4 * 1000) / totK) / 10
              : null;
          const pctF =
            nDays > 0 && totK > 0
              ? Math.round((sumFg * 9 * 1000) / totK) / 10
              : null;
          const avgPg = nDays > 0 ? sumPg / nDays : 0;
          const avgCg = nDays > 0 ? sumCg / nDays : 0;
          const avgFg = nDays > 0 ? sumFg / nDays : 0;
          const firstD = macroDays[0] && macroDays[0].date;
          const lastD =
            macroDays[macroDays.length - 1] &&
            macroDays[macroDays.length - 1].date;
          const rangeTxt =
            firstD && lastD ? `${firstD} → ${lastD}` : `${nDays} days`;
          // Keep chip titles short so P/C/F stay on one row; date goes in subline.
          const latestSub = lastDate ? lastDate : "most recent logged day";
          // Daily targets → gram goals + calorie-share % (same 4/4/9 basis)
          const tgt =
            (data.nutrition_store && data.nutrition_store.targets) || {};
          const tgtG = {
            p: Number(tgt.protein_g) || 0,
            c: Number(tgt.carbs_g) || 0,
            f: Number(tgt.fat_g) || 0,
          };
          const tgtPct = macroCalPct(tgtG.p, tgtG.c, tgtG.f);
          const bandPct = (v, key) =>
            macroTargetBandClass(v, tgtPct[key]);
          note.innerHTML = `
            <div class="chart-summary-row">
              <div class="chart-summary-chip chip-protein ${bandPct(last.p, "p")}">
                <span class="chip-k">Latest · P</span>
                <span class="chip-v">${last.p}%</span>
                <span class="chip-s">${Math.round(last.grams.p)} g · ${latestSub}</span>
              </div>
              <div class="chart-summary-chip chip-carbs ${bandPct(last.c, "c")}">
                <span class="chip-k">Latest · C</span>
                <span class="chip-v">${last.c}%</span>
                <span class="chip-s">${Math.round(last.grams.c)} g · ${latestSub}</span>
              </div>
              <div class="chart-summary-chip chip-fat ${bandPct(last.f, "f")}">
                <span class="chip-k">Latest · F</span>
                <span class="chip-v">${last.f}%</span>
                <span class="chip-s">${Math.round(last.grams.f)} g · ${latestSub}</span>
              </div>
            </div>
            ${
              lastRoll
                ? `<div class="chart-summary-row">
              <div class="chart-summary-chip chip-protein ${bandPct(lastRoll.p, "p")}">
                <span class="chip-k">7d rolling · P</span>
                <span class="chip-v">${lastRoll.p}%</span>
                <span class="chip-s">of kcal · chart line</span>
              </div>
              <div class="chart-summary-chip chip-carbs ${bandPct(lastRoll.c, "c")}">
                <span class="chip-k">7d rolling · C</span>
                <span class="chip-v">${lastRoll.c}%</span>
                <span class="chip-s">of kcal · chart line</span>
              </div>
              <div class="chart-summary-chip chip-fat ${bandPct(lastRoll.f, "f")}">
                <span class="chip-k">7d rolling · F</span>
                <span class="chip-v">${lastRoll.f}%</span>
                <span class="chip-s">of kcal · chart line</span>
              </div>
            </div>`
                : ""
            }
            <div class="chart-summary-row">
              <div class="chart-summary-chip chip-protein ${bandPct(pctP, "p")}">
                <span class="chip-k">Period · P</span>
                <span class="chip-v">${pctP != null ? pctP + "%" : "—"}</span>
                <span class="chip-s">${Math.round(avgPg)} g avg/day · ±5pp of target</span>
              </div>
              <div class="chart-summary-chip chip-carbs ${bandPct(pctC, "c")}">
                <span class="chip-k">Period · C</span>
                <span class="chip-v">${pctC != null ? pctC + "%" : "—"}</span>
                <span class="chip-s">${Math.round(avgCg)} g avg/day · ±5pp of target</span>
              </div>
              <div class="chart-summary-chip chip-fat ${bandPct(pctF, "f")}">
                <span class="chip-k">Period · F</span>
                <span class="chip-v">${pctF != null ? pctF + "%" : "—"}</span>
                <span class="chip-s">${Math.round(avgFg)} g avg/day · ±5pp of target</span>
              </div>
            </div>
            <div class="macro-target-bar" title="Daily target calorie share from P×4 / C×4 / F×9">
              <span class="macro-target-bar-label">Targets</span>
              <div class="macro-target-bar-pills">
                <span class="macro-target-pill pill-p">
                  <span class="mtp-k">P</span>
                  <span class="mtp-v">${tgtPct.p != null ? tgtPct.p + "%" : "—"}</span>
                  <span class="mtp-g">${tgtG.p || "—"}g</span>
                </span>
                <span class="macro-target-pill pill-c">
                  <span class="mtp-k">C</span>
                  <span class="mtp-v">${tgtPct.c != null ? tgtPct.c + "%" : "—"}</span>
                  <span class="mtp-g">${tgtG.c || "—"}g</span>
                </span>
                <span class="macro-target-pill pill-f">
                  <span class="mtp-k">F</span>
                  <span class="mtp-v">${tgtPct.f != null ? tgtPct.f + "%" : "—"}</span>
                  <span class="mtp-g">${tgtG.f || "—"}g</span>
                </span>
              </div>
              <span class="macro-target-bar-hint">green / red = within ±5pp</span>
            </div>
            <p class="chart-summary-meta">90d span · ${nDays} days with macros · ${rangeTxt} · % of kcal from P×4 / C×4 / F×9</p>
          `;
        }
      }
    }

    destroyChart(hydrationChart);
    if ($("chart-hydration")) {
      const hydDates = hydration.map((h) => h.date);
      const hydVals = hydration.map((h) => h.water_ml);
      const hydRoll7 = rollingAverage(hydVals, 7);
      const hydTrend = linearTrend(hydVals);
      const hSlope = trendSlopePerDay(hydVals);
      const lastHydRoll =
        [...hydRoll7].reverse().find((v) => v != null && !Number.isNaN(v)) ?? null;
      // Weight-based dynamic targets (35 ml/kg, forward-filled weight)
      const weightPts = (data.health && data.health.weight) || [];
      const lbsByDay = weightLbsSeriesForDates(weightPts, hydDates);
      const hydTargetDay = lbsByDay.map((lbs) => hydrationTargetMlFromLbs(lbs));
      const hydTargetRoll7 = rollingAverage(hydTargetDay, 7);
      const todayTarget =
        [...hydTargetDay].reverse().find((v) => v != null && !Number.isNaN(v)) ??
        null;
      const todayLbs =
        [...lbsByDay].reverse().find((v) => v != null && !Number.isNaN(v)) ?? null;
      const lastTgtRoll =
        [...hydTargetRoll7]
          .reverse()
          .find((v) => v != null && !Number.isNaN(v)) ?? null;

      const hydDatasets = [
        {
          type: "bar",
          label: "Water (ml)",
          data: hydVals,
          backgroundColor: "rgba(61,156,240,0.45)",
          borderRadius: 6,
          order: 4,
        },
        {
          type: "line",
          label: "7d rolling avg",
          data: hydRoll7,
          borderColor: "#5ce1a8",
          borderWidth: 2.5,
          pointRadius: 0,
          tension: 0.25,
          spanGaps: true,
          order: 1,
        },
        {
          type: "line",
          label: "Trend",
          data: hydTrend,
          borderColor: "#f0b429",
          borderDash: [6, 4],
          borderWidth: 2,
          pointRadius: 0,
          tension: 0,
          order: 2,
        },
      ];
      if (hydTargetDay.some((v) => v != null)) {
        hydDatasets.push({
          type: "line",
          label: "Day target (35 ml/kg)",
          data: hydTargetDay,
          borderColor: "rgba(192, 132, 252, 0.9)",
          borderDash: [2, 3],
          borderWidth: 1.75,
          pointRadius: 0,
          tension: 0,
          spanGaps: true,
          order: 0,
        });
        hydDatasets.push({
          type: "line",
          label: "7d rolling target",
          data: hydTargetRoll7,
          borderColor: "rgba(240, 113, 120, 0.85)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.2,
          spanGaps: true,
          order: 0,
        });
      }

      hydrationChart = new Chart($("chart-hydration"), {
        data: {
          labels: hydDates,
          datasets: hydDatasets,
        },
        options: chartDefaults(),
      });
      if ($("hydration-trend-note")) {
        const zeroDays = hydVals.filter((v) => v <= 0).length;
        if (lastHydRoll == null && todayTarget == null) {
          $("hydration-trend-note").textContent =
            "Need hydration logs (and weight for a dynamic target).";
        } else {
          const bits = [];
          if (todayTarget != null && todayLbs != null) {
            bits.push(
              `Today target ~${todayTarget.toLocaleString()} ml (35 ml/kg @ ${todayLbs.toFixed(1)} lb)`
            );
          }
          if (lastHydRoll != null) {
            bits.push(`7d intake avg ${Math.round(lastHydRoll).toLocaleString()} ml`);
          }
          if (lastTgtRoll != null) {
            bits.push(`7d target avg ${Math.round(lastTgtRoll).toLocaleString()} ml`);
            if (lastHydRoll != null) {
              const gap = Math.round(lastHydRoll - lastTgtRoll);
              const gapTxt = `${gap > 0 ? "+" : ""}${gap.toLocaleString()}`;
              bits.push(`intake ${gapTxt} ml vs target avg`);
            }
          }
          if (hSlope != null) {
            bits.push(
              `trend ${hSlope >= 0 ? "+" : ""}${Math.round(hSlope * 7)} ml/week`
            );
          }
          if (zeroDays > 0) {
            bits.push(`${zeroDays} day(s) no log = 0 ml`);
          }
          bits.push(`${hydration.length} calendar days`);
          $("hydration-trend-note").textContent = bits.join(" · ");
        }
      }
    }
  }

  function renderStrength(data) {
    const series =
      (selectedExercise && data.strength_trends && data.strength_trends[selectedExercise]) ||
      [];
    const loadVals = series.map((p) => p.best_working_weight);
    const e1rmVals = series.map((p) => p.best_e1rm);
    const loadTrend = linearTrend(loadVals);
    const e1rmTrend = linearTrend(e1rmVals);
    const loadSlope = trendSlopePerDay(loadVals);
    destroyChart(strengthChart);
    strengthChart = new Chart($("chart-strength"), {
      type: "line",
      data: {
        labels: series.map((p) => p.date),
        datasets: [
          {
            label: `${selectedExercise || "Exercise"} best load (lb)`,
            data: loadVals,
            borderColor: "#3d9cf0",
            tension: 0.25,
            pointRadius: 3,
            order: 3,
          },
          {
            label: "Load trend",
            data: loadTrend,
            borderColor: "#5ce1a8",
            borderDash: [6, 4],
            borderWidth: 2.5,
            pointRadius: 0,
            tension: 0,
            order: 1,
          },
          {
            label: "Est. 1RM (Epley)",
            data: e1rmVals,
            borderColor: "#f0b429",
            borderDash: [5, 5],
            tension: 0.25,
            pointRadius: 2,
            order: 4,
          },
          {
            label: "1RM trend",
            data: e1rmTrend,
            borderColor: "#c084fc",
            borderDash: [4, 4],
            borderWidth: 2,
            pointRadius: 0,
            tension: 0,
            order: 2,
          },
        ],
      },
      options: chartDefaults(),
    });
    if ($("strength-trend-note")) {
      const n = series.length;
      const exercises = (data && data.top_exercises) || [];
      let base = exercises.length
        ? `${exercises.length} exercises ranked by log frequency — pick a tab to view trend`
        : "No exercises with strength history yet.";
      if (selectedExercise && n >= 2 && loadSlope != null) {
        const perWeek = loadSlope * 7;
        const dir = perWeek > 0.05 ? "up" : perWeek < -0.05 ? "down" : "flat";
        base = `${selectedExercise}: load trend ${dir} (~${perWeek >= 0 ? "+" : ""}${perWeek.toFixed(2)} lb/week) · ${n} sessions · dashed = linear fit`;
      } else if (selectedExercise && n < 2) {
        base = `${selectedExercise}: need ≥2 sessions for a trendline`;
      }
      $("strength-trend-note").textContent = base;
    }
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

  function filterSuggestionsAgainstInventory(store) {
    const ings = ((store.inventory && store.inventory.ingredients) || []).map((i) => ({
      id: String(i.id || "").toLowerCase(),
      name: String(i.name || "").toLowerCase(),
      stock: i.in_stock !== false,
    }));
    const block = store && store.inventory_suggestions;
    if (block && Array.isArray(block.suggestions)) {
      block.suggestions = block.suggestions.filter((s) => {
        const sid = String(s.id || "").toLowerCase();
        const sname = String(s.name || "").toLowerCase();
        const match = ings.find(
          (i) =>
            (sid && i.id === sid) ||
            (sname && (i.name === sname || i.name.includes(sname) || sname.includes(i.name)))
        );
        if (!match) return true; // still missing → keep "add"
        if (match.stock) return false; // already in stock
        return s.action === "restock";
      });
      block.count = block.suggestions.length;
    }
    const rem = store && store.inventory_removals;
    if (rem && Array.isArray(rem.suggestions)) {
      rem.suggestions = rem.suggestions.filter((s) => {
        const sid = String(s.id || "").toLowerCase();
        const sname = String(s.name || "").toLowerCase();
        return ings.some(
          (i) =>
            (sid && i.id === sid) ||
            (sname && (i.name === sname || i.name.includes(sname) || sname.includes(i.name)))
        );
      });
      rem.count = rem.suggestions.length;
    }
  }

  function applyInventoryUpdate(inventory) {
    if (!state) state = {};
    if (!state.nutrition_store) state.nutrition_store = {};
    state.nutrition_store.inventory = inventory;
    filterSuggestionsAgainstInventory(state.nutrition_store);
    renderInventory(state.nutrition_store);
    renderInventorySuggestions(state.nutrition_store);
    renderInventoryRemovals(state.nutrition_store);
  }

  function invMacroStrip(ing, compact = false) {
    const pct = macroCalPct(ing.protein_g, ing.carbs_g, ing.fat_g);
    if (compact) {
      // Dense one-line macros for carousel cards
      return `
      <div class="inv-macro-strip compact">
        <span class="inv-macro-pill macro-cals">${fmtNum(ing.calories)}</span>
        <span class="inv-macro-pill macro-protein">P${fmtNum(ing.protein_g)}</span>
        <span class="inv-macro-pill macro-carbs">C${fmtNum(ing.carbs_g)}</span>
        <span class="inv-macro-pill macro-fat">F${fmtNum(ing.fat_g)}</span>
      </div>`;
    }
    const pPct = pct.p != null ? ` · ${pct.p}%` : "";
    const cPct = pct.c != null ? ` · ${pct.c}%` : "";
    const fPct = pct.f != null ? ` · ${pct.f}%` : "";
    return `
      <div class="inv-macro-strip">
        <span class="inv-macro-pill macro-cals"><span class="pill-k">Cal</span> ${fmtNum(ing.calories)}</span>
        <span class="inv-macro-pill macro-protein"><span class="pill-k">P</span> ${fmtNum(ing.protein_g)}g${pPct}</span>
        <span class="inv-macro-pill macro-carbs"><span class="pill-k">C</span> ${fmtNum(ing.carbs_g)}g${cPct}</span>
        <span class="inv-macro-pill macro-fat"><span class="pill-k">F</span> ${fmtNum(ing.fat_g)}g${fPct}</span>
      </div>`;
  }

  function invCarouselShell(id, slidesHtml, emptyMsg) {
    if (!slidesHtml) {
      return `<p class="muted inv-carousel-empty">${emptyMsg || "Nothing here yet."}</p>`;
    }
    return `
      <div class="inv-carousel-shell" data-carousel-id="${id}">
        <button type="button" class="inv-carousel-nav prev" data-action="carousel-nav" data-carousel="${id}" data-dir="-1" aria-label="Previous">‹</button>
        <div class="inv-carousel" id="${id}" tabindex="0">
          <div class="inv-carousel-track">
            ${slidesHtml}
          </div>
        </div>
        <button type="button" class="inv-carousel-nav next" data-action="carousel-nav" data-carousel="${id}" data-dir="1" aria-label="Next">›</button>
      </div>`;
  }

  /** Local clock for a meal bucket (server eat_at is America/New_York-aware ISO). */
  function mealBucketClock(m) {
    if (m && m.eat_at) {
      const clock = fmtBarWhen(m.eat_at);
      if (clock && clock !== "—") return clock;
    }
    return String((m && m.eat_at_label) || "").trim();
  }

  /** Prefer weighable grams on meal-plan / inventory lines (food scale). */
  function formatPlanPortion(it) {
    if (!it) return "1 serving";
    const pg = Number(it.portion_g);
    if (Number.isFinite(pg) && pg > 0) return `${Math.round(pg)}g`;
    const sg = Number(it.serving_g);
    const n = Number(it.servings) || 1;
    if (Number.isFinite(sg) && sg > 0) return `${Math.round(sg * n)}g`;
    const label = String(it.serving_label || "1 serving").trim() || "1 serving";
    // Server already collapsed multi-servings into a total gram label (e.g. "510g").
    if (/^\d+(\.\d+)?g\b/i.test(label)) return label;
    if (n > 1) return `${n} × ${label}`;
    return label;
  }

  function formatInventoryPortion(ing) {
    if (!ing) return "1 serving";
    const sg = Number(ing.serving_g);
    if (Number.isFinite(sg) && sg > 0) {
      const label = String(ing.serving_label || "").trim();
      if (label && /g\b/i.test(label)) return label;
      return `${Math.round(sg)}g`;
    }
    return String(ing.serving_label || "1 serving").trim() || "1 serving";
  }

  /** Parse N g / N oz from a Health or inventory label. Never invents mass. */
  function parseGramsFromLabel(label) {
    const text = String(label || "");
    const g = text.match(/([\d.]+)\s*g\b/i);
    if (g) {
      const v = Number(g[1]);
      return Number.isFinite(v) && v > 0 ? v : null;
    }
    const oz = text.match(/([\d.]+)\s*oz\b/i);
    if (oz) {
      const v = Number(oz[1]) * 28.3495;
      return Number.isFinite(v) && v > 0 ? Math.round(v) : null;
    }
    return null;
  }

  function usableServingGrams(it) {
    if (!it) return null;
    const sg = Number(it.serving_g);
    if (Number.isFinite(sg) && sg > 0) return sg;
    return parseGramsFromLabel(it.serving_label);
  }

  function itemHasMacros(it) {
    if (!it) return false;
    return ["calories", "protein_g", "carbs_g", "fat_g"].some((k) => Number(it[k]) > 0);
  }

  /** Macros present, no usable serving_g / parseable N g or N oz. */
  function itemNeedsServingGrams(it) {
    return !!(it && itemHasMacros(it) && usableServingGrams(it) == null);
  }

  /** Health logged-serving / generic “1 serving” must not save without user grams. */
  function servingGramsRequired(it) {
    if (!itemNeedsServingGrams(it)) return false;
    const label = String((it && it.serving_label) || "").trim();
    if (!label) return false;
    if (/logged\s+serving/i.test(label)) return true;
    return /^(?:\d+(?:\.\d+)?\s+)?(?:logged\s+)?servings?\s*(?:\([^)]*\))?$/i.test(label);
  }

  function servingGramsPrompt(it) {
    const name = String((it && it.name) || "this food").trim() || "this food";
    const label = String((it && it.serving_label) || "").trim();
    if (/logged\s+serving/i.test(label)) {
      return `Set serving grams for ${name} — Health “logged serving” has no weighable mass.`;
    }
    return `Set serving grams on ${name} to get weighable portions.`;
  }

  function invEscapeAttr(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  function inventoryEditNote(ing) {
    const sg = Number(ing && ing.serving_g);
    const gVal = Number.isFinite(sg) && sg > 0 ? String(Math.round(sg)) : "";
    let note = String((ing && ing.serving_label) || "").trim();
    if (!note || note.toLowerCase() === "1 serving") return "";
    if (gVal && (note === `${gVal}g` || note.toLowerCase() === `${gVal}g`)) return "";
    if (gVal && note.toLowerCase().startsWith(`${gVal}g`)) {
      return note.slice(String(`${gVal}g`).length).replace(/^[\s·,-]+/, "").trim();
    }
    return note;
  }

  function cancelInventoryEdit() {
    if (!inventoryEditId) return;
    inventoryEditId = null;
    renderInventory(state && state.nutrition_store);
  }

  function collectInventoryEdit(card) {
    const get = (field) => {
      const el = card && card.querySelector(`[data-edit-field="${field}"]`);
      return el ? String(el.value || "").trim() : "";
    };
    const servingGRaw = get("serving_g");
    const servingG = servingGRaw === "" ? null : Number(servingGRaw);
    const body = {
      id: String((card && card.getAttribute("data-ing-id")) || "").trim(),
      name: get("name"),
      category: get("category") || "other",
      serving_label: get("serving_label"),
      calories: Number(get("calories")),
      protein_g: Number(get("protein_g")),
      carbs_g: Number(get("carbs_g")),
      fat_g: Number(get("fat_g")),
    };
    if (Number.isFinite(servingG) && servingG > 0) {
      body.serving_g = servingG;
      if (!body.serving_label) body.serving_label = `${Math.round(servingG)}g`;
    } else if (!body.serving_label) {
      body.serving_label = "1 serving";
    }
    return body;
  }

  function assertServingGramsOrExplain(body) {
    if (!itemNeedsServingGrams(body)) return "";
    return servingGramsPrompt(body);
  }

  function inventoryEditFormHtml(ing) {
    const iid = invEscapeAttr(ing.id || "");
    const cat = String(ing.category || "other");
    const sg = Number(ing.serving_g);
    const gVal = Number.isFinite(sg) && sg > 0 ? String(Math.round(sg)) : "";
    const cats = ["protein", "carb", "veg", "fat", "other"];
    const opts = cats
      .map((c) => {
        const label = c.charAt(0).toUpperCase() + c.slice(1);
        return `<option value="${c}"${c === cat ? " selected" : ""}>${label}</option>`;
      })
      .join("");
    return `
      <form class="inv-edit-form" data-ing-id="${iid}">
        <div class="inv-edit-row">
          <label>Name <input type="text" data-edit-field="name" value="${invEscapeAttr(
            ing.name || ""
          )}" required /></label>
          <label>Category <select data-edit-field="category">${opts}</select></label>
          <label>Portion (g) <input type="number" data-edit-field="serving_g" min="1" step="1" value="${invEscapeAttr(
            gVal
          )}"${itemNeedsServingGrams(ing) ? " required" : ""} /></label>
        </div>
        ${
          itemNeedsServingGrams(ing)
            ? `<p class="inv-grams-prompt">${invEscapeAttr(servingGramsPrompt(ing))}</p>`
            : ""
        }
        <label class="inv-edit-note">Note <input type="text" data-edit-field="serving_label" value="${invEscapeAttr(
          inventoryEditNote(ing)
        )}" placeholder="e.g. cooked" /></label>
        <div class="inv-edit-row macros">
          <label>Cal <input type="number" data-edit-field="calories" min="0" step="1" value="${invEscapeAttr(
            ing.calories
          )}" required /></label>
          <label>Protein (g) <input type="number" data-edit-field="protein_g" min="0" step="0.1" value="${invEscapeAttr(
            ing.protein_g
          )}" required /></label>
          <label>Carbs (g) <input type="number" data-edit-field="carbs_g" min="0" step="0.1" value="${invEscapeAttr(
            ing.carbs_g
          )}" required /></label>
          <label>Fat (g) <input type="number" data-edit-field="fat_g" min="0" step="0.1" value="${invEscapeAttr(
            ing.fat_g
          )}" required /></label>
        </div>
        <div class="actions inv-card-actions">
          <button type="submit" class="primary" data-action="edit-save" data-id="${iid}">Save</button>
          <button type="button" data-action="edit-cancel" data-id="${iid}">Cancel</button>
        </div>
      </form>`;
  }

  function renderInventory(store) {
    const list = $("inventory-list");
    if (!list) return;
    const items = ((store && store.inventory && store.inventory.ingredients) || []).slice();
    items.sort((a, b) => {
      const sa = a.in_stock === false ? 1 : 0;
      const sb = b.in_stock === false ? 1 : 0;
      if (sa !== sb) return sa - sb;
      return String(a.name).localeCompare(String(b.name));
    });
    if (!items.length) {
      list.innerHTML = `<div class="macro-summary inv-panel compact-panel">
        <div class="macro-summary-title">Current inventory</div>
        <p class="muted" style="margin:0.35rem 0 0">No ingredients yet — add above or accept a suggestion below.</p>
      </div>`;
      return;
    }
    const stocked = items.filter((i) => i.in_stock !== false).length;
    let cards = "";
    items.forEach((ing) => {
      const stock = ing.in_stock !== false;
      const iid = invEscapeAttr(ing.id || "");
      const iname = invEscapeAttr(ing.name || "");
      const rawId = String(ing.id || "");
      const editing = inventoryEditId && rawId && inventoryEditId === rawId;
      if (editing) {
        cards += `<div class="inv-card editing${stock ? "" : " out"}" data-ing-id="${iid}">
          ${inventoryEditFormHtml(ing)}
        </div>`;
        return;
      }
      cards += `<div class="inv-card${stock ? "" : " out"}" data-ing-id="${iid}">
        <div class="inv-card-name">${ing.name || "Ingredient"}${
        stock ? "" : ' <span class="inv-out-badge">out</span>'
      }</div>
        <div class="inv-card-meta muted">${ing.category || "other"} · ${formatInventoryPortion(
        ing
      )}</div>
        ${invMacroStrip(ing, false)}
        <div class="actions inv-card-actions">
          <button type="button" class="btn-edit" data-action="edit" data-id="${iid}" data-name="${iname}">Edit</button>
          <button type="button" class="btn-stock" data-action="stock" data-id="${iid}" data-name="${iname}" data-stock="${stock ? "0" : "1"}">
            ${stock ? "Mark out" : "Mark in stock"}
          </button>
          <button type="button" class="btn-remove" data-action="remove" data-id="${iid}" data-name="${iname}">Remove</button>
        </div>
      </div>`;
    });
    list.innerHTML = `<div class="macro-summary inv-panel compact-panel inv-panel-fill">
      <div class="macro-summary-header">
        <div>
          <div class="macro-summary-title">Current inventory</div>
          <div class="macro-summary-meta muted">${stocked} in · ${items.length - stocked} out · scroll</div>
        </div>
        <div class="inv-carousel-count muted">${items.length}</div>
      </div>
      <div class="inv-cards">${cards}</div>
    </div>`;
  }

  function renderInventorySuggestions(store) {
    const box = $("inventory-suggestions");
    if (!box) return;
    const block = (store && store.inventory_suggestions) || {};
    const items = block.suggestions || [];
    if (!items.length) {
      box.innerHTML = "";
      return;
    }
    let slides = "";
    items.forEach((s, idx) => {
      const action = s.action === "restock" ? "restock" : "add";
      const label = action === "restock" ? "Restock" : "Add";
      const payload = encodeURIComponent(
        JSON.stringify({
          id: s.id,
          name: s.name,
          category: s.category,
          serving_label: s.serving_label,
          serving_g: s.serving_g,
          calories: s.calories,
          protein_g: s.protein_g,
          carbs_g: s.carbs_g,
          fat_g: s.fat_g,
          in_stock: true,
          notes: s.notes || "",
        })
      );
      const reason = String(s.reason || "").slice(0, 90);
      slides += `<div class="inv-slide inv-card compact suggest">
        <div class="inv-card-name">${s.name || "Staple"}
          <span class="inv-action-badge inv-action-${action}">${action}</span>
        </div>
        <div class="inv-card-meta muted">${s.category || "other"} · ${
        formatInventoryPortion(s)
      }</div>
        ${
          itemNeedsServingGrams(s)
            ? `<p class="inv-grams-prompt compact">${invEscapeAttr(servingGramsPrompt(s))}</p>`
            : ""
        }
        ${reason ? `<div class="inv-reason compact" title="${String(s.reason || "").replace(/"/g, "&quot;")}">${reason}${String(s.reason || "").length > 90 ? "…" : ""}</div>` : ""}
        ${invMacroStrip(s, true)}
        <div class="actions inv-card-actions compact">
          <button type="button" class="primary btn-suggest-apply" data-action="suggest-apply"
            data-suggest-action="${action}" data-id="${String(s.id || "").replace(/"/g, "&quot;")}"
            data-payload="${payload}" data-idx="${idx}">
            ${label}
          </button>
        </div>
      </div>`;
    });
    box.innerHTML = `<div class="macro-summary inv-suggest-panel compact-panel">
      <div class="macro-summary-header">
        <div>
          <div class="macro-summary-title">Suggested staples</div>
          <div class="macro-summary-meta muted">${block.summary || "Based on logs, gaps, and catalog"}</div>
        </div>
        <div class="inv-carousel-count muted">${items.length}</div>
      </div>
      ${invCarouselShell("suggest-carousel", slides)}
    </div>`;
  }

  function renderInventoryRemovals(store) {
    const box = $("inventory-removals");
    if (!box) return;
    const block = (store && store.inventory_removals) || {};
    const items = block.suggestions || [];
    if (!items.length) {
      box.innerHTML = "";
      return;
    }
    let slides = "";
    items.forEach((s, idx) => {
      const iid = String(s.id || "").replace(/"/g, "&quot;");
      const iname = String(s.name || "").replace(/"/g, "&quot;");
      const reason = String(s.reason || "").slice(0, 110);
      slides += `<div class="inv-slide inv-card compact suggest-remove">
        <div class="inv-card-name">${s.name || "Item"}
          <span class="inv-action-badge inv-action-remove">remove</span>
        </div>
        <div class="inv-card-meta muted">${s.category || "other"} · ${s.serving_label || "1 serving"}</div>
        ${
          reason
            ? `<div class="inv-reason compact" title="${String(s.reason || "").replace(/"/g, "&quot;")}">${reason}${
                String(s.reason || "").length > 110 ? "…" : ""
              }</div>`
            : ""
        }
        ${invMacroStrip(s, true)}
        <div class="actions inv-card-actions compact">
          <button type="button" class="btn-suggest-remove" data-action="suggest-remove"
            data-id="${iid}" data-name="${iname}" data-idx="${idx}">
            Remove
          </button>
        </div>
      </div>`;
    });
    box.innerHTML = `<div class="macro-summary inv-remove-panel compact-panel">
      <div class="macro-summary-header">
        <div>
          <div class="macro-summary-title">Suggested removals</div>
          <div class="macro-summary-meta muted">${block.summary || "Items that may not help your plan"}</div>
        </div>
        <div class="inv-carousel-count muted">${items.length}</div>
      </div>
      ${invCarouselShell("remove-carousel", slides)}
    </div>`;
  }

  function dropAppliedSuggestion(idx) {
    if (state && state.nutrition_store && state.nutrition_store.inventory_suggestions) {
      const sug = state.nutrition_store.inventory_suggestions;
      if (Array.isArray(sug.suggestions) && !Number.isNaN(idx)) {
        sug.suggestions = sug.suggestions.filter((_, i) => i !== idx);
        sug.count = sug.suggestions.length;
      }
      renderInventorySuggestions(state.nutrition_store);
    }
  }

  function showSuggestionGramsPrompt(slide, body, suggestAction, idx) {
    if (!slide) return false;
    const host = slide.querySelector(".inv-card-actions");
    if (!host) return false;
    const payload = encodeURIComponent(JSON.stringify(body));
    const action = suggestAction === "restock" ? "restock" : "add";
    const wrap = document.createElement("div");
    wrap.innerHTML = `<form class="inv-grams-form">
        <p class="inv-grams-prompt">${invEscapeAttr(servingGramsPrompt(body))}</p>
        <label>Portion (g)
          <input type="number" data-grams-field="serving_g" min="1" step="1" required placeholder="e.g. 170" />
        </label>
        <div class="actions inv-card-actions compact">
          <button type="submit" class="primary" data-action="suggest-grams-save"
            data-suggest-action="${action}" data-idx="${idx}" data-payload="${payload}">Save</button>
          <button type="button" data-action="suggest-grams-cancel">Cancel</button>
        </div>
      </form>`;
    host.replaceWith(wrap.firstElementChild);
    const input = slide.querySelector("[data-grams-field=serving_g]");
    if (input) input.focus();
    return true;
  }

  async function persistInventorySuggestion(body, suggestAction, setGrams) {
    const name = (body && body.name) || "";
    if (setGrams && suggestAction === "restock" && body.id) {
      const res = await fetch("/api/inventory/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, in_stock: true }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        const res2 = await fetch("/api/inventory/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data2 = await res2.json().catch(() => ({}));
        if (!res2.ok || !data2.ok) throw new Error(data2.error || data.error || res.status);
        applyInventoryUpdate(data2.inventory);
      } else {
        applyInventoryUpdate(data.inventory);
      }
      showAlert(`Restocked ${name || body.id}`, "ok");
      return;
    }
    if (suggestAction === "restock" && body.id) {
      const res = await fetch("/api/inventory/stock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: body.id, in_stock: true }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        const res2 = await fetch("/api/inventory/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data2 = await res2.json().catch(() => ({}));
        if (!res2.ok || !data2.ok) throw new Error(data2.error || data.error || res.status);
        applyInventoryUpdate(data2.inventory);
      } else {
        applyInventoryUpdate(data.inventory);
      }
      showAlert(`Restocked ${name || body.id}`, "ok");
      return;
    }
    const res = await fetch("/api/inventory/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    applyInventoryUpdate(data.inventory);
    showAlert(`Added ${name || body.id} to inventory`, "ok");
  }

  /** One delegated listener — survives re-renders and avoids dead buttons. */
  function bindInventoryListOnce() {
    // Cover inventory + meal plan so shared carousel arrows work in both columns
    const root = $("inventory-section") || $("inventory-card") || document;
    if (root.dataset && root.dataset.invBound === "1") return;
    if (root.dataset) root.dataset.invBound = "1";
    root.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      if (!inventoryEditId) return;
      ev.preventDefault();
      cancelInventoryEdit();
    });
    root.addEventListener("submit", (ev) => {
      const gramsForm = ev.target.closest && ev.target.closest(".inv-grams-form");
      if (gramsForm && root.contains(gramsForm)) {
        ev.preventDefault();
        const saveBtn = gramsForm.querySelector('[data-action="suggest-grams-save"]');
        if (saveBtn) saveBtn.click();
        return;
      }
      const form = ev.target.closest && ev.target.closest(".inv-edit-form");
      if (!form || !root.contains(form)) return;
      ev.preventDefault();
      const saveBtn = form.querySelector('[data-action="edit-save"]');
      if (saveBtn) saveBtn.click();
    });
    root.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("button[data-action]");
      if (!btn || !root.contains(btn)) return;
      const action = btn.getAttribute("data-action");

      // Carousel navigation — horizontal (pantry/items) or vertical (planned meals)
      if (action === "carousel-nav") {
        ev.preventDefault();
        ev.stopPropagation();
        const cid = btn.getAttribute("data-carousel");
        const dir = Number(btn.getAttribute("data-dir") || 1);
        const scroller = cid ? document.getElementById(cid) : null;
        if (scroller) {
          const axis =
            btn.getAttribute("data-axis") ||
            scroller.getAttribute("data-axis") ||
            "x";
          if (axis === "y") {
            const step = Math.max(120, Math.floor(scroller.clientHeight * 0.92));
            scroller.scrollBy({ top: dir * step, behavior: "smooth" });
          } else {
            const step = Math.max(180, Math.floor(scroller.clientWidth * 0.85));
            scroller.scrollBy({ left: dir * step, behavior: "smooth" });
          }
        }
        return;
      }

      // Inventory actions only on inventory card
      const invCard = $("inventory-card");
      if (invCard && !invCard.contains(btn)) return;

      ev.preventDefault();
      ev.stopPropagation();
      const id = (btn.getAttribute("data-id") || "").trim();
      const name = (btn.getAttribute("data-name") || "").trim();
      if (action === "edit") {
        if (!id) {
          showAlert("Edit failed: missing ingredient id", "err");
          return;
        }
        inventoryEditId = id;
        renderInventory(state && state.nutrition_store);
        const card = Array.from(root.querySelectorAll(".inv-card.editing")).find(
          (el) => el.getAttribute("data-ing-id") === id
        );
        const first = card && card.querySelector("[data-edit-field=name]");
        if (first) first.focus();
        return;
      }
      if (action === "edit-cancel") {
        cancelInventoryEdit();
        return;
      }
      btn.disabled = true;
      try {
        if (action === "suggest-remove") {
          const res = await fetch("/api/inventory/remove", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id, name }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) {
            throw new Error(data.error || `HTTP ${res.status}`);
          }
          applyInventoryUpdate(data.inventory);
          showAlert(`Removed ${name || id} from inventory`, "ok");
          if (state && state.nutrition_store && state.nutrition_store.inventory_removals) {
            const rem = state.nutrition_store.inventory_removals;
            const idx = Number(btn.getAttribute("data-idx"));
            if (Array.isArray(rem.suggestions) && !Number.isNaN(idx)) {
              rem.suggestions = rem.suggestions.filter((_, i) => i !== idx);
              rem.count = rem.suggestions.length;
            }
            renderInventoryRemovals(state.nutrition_store);
          }
          try {
            await generatePlan();
          } catch (_) {
            /* optional */
          }
          return;
        }
        if (action === "suggest-grams-cancel") {
          btn.disabled = false;
          if (state) renderInventorySuggestions(state.nutrition_store);
          return;
        }
        if (action === "suggest-grams-save") {
          const form = btn.closest(".inv-grams-form");
          const gramsEl = form && form.querySelector("[data-grams-field=serving_g]");
          const grams = gramsEl ? Number(String(gramsEl.value || "").trim()) : NaN;
          const payloadRaw = btn.getAttribute("data-payload") || "{}";
          let body;
          try {
            body = JSON.parse(decodeURIComponent(payloadRaw));
          } catch (_) {
            throw new Error("bad suggestion payload");
          }
          if (!Number.isFinite(grams) || grams <= 0) {
            if (gramsEl) gramsEl.focus();
            throw new Error(servingGramsPrompt(body));
          }
          body.serving_g = grams;
          const suggestAction = btn.getAttribute("data-suggest-action") || "add";
          await persistInventorySuggestion(body, suggestAction, true);
          dropAppliedSuggestion(Number(btn.getAttribute("data-idx")));
          try {
            await generatePlan();
          } catch (_) {
            /* optional */
          }
          return;
        }
        if (action === "suggest-apply") {
          const payloadRaw = btn.getAttribute("data-payload") || "{}";
          let body;
          try {
            body = JSON.parse(decodeURIComponent(payloadRaw));
          } catch (_) {
            throw new Error("bad suggestion payload");
          }
          const suggestAction = btn.getAttribute("data-suggest-action") || "add";
          if (itemNeedsServingGrams(body)) {
            btn.disabled = false;
            const slide = btn.closest(".inv-slide") || btn.closest(".inv-card");
            showSuggestionGramsPrompt(
              slide,
              body,
              suggestAction,
              Number(btn.getAttribute("data-idx"))
            );
            showAlert(servingGramsPrompt(body), "warn");
            return;
          }
          await persistInventorySuggestion(body, suggestAction, false);
          dropAppliedSuggestion(Number(btn.getAttribute("data-idx")));
          try {
            await generatePlan();
          } catch (_) {
            /* optional */
          }
          return;
        }
        if (action === "edit-save") {
          const card = btn.closest(".inv-card");
          const body = collectInventoryEdit(card);
          if (!body.id) {
            throw new Error("missing ingredient id");
          }
          if (!body.name) {
            throw new Error("ingredient name required");
          }
          const gramsErr = assertServingGramsOrExplain(body);
          if (gramsErr) {
            const gramsInput = card && card.querySelector('[data-edit-field="serving_g"]');
            if (gramsInput) gramsInput.focus();
            throw new Error(gramsErr);
          }
          const res = await fetch("/api/inventory/update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) {
            throw new Error(data.error || `HTTP ${res.status}`);
          }
          inventoryEditId = null;
          applyInventoryUpdate(data.inventory);
          showAlert(`Updated ${body.name}`, "ok");
          try {
            await generatePlan();
          } catch (_) {
            /* optional */
          }
          return;
        }
        if (!id && !name) {
          showAlert("Action failed: missing ingredient id", "err");
          return;
        }
        if (action === "remove") {
          const res = await fetch("/api/inventory/remove", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id, name }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) {
            throw new Error(data.error || `HTTP ${res.status}`);
          }
          applyInventoryUpdate(data.inventory);
          showAlert(`Removed ${name || id}`, "ok");
          try {
            await generatePlan();
          } catch (_) {
            /* optional */
          }
        } else if (action === "stock") {
          const in_stock = btn.getAttribute("data-stock") === "1";
          const res = await fetch("/api/inventory/stock", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id, in_stock }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) {
            throw new Error(data.error || `HTTP ${res.status}`);
          }
          applyInventoryUpdate(data.inventory);
          showAlert(
            in_stock ? `Marked ${name || id} in stock` : `Marked ${name || id} out of stock`,
            "ok"
          );
          try {
            await generatePlan();
          } catch (_) {
            /* optional */
          }
        }
      } catch (e) {
        showAlert(`Inventory action failed: ${e.message}`, "err");
        btn.disabled = false;
      }
    });
  }

  function macroCalPct(protein_g, carbs_g, fat_g) {
    const p = Number(protein_g) || 0;
    const c = Number(carbs_g) || 0;
    const f = Number(fat_g) || 0;
    const tot = p * 4 + c * 4 + f * 9;
    if (tot <= 0) return { p: null, c: null, f: null };
    return {
      p: Math.round((p * 4 * 1000) / tot) / 10,
      c: Math.round((c * 4 * 1000) / tot) / 10,
      f: Math.round((f * 9 * 1000) / tot) / 10,
    };
  }

  /**
   * Macro calorie-share band: ±5 percentage points around target share.
   * (e.g. target 40.3% → in range 35.3–45.3%. Relative ±5% of 40.3 would be
   * a ~2pp band and is too tight for daily noise.)
   */
  function macroTargetBandClass(value, target) {
    const v = Number(value);
    const t = Number(target);
    if (value == null || target == null || Number.isNaN(v) || Number.isNaN(t) || t <= 0) {
      return "";
    }
    const lo = t - 5;
    const hi = t + 5;
    return v >= lo && v <= hi ? "chip-in-range" : "chip-out-of-range";
  }

  function targetPct(consumed, target) {
    const t = Number(target) || 0;
    if (t <= 0) return null;
    return Math.min(999, Math.round(((Number(consumed) || 0) * 1000) / t) / 10);
  }

  function macroChip(kind, label, grams, calPct) {
    const pct =
      calPct != null && !Number.isNaN(calPct) ? ` · ${calPct}%` : "";
    return `<div class="macro-chip macro-${kind}">
      <span class="macro-chip-label">${label}</span>
      <span class="macro-chip-value">${fmtNum(grams)} g<span class="macro-chip-pct">${pct}</span></span>
    </div>`;
  }

  /**
   * Today "Today so far" row: day totals + pace-relative center bar.
   * pace = server pace_vs_expected payload (band green|yellow|red, side, bar_pct).
   * Center = on pace for this point in the eating window (not day-empty→full).
   */
  function progressRow(label, consumed, target, kind, pace) {
    const pct = targetPct(consumed, target);
    const p = pace || null;
    const band = (p && p.band) || (pct != null && pct > 120 ? "red" : pct != null && pct > 105 ? "yellow" : "green");
    const side = (p && p.side) || "on";
    const barPct = Math.max(0, Math.min(100, Number(p && p.bar_pct) || 0));
    const halfW = barPct * 0.5;
    const leftW = side === "behind" ? halfW : 0;
    const rightW = side === "ahead" ? halfW : 0;
    const unit = kind === "cals" || kind === "calories" ? "kcal" : "g";
    const paced = p && p.paced_expected != null ? fmtNum(p.paced_expected) : "—";
    let paceHint = "";
    let paceTitle = (p && p.summary) || `${label} vs pace`;
    if (p && p.status !== "no_target" && p.paced_expected != null) {
      // pace now = expected by this time in the eating window
      // signed delta = consumed − expected (positive = ahead of pace)
      const d = Number(p.delta_vs_pace);
      const dTxt = Number.isFinite(d)
        ? d > 0
          ? `+${fmtNum(d)}`
          : fmtNum(d)
        : "";
      const rel =
        side === "ahead" ? "ahead" : side === "behind" ? "behind" : "on pace";
      paceHint = dTxt
        ? ` · pace now ${paced}${unit} · <span class="pace-delta">${dTxt} ${rel}</span>`
        : ` · pace now ${paced}${unit}`;
      paceTitle = `Pace now ${paced} ${unit} = day target × fraction of eating window elapsed. ${
        dTxt ? `${dTxt} ${unit} = intake minus that expected (${rel}).` : "On pace."
      }`;
    }
    return `<div class="macro-progress-row">
      <div class="macro-progress-meta">
        <span class="macro-progress-label">${label}</span>
        <span class="macro-progress-nums">${fmtNum(consumed)} / ${fmtNum(target)}${
      pct != null ? ` · <strong>${pct}%</strong>` : ""
    }${paceHint}</span>
      </div>
      <div class="macro-pace-track band-${band}" role="img" aria-label="${label} pace ${side} ${band}" title="${paceTitle.replace(
      /"/g,
      "&quot;"
    )}">
        <div class="macro-pace-mid" aria-hidden="true"></div>
        <div class="macro-pace-fill macro-pace-left band-${band}" style="width:${leftW}%"></div>
        <div class="macro-pace-fill macro-pace-right band-${band}" style="width:${rightW}%"></div>
        <div class="macro-pace-labels" aria-hidden="true">
          <span>behind</span><span>on pace</span><span>ahead</span>
        </div>
      </div>
    </div>`;
  }

  function fillMacroSplit(el, todayVal, targetVal, unit, todayPct, targetPct) {
    if (!el) return;
    const hasToday = todayVal != null && !Number.isNaN(Number(todayVal));
    const hasTarget = targetVal != null && !Number.isNaN(Number(targetVal));
    const t = hasToday ? Number(todayVal) : null;
    const g = hasTarget ? Number(targetVal) : null;
    const unitSuf = unit ? ` ${unit}` : "";
    // % of total calories from this macro (P×4 / C×4 / F×9 basis)
    const pctBit = (pct) =>
      pct != null && !Number.isNaN(Number(pct))
        ? `<span class="macro-split-pct"> · ${pct}%</span>`
        : "";
    let deltaHtml = "";
    if (t != null && g != null && g > 0) {
      const left = g - t;
      if (Math.abs(left) < 0.05) {
        deltaHtml = `<div class="macro-split-delta">on target</div>`;
      } else if (left > 0) {
        deltaHtml = `<div class="macro-split-delta under">${fmtNum(left)}${unitSuf} left</div>`;
      } else {
        deltaHtml = `<div class="macro-split-delta over">+${fmtNum(-left)}${unitSuf} over</div>`;
      }
    }
    el.innerHTML = `
      <div class="macro-split-half">
        <div class="macro-split-k">Today</div>
        <div class="macro-split-v">${
          hasToday ? fmtNum(t) + unitSuf + pctBit(todayPct) : "—"
        }</div>
      </div>
      <div class="macro-split-rule" aria-hidden="true"></div>
      <div class="macro-split-half">
        <div class="macro-split-k">Target</div>
        <div class="macro-split-v">${
          hasTarget ? fmtNum(g) + unitSuf + pctBit(targetPct) : "—"
        }</div>
        ${deltaHtml}
      </div>`;
  }

  function renderNutritionStatTiles(store) {
    const t = (store && store.targets) || {};
    const c = (store && store.today_consumed) || {};
    // Calorie share % of total macro kcal (same basis as macro chips / chart)
    const soFarPct = macroCalPct(c.protein_g, c.carbs_g, c.fat_g);
    const tgtPct = macroCalPct(t.protein_g, t.carbs_g, t.fat_g);
    // Calories tile: show % of daily calorie target on Today side
    const calHit = targetPct(c.calories, t.calories);
    fillMacroSplit($("stat-calories"), c.calories, t.calories, "", calHit, null);
    fillMacroSplit(
      $("stat-protein"),
      c.protein_g,
      t.protein_g,
      "g",
      soFarPct.p,
      tgtPct.p
    );
    fillMacroSplit(
      $("stat-carbs"),
      c.carbs_g,
      t.carbs_g,
      "g",
      soFarPct.c,
      tgtPct.c
    );
    fillMacroSplit($("stat-fat"), c.fat_g, t.fat_g, "g", soFarPct.f, tgtPct.f);
    updateMacroStrip(c, t, state && state.recovery);
  }

  function fmtNumShort(n) {
    if (n == null || n === "" || Number.isNaN(Number(n))) return "—";
    const x = Number(n);
    return Number.isInteger(x) ? String(x) : x.toFixed(0);
  }

  /** Sticky macro strip removed (product: eyesore / low value). Keep no-op for callers. */
  function updateMacroStrip(_consumed, _targets, _recovery) {
    /* intentionally empty */
  }

  function syncMacroStripVisibility() {
    /* strip removed */
  }

  function fmtBarWhen(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso).slice(11, 16) || "—";
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (_) {
      return "—";
    }
  }

  /** Render hydration pacing bar (same wake window as calorie eating window). */
  function renderHydrationBars(data) {
    const bars = (data && data.hydration_bars) || {};
    const pacing = bars.pacing || null;
    const fill = $("hydration-pacing-fill");
    const marker = $("hydration-pacing-marker");
    const paceSum = $("hydration-pacing-summary");
    const paceMeta = $("hydration-pacing-meta");
    if (fill && pacing) {
      const pacedStatuses = {
        on_pace: true,
        ahead: true,
        behind: true,
        start: true,
      };
      const sipAware =
        pacing.sip_aware === true ||
        (pacing.sip_aware !== false &&
          pacing.intake_source &&
          pacing.intake_source !== "none" &&
          pacedStatuses[pacing.status]);
      const rawStatus = pacing.status || "unknown";
      const status =
        sipAware && rawStatus !== "unknown" ? rawStatus : "unknown";
      const pct =
        status === "unknown"
          ? 0
          : Math.max(0, Math.min(100, Number(pacing.fill_pct) || 0));
      const exp = Math.max(0, Math.min(100, Number(pacing.expected_pct) || 0));
      fill.style.width = `${pct}%`;
      const band = status === "unknown" ? "muted" : pacing.band || "";
      fill.className = band
        ? `pace-fill band-${band} ${status}`
        : `pace-fill ${status}`;
      if (marker) marker.style.left = `${exp}%`;
      if (paceSum) {
        let sum = pacing.summary || "—";
        const consumed =
          pacing.consumed_ml != null ? pacing.consumed_ml : pacing.consumed;
        const target =
          pacing.target_ml != null ? pacing.target_ml : pacing.target;
        const paced =
          pacing.paced_budget_ml != null
            ? pacing.paced_budget_ml
            : pacing.paced_budget;
        if (status === "unknown") {
          sum =
            pacing.summary ||
            "No sip timestamps — wake-window pace unknown.";
        } else if (pacedStatuses[status] && target != null) {
          // Prefer concise pace-now line for the athlete glance
          const delta = Number(pacing.delta_vs_pace);
          let paceLine = `pace now ${fmtNum(paced)} ml`;
          if (Number.isFinite(delta)) {
            if (Math.abs(delta) < 1) paceLine += " · on pace";
            else if (delta > 0) paceLine += ` · +${fmtNum(delta)} ahead`;
            else paceLine += ` · ${fmtNum(delta)} behind`;
          }
          paceLine += ` · ${fmtNum(consumed)} / ${fmtNum(target)} ml day`;
          sum = paceLine;
        }
        paceSum.textContent = sum;
      }
      const win = pacing.window || bars.window || {};
      if (paceMeta) {
        const bits = [];
        if (win.window_start)
          bits.push(`wake ${fmtBarWhen(win.window_start)}`);
        if (win.window_end) bits.push(`bed ~${fmtBarWhen(win.window_end)}`);
        if (win.fraction != null)
          bits.push(`${Math.round(Number(win.fraction) * 100)}% of window`);
        if (pacing.paced_budget_ml != null || pacing.paced_budget != null)
          bits.push(
            `paced ~${fmtNum(
              pacing.paced_budget_ml != null
                ? pacing.paced_budget_ml
                : pacing.paced_budget
            )} ml`
          );
        if (pacing.target_source === "weight_35ml_kg" && pacing.weight_lbs) {
          bits.push(
            `target 35 ml/kg @ ${Number(pacing.weight_lbs).toFixed(1)} lb`
          );
        } else if (pacing.target_source === "default") {
          bits.push("target default 2500 ml");
        }
        if (pacing.intake_source && pacing.intake_source !== "none")
          bits.push(String(pacing.intake_source).replace(/_/g, " "));
        paceMeta.textContent = bits.join(" · ");
      }
    } else if (paceSum) {
      paceSum.textContent = "Waiting for hydration / sleep data…";
      if (paceMeta) paceMeta.textContent = "";
    }
  }

  /** Render full-width calorie pacing + in/out delta from server calorie_bars. */
  function renderCalorieBars(data) {
    const bars = (data && data.calorie_bars) || {};
    const pacing = bars.pacing || null;
    const delta = bars.delta || null;

    // —— Pacing bar (above macro chips) ——
    const fill = $("calorie-pacing-fill");
    const marker = $("calorie-pacing-marker");
    const paceSum = $("calorie-pacing-summary");
    const paceMeta = $("calorie-pacing-meta");
    if (fill && pacing) {
      const pct = Math.max(0, Math.min(100, Number(pacing.fill_pct) || 0));
      const exp = Math.max(0, Math.min(100, Number(pacing.expected_pct) || 0));
      fill.style.width = `${pct}%`;
      // Prefer severity band (green/yellow/red vs pace); fall back to ahead/behind.
      const band = pacing.band || "";
      const status = pacing.status || "on_pace";
      fill.className = band
        ? `pace-fill band-${band} ${status}`
        : `pace-fill ${status}`;
      if (marker) marker.style.left = `${exp}%`;
      if (paceSum) {
        let sum = pacing.summary || "—";
        if (pacing.intake_source === "eating_window_logs") {
          const n =
            (pacing.window_intake && pacing.window_intake.log_count) || 0;
          sum += ` · from ${n} log${n === 1 ? "" : "s"} in wake window`;
        }
        paceSum.textContent = sum;
      }
      const win = pacing.window || {};
      if (paceMeta) {
        const bits = [];
        if (win.window_start)
          bits.push(`wake ${fmtBarWhen(win.window_start)}`);
        if (win.window_end) bits.push(`bed ~${fmtBarWhen(win.window_end)}`);
        if (win.fraction != null)
          bits.push(`${Math.round(Number(win.fraction) * 100)}% of window`);
        if (pacing.paced_budget != null)
          bits.push(`paced ~${fmtNum(pacing.paced_budget)} kcal`);
        if (pacing.intake_source === "eating_window_logs")
          bits.push("intake = logs in window (spans midnight)");
        paceMeta.textContent = bits.join(" · ");
      }
    } else if (paceSum) {
      paceSum.textContent = "Waiting for nutrition / sleep data…";
    }

    // —— In/out delta bar (below macro chips) ——
    const left = $("calorie-delta-fill-left");
    const right = $("calorie-delta-fill-right");
    const dSum = $("calorie-delta-summary");
    const dMeta = $("calorie-delta-meta");
    if (left) left.style.width = "0%";
    if (right) right.style.width = "0%";
    if (delta && delta.status === "ok") {
      // bar_pct is 0–100 of the half-track; CSS widths are % of full track
      const barPct = Math.max(0, Math.min(100, Number(delta.bar_pct) || 0));
      const halfW = barPct * 0.5;
      if (delta.side === "deficit" && left) {
        left.style.width = `${halfW}%`;
      } else if (delta.side === "surplus" && right) {
        right.style.width = `${halfW}%`;
      }
      if (dSum) dSum.textContent = delta.summary || "—";
      if (dMeta) {
        dMeta.textContent = `in ${fmtNum(delta.intake)} · out ${fmtNum(
          delta.burned
        )} · scale ±${fmtNum(delta.scale_kcal)} kcal`;
      }
    } else if (dSum) {
      dSum.textContent =
        (delta && delta.summary) ||
        "No same-day burned calories yet — delta unavailable.";
      if (dMeta) dMeta.textContent = "";
    }
  }

  function renderTargetsAndRemaining(store) {
    // Hydrates More → Daily targets form + optional macro pace bars under Today so far.
    // Day totals / remaining chips stay in the Today|Target tiles above (not duplicated).
    const t = (store && store.targets) || {};
    const c = (store && store.today_consumed) || {};
    const mp =
      (state && state.calorie_bars && state.calorie_bars.macro_pace) || {};
    if ($("tgt-cal")) {
      $("tgt-cal").value = t.calories ?? 2100;
      $("tgt-p").value = t.protein_g ?? 210;
      $("tgt-c").value = t.carbs_g ?? 180;
      $("tgt-f").value = t.fat_g ?? 55;
    }
    if ($("tgt-weight-goal")) {
      $("tgt-weight-goal").value =
        t.weight_goal_lbs != null && t.weight_goal_lbs !== ""
          ? t.weight_goal_lbs
          : "";
    }
    if ($("macro-pace-bars")) {
      $("macro-pace-bars").innerHTML = `
        <div class="macro-progress-list">
          <p class="muted macro-pace-legend" style="margin:0 0 0.35rem;font-size:0.78rem">
            Bars = vs <strong>pace now</strong> in the eating window (center = on target for this time).
            Green ≤5% · yellow ≤20% · red &gt;20% · protein over stays green longer.
          </p>
          ${progressRow("Calories", c.calories, t.calories, "cals", mp.calories)}
          ${progressRow("Protein", c.protein_g, t.protein_g, "protein", mp.protein_g)}
          ${progressRow("Carbs", c.carbs_g, t.carbs_g, "carbs", mp.carbs_g)}
          ${progressRow("Fat", c.fat_g, t.fat_g, "fat", mp.fat_g)}
        </div>
      `;
    }
  }

  function renderFoodLogsToday(store) {
    const box = $("food-logs-today");
    if (!box) return;
    const logs = (store && store.food_logs_today) || [];
    if (!logs.length) {
      box.innerHTML = `<p class="muted" style="margin:0;font-size:0.85rem">No meal-level food logs for today yet. Log food in Fitbit/Google Health (requires a valid Health OAuth connection).</p>`;
      return;
    }
    let slides = "";
    logs.forEach((f) => {
      const when = [f.time, f.meal_type].filter(Boolean).join(" · ");
      const serve = f.serving_label || "";
      slides += `<div class="inv-slide meal-item compact food-log-slide">
        <div class="meal-item-name">${f.name || "Food"}</div>
        <div class="meal-item-meta muted">${[when, serve].filter(Boolean).join(" · ") || "Logged meal"}</div>
        ${invMacroStrip(f, true)}
      </div>`;
    });
    box.innerHTML = `<div class="food-logs-carousel-panel">
      <div class="macro-summary-header" style="margin-bottom:0.25rem">
        <div class="macro-summary-title" style="font-size:0.95rem">Logged today</div>
        <div class="inv-carousel-count muted">${logs.length}</div>
      </div>
      ${invCarouselShell("food-logs-carousel", slides)}
    </div>`;
  }

  function renderFoodCoach(coach, store) {
    const box = $("food-coach-commentary");
    if (!box) return;
    const fc = (coach && coach.food_commentary) || {};
    const md = fc.markdown || "";
    const working = fc.working_well || [];
    const improve = fc.can_improve || [];
    if (!md && !working.length && !improve.length) {
      box.innerHTML =
        `<p class="muted" style="margin:0">Not enough food-log detail yet for a specific assessment.</p>`;
    } else if (md && typeof marked !== "undefined" && marked.parse) {
      box.innerHTML = `<div class="ask-md">${marked.parse(md)}</div>`;
    } else {
      let html = "";
      if (working.length) {
        html += `<p class="muted" style="margin:0.25rem 0"><strong>Working well</strong></p><ul class="reasons">${working.map((x) => `<li>${x}</li>`).join("")}</ul>`;
      }
      if (improve.length) {
        html += `<p class="muted" style="margin:0.25rem 0"><strong>Can improve</strong></p><ul class="reasons">${improve.map((x) => `<li>${x}</li>`).join("")}</ul>`;
      }
      box.innerHTML = html;
    }


    const labsBox = $("labs-summary");
    if (labsBox) {
      const labs = (fc.labs || (store && store.labs)) || {};
      const panels = labs.panels || [];
      if (labs.has_labs === false || (!panels.length && !labs.has_labs)) {
        labsBox.innerHTML =
          `Labs: none on file — optional <code>fitness/data/labs.json</code> for bi-annual/quarterly markers.`;
      } else if (labs.has_labs) {
        const flags = labs.flags || [];
        labsBox.innerHTML = `Latest labs <strong>${labs.date || "—"}</strong>${
          labs.lab ? ` (${labs.lab})` : ""
        }: ${labs.marker_count || 0} markers${
          flags.length
            ? ` · flags: ${flags.map((f) => `${f.marker} ${f.status}`).join(", ")}`
            : " · no coach flags"
        }.`;
      } else if (panels.length) {
        const p = panels[panels.length - 1];
        labsBox.innerHTML = `Latest labs <strong>${p.date || "—"}</strong>${
          p.lab ? ` (${p.lab})` : ""
        }: ${Object.keys(p.markers || {}).length} markers.`;
      } else {
        labsBox.innerHTML = "";
      }
    }
  }

  function renderExerciseCatalog(store) {
    const list = $("exercise-catalog-list");
    if (!list) return;
    list.innerHTML = "";
    const items = ((store && store.catalog && store.catalog.exercises) || []).slice();
    items.sort((a, b) => String(a.name).localeCompare(String(b.name)));
    if (!items.length) {
      list.innerHTML = `<li class="muted">No movements in the catalog library.</li>`;
      return;
    }
    const owned = new Set(
      ((store && store.equipment && store.equipment.items) || [])
        .map((g) => String((g && g.tag) || "").toLowerCase())
        .filter(Boolean)
    );
    items.forEach((ex) => {
      const li = document.createElement("li");
      const muscles = (ex.primary_muscles || []).join(", ") || "—";
      const sessions = (ex.session_types || []).join("/");
      const req = (ex.equipment || []).filter(Boolean);
      const any = (ex.equipment_any || []).filter(Boolean);
      const tags = req.concat(any);
      const feasible =
        (!req.length || req.every((t) => owned.has(String(t).toLowerCase()))) &&
        (!any.length || any.some((t) => owned.has(String(t).toLowerCase())));
      const gear = tags.length ? tags.join(", ") : "no gear tags";
      li.innerHTML = `
        <div class="title">${ex.name}${feasible ? "" : " <span class='muted'>(needs gear)</span>"}</div>
        <div class="meta">${sessions || "?"} · ${ex.movement || "compound"} · ${muscles}
          · ${gear}</div>
      `;
      list.appendChild(li);
    });
  }

  function renderEquipmentInventory(store) {
    const list = $("equipment-list");
    if (!list) return;
    const items = ((store && store.equipment && store.equipment.items) || []).slice();
    items.sort((a, b) => String(a.name).localeCompare(String(b.name)));
    if (!items.length) {
      list.innerHTML = `<p class="muted">No owned gear yet — add what you have and the max load. The planner will not invent cable/smith/assisted-pullup.</p>`;
      return;
    }
    let cards = "";
    items.forEach((g) => {
      const id = String(g.id || "").replace(/"/g, "&quot;");
      const name = String(g.name || "").replace(/"/g, "&quot;");
      const max =
        g.max_weight_lbs != null && g.max_weight_lbs !== ""
          ? `${g.max_weight_lbs} lb max`
          : "no load cap";
      cards += `<div class="inv-card" data-eq-id="${id}">
        <div class="inv-card-name">${g.name || g.tag || "Gear"}</div>
        <div class="inv-card-meta muted">${g.tag || "—"} · ${max}${
        g.notes ? ` · ${String(g.notes).slice(0, 80)}` : ""
      }</div>
        <div class="actions inv-card-actions">
          <button type="button" class="btn-edit" data-eq-action="edit" data-id="${id}"
            data-name="${name}" data-tag="${String(g.tag || "").replace(/"/g, "&quot;")}"
            data-max="${g.max_weight_lbs != null ? g.max_weight_lbs : ""}">Edit</button>
          <button type="button" class="btn-remove" data-eq-action="remove" data-id="${id}" data-name="${name}">Remove</button>
        </div>
      </div>`;
    });
    list.innerHTML = `<div class="inv-cards">${cards}</div>`;
  }

  function renderWorkoutGoals(store) {
    const g = (store && store.goals) || {};
    if ($("wg-count")) $("wg-count").value = g.exercises_per_session ?? 5;
    if ($("wg-rest")) $("wg-rest").value = g.rest_if_recovery_below ?? 40;
    if ($("wg-split")) $("wg-split").value = g.split || "ppl";
  }

  /** Majors shown for a PPL day (weekly credits still track the full body). */
  const SESSION_MUSCLES = {
    push: ["chest", "delts", "triceps", "traps"],
    pull: ["mid_upper_back", "lats", "biceps", "traps"],
    legs: ["quads", "hamstrings", "glutes", "calves", "adductors"],
  };

  function markForcedSessionButtons(sessionType) {
    const st = String(sessionType || "").toLowerCase();
    ["push", "pull", "legs"].forEach((key) => {
      const btn = $(`btn-force-session-${key}`);
      if (!btn) return;
      const on = st === key;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function renderVolumeBalance(volume, sessionType) {
    if (!volume || !Array.isArray(volume.muscles)) return "";
    const fw = volume.framework || {};
    const st = String(sessionType || "").toLowerCase();
    const sessionMuscles = SESSION_MUSCLES[st] || null;
    let pool = volume.muscles;
    // Train day: only muscles trained that session — not a mixed full-body list.
    // Rest day / unknown: full week overview for muscles with signal.
    if (sessionMuscles) {
      const allow = new Set(sessionMuscles);
      pool = volume.muscles.filter((m) => allow.has(String(m.muscle || "")));
    } else {
      pool = volume.muscles.filter(
        (m) =>
          (m.done || 0) > 0 ||
          (m.planned || 0) > 0 ||
          m.status === "under" ||
          m.status === "low" ||
          m.priority
      );
    }
    const show = (pool.length ? pool : volume.muscles).slice(0, 13);
    let chips = show
      .map((m) => {
        const label = String(m.muscle || "").replace(/_/g, " ");
        const done = m.done != null ? m.done : 0;
        const planned = m.planned != null ? m.planned : 0;
        const projected = m.projected != null ? m.projected : done + planned;
        const band = `${m.min}–${m.max}`;
        // Readable: done + plan → week total  |  weekly target band
        const proj =
          planned > 0
            ? `${done} done + ${planned} plan → ${projected}`
            : `${done} done`;
        const status = m.status || "ok";
        const statusHint =
          status === "under" || status === "low"
            ? "below weekly band"
            : status === "high" || status === "over"
              ? "above weekly band"
              : "in weekly band";
        return `<span class="vol-chip vol-${status}" title="${label}: ${done} hard sets logged this week + ${planned} in today’s plan → ${projected} projected (target ${band}) — ${statusHint}">
          <span class="vol-chip-m">${label}</span>
          <span class="vol-chip-v">${proj}</span>
          <span class="vol-chip-b">target ${band}</span>
        </span>`;
      })
      .join("");
    const focus = volume.focus || {};
    const focusMuscles = Array.isArray(focus.muscles) ? focus.muscles : [];
    const src = focus.source || "auto";
    const dayLabel = st
      ? st.charAt(0).toUpperCase() + st.slice(1)
      : "Week";
    const legendLine = `<p class="muted volume-balance-note"><strong>Chip legend:</strong> <em>done</em> = hard sets already logged this week · <em>plan</em> = hard sets in today’s prescription · <em>→ total</em> = projected week if you complete the plan · <em>target min–max</em> = weekly band. Amber = under · green = in range · red = high/over.</p>`;
    const scopeLine = sessionMuscles
      ? `<p class="muted volume-balance-note">Showing <strong>${dayLabel}</strong> muscles only (this session). Weekly hard-set credits still count everywhere.</p>`
      : `<p class="muted volume-balance-note">Full-week overview (≈4–8 hard sets/muscle/week w/ overlap).</p>`;
    const focusLine = focusMuscles.length
      ? `<p class="muted volume-balance-note"><strong>${
          src === "auto" ? "Auto focus" : "Focus"
        }:</strong> ${focusMuscles
          .map((m) => String(m).replace(/_/g, " "))
          .join(", ")}${
          focus.reason ? ` — ${focus.reason}` : ""
        }. ${
          src === "auto"
            ? "Planner picked these as this week’s priority muscles (lagging vs the weekly hard-set band); other groups stay nearer maintenance."
            : "Manual priority muscles; others near maintenance."
        }</p>`
      : `<p class="muted volume-balance-note">Balanced volume (no priority muscles). Primary sets full credit; secondary partial.</p>`;
    // fw.label is already "Balanced volume (≈4–8…)" — no coach brand in heading
    const frameworkBit = (fw.label || "Balanced volume (≈4–8 / muscle)").replace(
      /^DeanT\s+/i,
      ""
    );
    return `<div class="volume-balance">
      <div class="volume-balance-title">Weekly Hard Sets · ${dayLabel} · ${frameworkBit}</div>
      <div class="volume-balance-chips">${chips}</div>
      ${legendLine}
      ${scopeLine}
      ${focusLine}
    </div>`;
  }

  function renderContinuityBanner(ctx) {
    const c = (ctx && ctx.training_continuity) || null;
    if (!c || !c.phase || c.phase === "normal") return "";
    const days =
      c.days_since != null ? `${c.days_since}d since last log` : "no recent logs";
    const cut =
      c.load_cut_pct != null ? `loads −${c.load_cut_pct}%` : "loads reduced";
    const ramp = c.volume_band_scale != null
      ? `volume ramp ~${Math.round(Number(c.volume_band_scale) * 100)}%`
      : "volume ramping";
    return `<div class="continuity-banner" role="status" style="margin:0.5rem 0 0.75rem;padding:0.65rem 0.75rem;border-radius:8px;border:1px solid rgba(240,180,41,0.45);background:rgba(240,180,41,0.1)">
      <strong>${c.label || "Return phase"}</strong>
      <span class="muted"> · ${days} · ${cut} · ${ramp}</span>
      <div class="muted" style="margin-top:0.25rem;font-size:0.85rem">${
        c.summary || "Re-establish pattern before chasing prior loads."
      }</div>
    </div>`;
  }

  function renderWorkoutPlan(plan) {
    const box = $("workout-plan-result");
    if (!box) return;
    if (!plan) {
      box.innerHTML = "";
      markForcedSessionButtons("");
      return;
    }
    const ctx0 = plan.context || {};
    let html = renderContinuityBanner(ctx0);
    html += `<p class="muted">${plan.message || ""}</p>`;
    if (plan.is_rest_day) {
      html += `<p><strong>Rest day</strong> — recovery below threshold.</p>`;
      html += renderVolumeBalance(plan.volume, null);
      markForcedSessionButtons("");
      box.innerHTML = html;
      return;
    }
    const stRaw = String(plan.session_type || "").toLowerCase();
    const st = stRaw.toUpperCase();
    const hard = (plan.context && plan.context.session_hard_sets) || null;
    html += `<p><strong>${st || "Session"}</strong> · ${(plan.exercises || []).length} lifts${
      hard != null ? ` · ${hard} hard sets` : ""
    }</p>`;
    html += renderVolumeBalance(plan.volume, stRaw);
    markForcedSessionButtons(stRaw);
    const items = plan.exercises || [];
    if (!items.length) {
      html += `<p class="muted">${
        plan.message ||
        "No owned equipment can load a lift for this session. Add gear — do not invent cable/smith/assisted-pullup."
      }</p>`;
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
        const credits = ex.set_credits
          ? Object.entries(ex.set_credits)
              .map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`)
              .join(", ")
          : "";
        html += `<li>
          <div class="title">${ex.name}</div>
          <div class="meta"><strong>${w} × ${rx.sets || "?"} × ${rx.reps || "?"}</strong>
            · ${ex.movement || ""} · ${muscles}</div>
          <div class="meta muted" style="font-size:0.85rem">${ex.rationale || last}${
          credits ? ` · credits: ${credits}` : ""
        }</div>
        </li>`;
      });
      html += `</ul>`;
    }
    const ctx = plan.context || {};
    if (ctx.last_session_type != null || ctx.days_since_last != null || ctx.training_continuity) {
      const phase = (ctx.training_continuity && ctx.training_continuity.phase) || "—";
      html += `<p class="muted" style="margin-top:0.75rem;font-size:0.85rem">
        Context: last=${ctx.last_session_type || "—"} · days since log=${ctx.days_since_last ?? "—"}
        · continuity=${phase}
        · catalog pool=${ctx.pool_for_session ?? "—"}
        · session cap=${ctx.session_working_set_cap ?? "—"} hard sets
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
    const pt = plan.planned_totals || {};
    const ra = plan.remaining_after_plan || {};
    let html = `<div class="meal-plan-panel">`;
    html += `<p class="muted" style="margin:0 0 0.5rem;font-size:0.85rem">${plan.message || ""}</p>`;
    if (plan.serving_grams_nudge) {
      html += `<p class="meal-grams-nudge">${plan.serving_grams_nudge}</p>`;
    }
    html += `<div class="meal-plan-totals-row">
      <div class="meal-plan-totals compact">
        <div class="meal-plan-totals-label">Planned add</div>
        ${invMacroStrip(pt, true)}
      </div>
      <div class="meal-plan-totals remaining compact">
        <div class="meal-plan-totals-label">After plan remaining</div>
        ${invMacroStrip(ra, true)}
      </div>
    </div>`;
    if (!meals.length) {
      const remB = plan.remaining_before_plan || {};
      const remCals = Number(remB.calories);
      const remP = Number(remB.protein_g);
      if (
        Number.isFinite(remCals) &&
        Number.isFinite(remP) &&
        remCals < 150 &&
        remP < 20
      ) {
        html += `<p class="muted">Day is essentially full (≈${fmtNumShort(
          remCals
        )} kcal / ${fmtNumShort(remP)}g protein left) — nothing useful to add from stock.</p>`;
      } else if (plan.pantry_dark) {
        if (!/Pantry unavailable/i.test(plan.message || "")) {
          html += `<p class="muted">Pantry unavailable</p>`;
        }
      } else if (plan.stocked_count === 0) {
        if (!/No in-stock/i.test(plan.message || "")) {
          html += `<p class="muted">No in-stock items</p>`;
        }
      }
    } else {
      // Vertical snap carousel: one meal bucket visible at a time (balances height vs Today so far)
      let mealSlides = "";
      meals.forEach((m, mi) => {
        const items = m.items || [];
        let slides = "";
        items.forEach((it) => {
          // Total portion only (grams when known) — no ×N inventory-serving badge.
          const serve = formatPlanPortion(it);
          slides += `<div class="inv-slide meal-item compact">
            <div class="meal-item-name">${it.name || "Item"}</div>
            <div class="meal-item-meta muted">${serve}</div>
            ${invMacroStrip(it, true)}
          </div>`;
        });
        const cid = `meal-carousel-${mi}`;
        const clock = mealBucketClock(m);
        const clockBit = clock
          ? ` · <span class="meal-bucket-time">${clock}</span>`
          : "";
        mealSlides += `<div class="meal-vslide meal-bucket" data-meal-idx="${mi}">
          <div class="meal-bucket-head">
            <div class="title">${m.label || "Meal"}${clockBit} · ${items.length} item${
          items.length === 1 ? "" : "s"
        }</div>
            ${invMacroStrip(m.totals || {}, true)}
          </div>
          ${
            items.length
              ? invCarouselShell(cid, slides, "No items")
              : `<p class="muted" style="margin:0.35rem 0 0">No items.</p>`
          }
        </div>`;
      });
      html += `<div class="meal-vcarousel-shell">
        <div class="meal-vcarousel-meta muted">
          <span id="meal-vcarousel-label">${meals.length} meal${
        meals.length === 1 ? "" : "s"
      } · one at a time</span>
        </div>
        <div class="meal-vcarousel-row">
          <button type="button" class="inv-carousel-nav meal-vcarousel-nav prev" data-action="carousel-nav" data-carousel="meal-plan-vcarousel" data-dir="-1" data-axis="y" aria-label="Previous meal">▴</button>
          <div class="meal-vcarousel" id="meal-plan-vcarousel" data-axis="y" tabindex="0" role="region" aria-label="Planned meals">
            <div class="meal-vcarousel-track">
              ${mealSlides}
            </div>
          </div>
          <button type="button" class="inv-carousel-nav meal-vcarousel-nav next" data-action="carousel-nav" data-carousel="meal-plan-vcarousel" data-dir="1" data-axis="y" aria-label="Next meal">▾</button>
        </div>
      </div>`;
    }
    html += `</div>`;
    box.innerHTML = html;
  }

  function liveFingerprint(data) {
    const rec = data.recovery || {};
    const health = data.health || {};
    const sleep = (health.sleep || []).slice(-1)[0] || {};
    const hydr = (health.hydration || []).slice(-1)[0] || {};
    const nut = (health.nutrition || []).slice(-1)[0] || {};
    const sessions = data.sessions || [];
    const last = sessions[0] || {};
    return JSON.stringify({
      recScore: rec.score,
      recLabel: rec.label,
      sleepH: sleep.sleep_hours,
      sleepD: sleep.date,
      hydrMl: hydr.water_ml,
      hydrD: hydr.date,
      cals: nut.calories,
      protein: nut.protein_g,
      nutD: nut.date,
      foodN: (health.food_logs || []).length,
      sessN: sessions.length,
      lastSess: `${last.date || ""}:${last.session_type || ""}`,
      healthErr: health.error || "",
    });
  }

  function render(data, opts) {
    const quiet = !!(opts && opts.quiet);
    state = data;
    if (!quiet) clearAlerts();

    const rec = data.recovery || {};
    if ($("recovery-badge")) {
      $("recovery-badge").innerHTML = `<span class="badge ${recoveryClass(rec.label)}">${rec.label || "—"} · ${rec.score ?? "—"}</span>`;
    }
    renderSleepBatteryMini(
      (rec && rec.sleep_battery) || data.sleep_battery || null
    ); // bottom of recovery card
    const reasons = $("recovery-reasons");
    if (reasons) {
      reasons.innerHTML = "";
      // Cap reasons so the mini battery does not force the card taller
      (rec.reasons || []).slice(0, 4).forEach((r) => {
        const li = document.createElement("li");
        li.textContent = r;
        reasons.appendChild(li);
      });
    }

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
      ? ` · cache: ${cacheBits.join(", ")} (ttl ${ttlMin}m; Refresh data forces pull)`
      : ` · cache ttl ${ttlMin}m`;
    const tzBit = meta.timezone ? ` · tz ${meta.timezone}` : "";
    const todayBit = meta.local_today ? ` · today ${meta.local_today}` : "";
    $("meta-line").textContent =
      `source=${meta.source || "?"} · generated ${meta.generated_at || ""}${loadMs}${cacheNote}${todayBit}${tzBit}`;

    // Colored macro tiles: left = today so far, right = daily target
    renderNutritionStatTiles(data.nutrition_store);
    // Full-width pacing (above chips) + in/out delta (below chips)
    renderCalorieBars(data);
    renderHydrationBars(data);
    // Mirror cache meta onto mobile admin card
    if ($("mobile-meta-line") && $("meta-line")) {
      $("mobile-meta-line").textContent = $("meta-line").textContent;
    }

    if (data.health && data.health.error) {
      const err = String(data.health.error || "");
      const needsAuth =
        /token|refresh|oauth|credentials|invalid_grant|401|403/i.test(err);
      // Auth toasts once per browser session — sticky toast every load was noisy
      // while cache still held an old HTTP 400 after a successful re-auth.
      const authKey = "fitdash_gh_auth_toast";
      if (needsAuth) {
        if (!sessionStorage.getItem(authKey)) {
          sessionStorage.setItem(authKey, "1");
          showAlert(
            "Google Health sign-in expired — More → Reconnect Google Health, then Refresh data.",
            "warn"
          );
        }
        $("health-note").textContent =
          "Google Health auth needs attention — More → Reconnect Google Health, then Refresh data. Cached health still shown.";
      } else {
        showAlert(`Google Health: ${err}`, "warn");
        $("health-note").textContent =
          "Some Google Health streams failed or need extra OAuth scopes (nutrition / activity). Recovery still uses available data.";
      }
    } else if (!(data.health && data.health.weight && data.health.weight.length)) {
      try {
        sessionStorage.removeItem("fitdash_gh_auth_toast");
      } catch (_) {
        /* ignore */
      }
      $("health-note").textContent = "No weight samples returned for the recent window.";
    } else {
      try {
        sessionStorage.removeItem("fitdash_gh_auth_toast");
      } catch (_) {
        /* ignore */
      }
      $("health-note").textContent = `Google Health connected · ${data.health.weight.length} weight pts, ${(data.health.sleep || []).length} sleep nights.`;
    }
    if ($("nutrition-note")) {
      const note = $("nutrition-note");
      const n = ((data.health && data.health.nutrition) || []).length;
      const h = ((data.health && data.health.hydration) || []).length;
      const b = ((data.health && data.health.calories_burned) || []).length;
      if (!n && !h && !b) {
        note.innerHTML =
          `<p class="chart-summary-empty">No nutrition/hydration yet — re-connect Google Health to grant nutrition + activity scopes, and log food/water in Fitbit/Google Health.</p>`;
      } else {
        // Cumulative intake − burned over the same window as the chart.
        // Days with both series present only, so the sum matches the shaded bands.
        const spanDays = CAL_IN_OUT_SPAN_DAYS;
        const end = new Date();
        end.setHours(0, 0, 0, 0);
        const z = (x) => String(x).padStart(2, "0");
        const labels = [];
        for (let i = spanDays - 1; i >= 0; i--) {
          const d = new Date(end);
          d.setDate(d.getDate() - i);
          labels.push(
            `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`
          );
        }
        const intakeBy = Object.fromEntries(
          ((data.health && data.health.nutrition) || []).map((row) => [
            String(row.date).slice(0, 10),
            Number(row.calories),
          ])
        );
        const burnedBy = Object.fromEntries(
          ((data.health && data.health.calories_burned) || []).map((row) => [
            String(row.date).slice(0, 10),
            Number(row.calories),
          ])
        );
        let cumDelta = 0;
        let pairDays = 0;
        let sumIn = 0;
        let sumOut = 0;
        labels.forEach((day) => {
          const vin = intakeBy[day];
          const vout = burnedBy[day];
          if (
            vin == null ||
            vout == null ||
            Number.isNaN(vin) ||
            Number.isNaN(vout)
          ) {
            return;
          }
          sumIn += vin;
          sumOut += vout;
          cumDelta += vin - vout;
          pairDays += 1;
        });
        const rounded = Math.round(cumDelta);
        let deltaClass = "chip-balance";
        let deltaLabel = "Balance";
        let deltaVal = "±0 kcal";
        if (pairDays > 0 && rounded > 0) {
          deltaClass = "chip-surplus";
          deltaLabel = "Cumulative surplus";
          deltaVal = `+${rounded.toLocaleString()} kcal`;
        } else if (pairDays > 0 && rounded < 0) {
          deltaClass = "chip-deficit";
          deltaLabel = "Cumulative deficit";
          deltaVal = `${rounded.toLocaleString()} kcal`;
        } else if (pairDays === 0) {
          deltaLabel = "Cumulative";
          deltaVal = "—";
        }
        const goalHint = [
          (data.nutrition_store && data.nutrition_store.targets && data.nutrition_store.targets.notes) ||
            "",
          (data.workout_store && data.workout_store.goals && data.workout_store.goals.goal) ||
            "",
          (data.workout_store && data.workout_store.goals && data.workout_store.goals.notes) ||
            "",
        ].join(" ");
        const align = energyWeightAlignment({
          cumDeltaKcal: pairDays > 0 ? cumDelta : null,
          pairDays,
          weights: (data.health && data.health.weight) || [],
          windowStart: labels[0],
          windowEnd: labels[labels.length - 1],
          goalHint,
        });

        let alignHtml = "";
        if (align) {
          const expSign = align.expectedLb >= 0 ? "+" : "";
          const actSign = align.actualLb >= 0 ? "+" : "";
          const statusLabel =
            align.status === "aligned"
              ? "Lines up"
              : align.status === "divergent"
                ? "Does not line up"
                : "Partial match";
          const statusClass =
            align.status === "aligned"
              ? "align-ok"
              : align.status === "divergent"
                ? "align-bad"
                : "align-warn";
          alignHtml = `
            <div class="energy-weight-insight ${statusClass}">
              <div class="ewi-header">
                <span class="ewi-title">Energy vs scale · ${spanDays}d</span>
                <span class="ewi-badge">${statusLabel}</span>
              </div>
              <div class="chart-summary-row ewi-metrics">
                <div class="chart-summary-chip">
                  <span class="chip-k">From calories</span>
                  <span class="chip-v">${expSign}${align.expectedLb.toFixed(1)} lb</span>
                  <span class="chip-s">expected · ~3,500 kcal/lb</span>
                </div>
                <div class="chart-summary-chip">
                  <span class="chip-k">On scale</span>
                  <span class="chip-v">${actSign}${align.actualLb.toFixed(1)} lb</span>
                  <span class="chip-s">${align.first.date} → ${align.last.date}</span>
                </div>
                <div class="chart-summary-chip">
                  <span class="chip-k">Gap</span>
                  <span class="chip-v">${align.residualLb >= 0 ? "+" : ""}${align.residualLb.toFixed(1)} lb</span>
                  <span class="chip-s">scale − expected</span>
                </div>
              </div>
              <ul class="ewi-advice">
                ${align.advice.map((a) => `<li>${a}</li>`).join("")}
              </ul>
            </div>
          `;
        } else if (pairDays >= 5) {
          alignHtml = `
            <div class="energy-weight-insight align-warn">
              <div class="ewi-header">
                <span class="ewi-title">Energy vs scale · ${spanDays}d</span>
                <span class="ewi-badge">Need weigh-ins</span>
              </div>
              <p class="chart-summary-meta" style="margin:0">
                Log at least two weigh-ins ≥5 days apart in this window to compare the cumulative calorie balance to scale change.
              </p>
            </div>
          `;
        }

        note.innerHTML = `
          <div class="chart-summary-row">
            <div class="chart-summary-chip chip-in">
              <span class="chip-k">Σ intake</span>
              <span class="chip-v">${
                pairDays > 0 ? Math.round(sumIn).toLocaleString() : "—"
              }</span>
              <span class="chip-s">kcal · ${spanDays}d window</span>
            </div>
            <div class="chart-summary-chip chip-out">
              <span class="chip-k">Σ burned</span>
              <span class="chip-v">${
                pairDays > 0 ? Math.round(sumOut).toLocaleString() : "—"
              }</span>
              <span class="chip-s">kcal · ${spanDays}d window</span>
            </div>
            <div class="chart-summary-chip ${deltaClass}">
              <span class="chip-k">${deltaLabel}</span>
              <span class="chip-v">${deltaVal}</span>
              <span class="chip-s">${
                pairDays > 0
                  ? `${pairDays} days with both in &amp; out`
                  : "Need paired intake + burned days"
              }</span>
            </div>
          </div>
          ${alignHtml}
          <p class="chart-summary-meta">
            Rolling ${spanDays}d · ${n} nutrition days · ${b} burned days · green band = surplus · red band = deficit
          </p>
        `;
      }
    }

    if (meta.error) {
      showAlert(`Lift source note: ${meta.error}`, "warn");
    }

    renderCharts(data);
    // Charts built while Trends is display:none measure wrong on phone — fix after paint
    requestAnimationFrame(() => {
      requestAnimationFrame(() => resizeAllCharts());
    });
    renderHistory(data.sessions || []);
    renderInventory(data.nutrition_store);
    renderInventorySuggestions(data.nutrition_store);
    renderInventoryRemovals(data.nutrition_store);
    renderTargetsAndRemaining(data.nutrition_store);
    renderFoodLogsToday(data.nutrition_store);
    // Auto meal plan is computed server-side on every dashboard load
    if (data.nutrition_store && data.nutrition_store.meal_plan) {
      renderMealPlan(data.nutrition_store.meal_plan);
    }
    renderFoodCoach(data.coach, data.nutrition_store);
    renderExerciseCatalog(data.workout_store);
    renderEquipmentInventory(data.workout_store);
    renderWorkoutGoals(data.workout_store);
    if (data.workout_store && data.workout_store.plan) {
      renderWorkoutPlan(data.workout_store.plan);
    }
    renderTodayHub(data);
  }

  /** Civil day of the last successful /api/daily-tasks sync in this page. */
  let lastQuestSyncedDay = "";
  /** Last GT-backed quest payload — Refresh data must not wipe task ids. */
  let lastSyncedDailyTasks = null;

  function escQuest(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function questLeafIds(item, group, dailyListId) {
    const it = item || {};
    const g = group || {};
    const tid = String(it.task_id || it.id || "").trim();
    const lid = String(it.list_id || g.list_id || dailyListId || "").trim();
    const pid = String(g.task_id || g.id || "").trim();
    return { tid, lid, pid, ready: !!(tid && lid) };
  }

  const QUEST_SYNC_FAIL = "Could not sync quests with Google Tasks";
  let questSyncUi = { status: "idle", error: "" };

  function dailyHasReadyLeaf(daily) {
    const listId = (daily && daily.list_id) || "";
    for (const g of (daily && daily.groups) || []) {
      for (const it of g.open_items || g.items || []) {
        if (it && !it.completed && questLeafIds(it, g, listId).ready) return true;
      }
    }
    return false;
  }

  function questFallbackActions() {
    return (
      (state && state.coach && state.coach.today && state.coach.today.actions) ||
      []
    );
  }

  /** Match server looks_like_lift_quest: training/ex-* leaves, not meals or session titles. */
  function looksLikeLiftQuest(group, title, slug) {
    const g = String(group || "").trim().toLowerCase();
    const slugL = String(slug || "").trim().toLowerCase();
    const titleS = String(title || "").trim();
    if (
      g === "nutrition" ||
      g === "shopping" ||
      g === "sleep" ||
      g === "recovery" ||
      g === "other"
    ) {
      return false;
    }
    if (/^(complete today|rest day|protect |cover remaining|eat through|eat:)/i.test(titleS)) {
      return false;
    }
    if (slugL.startsWith("ex-")) return true;
    if ((g === "training" || g === "train") && titleS) return true;
    return false;
  }

  /** Build quest body HTML (sync note + groups) — never includes the collapsible shell. */
  function buildDailyQuestBodyHtml(groups, { syncing, err, src, listId, failed }) {
    let html = "";
    if (syncing) {
      html += `<p class="muted quest-sync-note">Syncing with Google Tasks…</p>`;
    } else if (failed || err) {
      html += `<p class="quest-sync-note quest-sync-error">${QUEST_SYNC_FAIL}</p>`;
      if (err && String(err) !== QUEST_SYNC_FAIL) {
        html += `<p class="muted quest-sync-note">${escQuest(err)}</p>`;
      }
      html += `<button type="button" class="quest-sync-retry" data-quest-retry>Retry sync</button>`;
    } else if (src === "google_tasks") {
      html += `<p class="muted quest-sync-note">Fitness list · complete here or in Google Tasks</p>`;
    }

    groups.forEach((g) => {
      const gDone = g.done || 0;
      const gTot = g.total || 0;
      const allItems = g.items || g.open_items || [];
      const open = allItems.filter((x) => !x.completed);
      const visible = allItems.filter((x) => {
        if (!x.completed) return true;
        return looksLikeLiftQuest(g.group || x.group, x.title, x.slug);
      });
      const emoji = g.emoji || "✓";
      html += `<div class="quest-group${g.completed || !open.length ? " is-done" : ""}" data-group="${escQuest(g.group || "")}">
        <div class="quest-group-head">
          <span class="quest-group-emoji">${emoji}</span>
          <span class="quest-group-title">${escQuest(g.title || g.group || "Group")}</span>
          <span class="quest-group-prog">${gDone}/${gTot}</span>
        </div>`;
      if (!visible.length) {
        html += `<div class="quest-group-done">Cleared ✓</div>`;
      } else {
        // Bucket by meal_label for nutrition
        const byMeal = {};
        const noMeal = [];
        visible.forEach((it) => {
          if (it.meal_label) {
            (byMeal[it.meal_label] = byMeal[it.meal_label] || []).push(it);
          } else noMeal.push(it);
        });
        const renderCard = (it, g) => {
          const { tid, lid, pid, ready } = questLeafIds(it, g, listId);
          const done = !!it.completed;
          const liftDone = done && looksLikeLiftQuest(g.group || it.group, it.title, it.slug);
          // Strip redundant "Next meal: " prefix if already under meal header
          let label = it.title || "";
          if (it.meal_label && label.startsWith(it.meal_label + ":")) {
            label = label.slice(it.meal_label.length + 1).trim();
          }
          const verb = liftDone ? "Uncheck" : "Complete";
          // Leaf with task_id + list_id is a real control (never native disabled —
          // disabled buttons drop clicks so document delegation cannot bind them).
          return `<button type="button" class="quest-card${ready ? "" : " quest-card-pending"}${done ? " is-done" : ""}"
            data-task-id="${escQuest(tid)}" data-list-id="${escQuest(lid)}" data-parent-id="${escQuest(pid)}"
            data-group="${escQuest(g.group || "")}" data-title="${escQuest(it.title || "")}"
            data-slug="${escQuest(it.slug || "")}"
            ${ready ? "" : 'aria-disabled="true"'} aria-pressed="${done ? "true" : "false"}"
            aria-label="${verb}: ${escQuest(label)}">
            <span class="quest-card-mark" aria-hidden="true"></span>
            <span class="quest-card-text">${escQuest(label)}</span>
          </button>`;
        };
        Object.keys(byMeal).forEach((mealLab) => {
          html += `<div class="quest-meal-bucket">
            <div class="quest-meal-label">${mealLab}</div>
            <div class="quest-card-row">`;
          byMeal[mealLab].forEach((it) => {
            html += renderCard(it, g);
          });
          html += `</div></div>`;
        });
        if (noMeal.length) {
          html += `<div class="quest-card-row">`;
          noMeal.forEach((it) => {
            html += renderCard(it, g);
          });
          html += `</div>`;
        }
      }
      html += `</div>`;
    });
    return html;
  }

  /** Apply collapseOpen.quests to existing head/body without replacing the shell. */
  function applyQuestsCollapseDom(root) {
    const el = root || $("today-actions") || document;
    const head =
      el.querySelector?.('[data-collapse="quests"]') ||
      document.querySelector('[data-collapse="quests"]');
    const body =
      el.querySelector?.('[data-collapse-body="quests"]') ||
      document.querySelector('[data-collapse-body="quests"]');
    if (!head && !body) return;
    const open = collapseOpen.quests !== false;
    if (head) {
      head.setAttribute("aria-expanded", open ? "true" : "false");
      head.classList.toggle("is-collapsed", !open);
    }
    if (body) body.hidden = !open;
  }

  /**
   * Polished daily quests — tap cards, meal subgroups, async GT sync.
   * Critical: once the collapsible shell exists, never replace it on re-render
   * (async GT sync was forcing quests open by rewriting the whole block).
   */
  function renderDailyPlanTasks(daily, fallbackActions) {
    const box = $("today-actions");
    if (!box) return;
    const groups = (daily && daily.groups) || [];
    const sum = (daily && daily.summary) || {};
    const err = daily && daily.error;
    const src = (daily && daily.source) || "";
    const failed = !!(daily && (daily.sync_failed || questSyncUi.status === "failed"));
    const syncing =
      !failed && daily && daily.needs_sync && src !== "google_tasks";
    const existing = box.querySelector(".daily-quests");

    if (!groups.length) {
      const acts = fallbackActions || [];
      if (!acts.length) {
        // Keep shell if user already collapsed it — only swap body note / wipe once
        if (existing) {
          const body = existing.querySelector('[data-collapse-body="quests"]');
          if (body) {
            body.innerHTML = failed || err
              ? `<p class="quest-sync-note quest-sync-error">${QUEST_SYNC_FAIL}</p>` +
                (err && String(err) !== QUEST_SYNC_FAIL
                  ? `<p class="muted quest-sync-note">${escQuest(err)}</p>`
                  : "") +
                `<button type="button" class="quest-sync-retry" data-quest-retry>Retry sync</button>`
              : syncing
                ? `<p class="muted quest-sync-note">Syncing with Google Tasks…</p>`
                : `<p class="muted quest-sync-note">No open quests.</p>`;
          }
          applyQuestsCollapseDom(existing);
          return;
        }
        box.innerHTML = failed || err
          ? `<p class="quest-sync-note quest-sync-error">${QUEST_SYNC_FAIL}</p>` +
            `<button type="button" class="quest-sync-retry" data-quest-retry>Retry sync</button>`
          : syncing
            ? `<p class="muted" style="font-size:0.82rem;margin:0">Syncing quests…</p>`
            : "";
        return;
      }
    }

    const done = sum.done != null ? sum.done : 0;
    const total = sum.total != null ? sum.total : 0;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    const bodyHtml = buildDailyQuestBodyHtml(groups, {
      syncing,
      err,
      src,
      failed,
      listId: (daily && daily.list_id) || "",
    });

    // Re-render path: update meter/count/body only — head + open/closed stay put
    if (existing) {
      const count = existing.querySelector(".daily-quests-count");
      if (count) count.textContent = `${done}/${total}`;
      const fill = existing.querySelector(".daily-quests-meter-fill");
      if (fill) fill.style.width = `${pct}%`;
      const body = existing.querySelector('[data-collapse-body="quests"]');
      if (body) body.innerHTML = bodyHtml;
      existing.classList.toggle("is-syncing", !!syncing);
      existing.classList.toggle("is-sync-failed", !!failed);
      applyQuestsCollapseDom(existing);
      return;
    }

    // First paint only — bake current preference into shell
    const questsOpen = collapseOpen.quests !== false;
    box.innerHTML = `<div class="daily-quests${syncing ? " is-syncing" : ""}${failed ? " is-sync-failed" : ""}">
      <button type="button" class="daily-quests-head collapsible-head${questsOpen ? "" : " is-collapsed"}" data-collapse="quests" aria-expanded="${questsOpen ? "true" : "false"}">
        <span class="collapsible-title">⚔ Daily quests</span>
        <span class="daily-quests-meter" aria-hidden="true"><span class="daily-quests-meter-fill" style="width:${pct}%"></span></span>
        <span class="muted daily-quests-count">${done}/${total}</span>
        <span class="collapse-chevron">▾</span>
      </button>
      <div class="collapsible-body" data-collapse-body="quests"${questsOpen ? "" : " hidden"}>
        ${bodyHtml}
      </div>
    </div>`;
  }

  function applyDailyTasks(daily) {
    if (state && daily && typeof daily === "object") {
      state.daily_tasks = daily;
      if (state.coach && state.coach.today) state.coach.today.daily_tasks = daily;
      if (
        daily.source === "google_tasks" &&
        daily.ok &&
        !daily.needs_sync &&
        !daily.sync_failed
      ) {
        lastSyncedDailyTasks = daily;
      }
    }
    renderDailyPlanTasks(daily, questFallbackActions());
  }

  function markQuestSyncFailed(message, daily) {
    const err = message || QUEST_SYNC_FAIL;
    questSyncUi = { status: "failed", error: err };
    const base = daily && typeof daily === "object" ? daily : (state && state.daily_tasks) || {};
    applyDailyTasks({
      ...base,
      ok: false,
      error: err,
      needs_sync: false,
      sync_failed: true,
      source: (base && base.source) || "local_preview",
      groups: (base && base.groups) || [],
    });
  }

  async function syncDailyTasksFromServer() {
    const current = (state && state.daily_tasks) || {};
    questSyncUi = { status: "syncing", error: "" };
    applyDailyTasks({
      ...current,
      needs_sync: true,
      sync_failed: false,
      error: null,
    });
    try {
      const res = await fetch("/api/daily-tasks", {
        credentials: "same-origin",
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      const daily = data && data.daily_tasks;
      if (!res.ok || !daily || typeof daily !== "object") {
        markQuestSyncFailed((data && data.error) || QUEST_SYNC_FAIL, current);
        return false;
      }
      if (dailyHasReadyLeaf(daily)) {
        questSyncUi = { status: "ok", error: "" };
        applyDailyTasks({ ...daily, needs_sync: false, sync_failed: false, error: null });
        return true;
      }
      markQuestSyncFailed(daily.error || data.error || QUEST_SYNC_FAIL, daily);
      return false;
    } catch (e) {
      markQuestSyncFailed((e && e.message) || QUEST_SYNC_FAIL, current);
      return false;
    }
  }

  function unlockQuestCard(btn) {
    if (!btn) return;
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    btn.classList.remove("is-completing");
  }

  function patchLocalQuestCompleted(taskId, completed) {
    const tid = String(taskId || "").trim();
    if (!tid) return;
    const seen = new Set();
    const synced =
      typeof lastSyncedDailyTasks !== "undefined" ? lastSyncedDailyTasks : null;
    for (const daily of [state && state.daily_tasks, synced]) {
      if (!daily || seen.has(daily)) continue;
      seen.add(daily);
      for (const g of daily.groups || []) {
        let found = false;
        for (const key of ["items", "open_items"]) {
          for (const it of g[key] || []) {
            if (String(it.task_id || it.id || "").trim() === tid) {
              it.completed = !!completed;
              found = true;
            }
          }
        }
        if (g.items) {
          g.open_items = g.items.filter((x) => !x.completed);
          g.done = g.items.filter((x) => x.completed).length;
          g.total = g.items.length;
          g.completed = g.total > 0 && g.done === g.total;
        } else if (found) {
          g.done = Math.max(0, (g.done || 0) + (completed ? 1 : -1));
          if (g.total) g.done = Math.min(g.done, g.total);
          g.completed = g.total > 0 && g.done === g.total;
          g.open_items = (g.open_items || []).filter((x) => !x.completed);
        }
      }
      if (daily.summary) {
        let done = 0;
        let total = 0;
        for (const g of daily.groups || []) {
          done += g.done || 0;
          total += g.total || 0;
        }
        daily.summary.done = done;
        daily.summary.total = total;
      }
    }
  }

  function paintQuestMeter(groupEl) {
    const daily =
      (state && state.daily_tasks) ||
      (typeof lastSyncedDailyTasks !== "undefined" ? lastSyncedDailyTasks : null);
    const s = daily && daily.summary;
    if (s) {
      const pct = s.total ? Math.round((s.done / s.total) * 100) : 0;
      const count = document.querySelector(".daily-quests-count");
      if (count) count.textContent = `${s.done}/${s.total}`;
      const fill = document.querySelector(".daily-quests-meter-fill");
      if (fill) fill.style.width = `${pct}%`;
    }
    const prog = groupEl && groupEl.querySelector(".quest-group-prog");
    if (prog && groupEl) {
      const gName = (groupEl.getAttribute && groupEl.getAttribute("data-group")) || "";
      const g = ((daily && daily.groups) || []).find((x) => (x.group || "") === gName);
      if (g) prog.textContent = `${g.done || 0}/${g.total || 0}`;
    }
  }

  function applyQuestUncheckToSessions(sessions, log, today) {
    const list = Array.isArray(sessions) ? sessions : [];
    if (!log || log.action !== "uncheck_remove") return list;
    const name = String(log.name || "").trim().toLowerCase();
    if (!name) return list;
    const day = String(today || "").slice(0, 10);
    return list.map((sess) => {
      const sDay = String((sess && sess.date) || "").slice(0, 10);
      if (day && sDay && sDay !== day) return sess;
      const exercises = (sess && sess.exercises) || [];
      const idx = exercises.findIndex((ex) => {
        if (String((ex && ex.name) || "").trim().toLowerCase() !== name) return false;
        return !!(ex && (ex.quest_seeded || String(ex.raw || "").startsWith("quest-seeded:")));
      });
      if (idx < 0) return sess;
      return { ...sess, exercises: exercises.filter((_, i) => i !== idx) };
    });
  }

  function applyWorkoutLogToLocalState(log) {
    if (!log || log.action !== "uncheck_remove") return;
    if (!state) return;
    const today = String(
      (state.meta && state.meta.local_today) ||
        (state.coach && state.coach.today && state.coach.today.date) ||
        ""
    ).slice(0, 10);
    state.sessions = applyQuestUncheckToSessions(state.sessions || [], log, today);
    if (typeof renderHistory === "function") renderHistory(state.sessions);
  }

  async function onDailyQuestClick(ev) {
    const btn = ev.target.closest && ev.target.closest(".quest-card");
    if (!btn || btn.classList.contains("is-completing")) return;
    const taskId = (btn.getAttribute("data-task-id") || "").trim();
    const listId = (btn.getAttribute("data-list-id") || "").trim();
    const parentId = (btn.getAttribute("data-parent-id") || "").trim();
    const groupEl = btn.closest && btn.closest(".quest-group");
    const questGroup = (
      btn.getAttribute("data-group")
      || (groupEl && groupEl.getAttribute && groupEl.getAttribute("data-group"))
      || ""
    ).trim();
    const questTitle = (btn.getAttribute("data-title") || "").trim();
    const questSlug = (btn.getAttribute("data-slug") || "").trim();
    const isLift = looksLikeLiftQuest(questGroup, questTitle, questSlug);
    const wantCompleted = !btn.classList.contains("is-done");
    if (!wantCompleted && !isLift) return;
    const todayWo =
      (state && state.coach && state.coach.today && state.coach.today.workout) || {};
    // Leaf with both ids is a real control even if a stale pending/disabled paint remains.
    if (!taskId || !listId) {
      if (btn.getAttribute("aria-disabled") === "true" || btn.classList.contains("quest-card-pending")) {
        showAlert((state && state.daily_tasks && state.daily_tasks.error) || "Quest is not synced to Google Tasks yet", "err");
      }
      return;
    }
    ev.preventDefault();
    btn.classList.remove("quest-card-pending");
    btn.removeAttribute("aria-disabled");
    btn.disabled = true;
    btn.classList.add("is-completing");
    btn.setAttribute("aria-busy", "true");
    const remaining = groupEl
      ? Array.from(groupEl.querySelectorAll(".quest-card:not(.is-completing):not(.is-done)"))
      : [];
    const siblingAllDone = wantCompleted && remaining.length === 0;
    try {
      const res = await fetch("/api/daily-tasks/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          list_id: listId,
          task_id: taskId,
          completed: wantCompleted,
          parent_id: parentId || null,
          sibling_all_done: siblingAllDone,
          group: questGroup || null,
          title: questTitle || null,
          slug: questSlug || null,
          session_type: todayWo.session_type || null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        unlockQuestCard(btn);
        const hint =
          res.status === 405
            ? "method_not_allowed"
            : res.status === 404
              ? "complete_not_found"
              : data.error || (wantCompleted ? "Could not complete quest" : "Could not uncheck quest");
        showAlert(hint, "err");
        return;
      }
      const log = data.workout_log || {};
      patchLocalQuestCompleted(taskId, wantCompleted);
      if (wantCompleted) {
        btn.classList.add("is-done");
        btn.setAttribute("aria-pressed", "true");
        if (isLift) {
          const label = questTitle || btn.textContent || "";
          btn.setAttribute("aria-label", `Uncheck: ${label}`);
          unlockQuestCard(btn);
          paintQuestMeter(groupEl);
        } else {
          btn.removeAttribute("aria-busy");
          setTimeout(() => {
            btn.remove();
            if (groupEl && !groupEl.querySelector(".quest-card")) {
              groupEl.classList.add("is-done");
              const doneEl = document.createElement("div");
              doneEl.className = "quest-group-done";
              doneEl.textContent = "Cleared ✓";
              groupEl.appendChild(doneEl);
            }
            paintQuestMeter(groupEl);
          }, 280);
        }
      } else {
        btn.classList.remove("is-done");
        btn.setAttribute("aria-pressed", "false");
        btn.setAttribute("aria-label", `Complete: ${questTitle || btn.textContent || ""}`);
        unlockQuestCard(btn);
        if (groupEl) groupEl.classList.remove("is-done");
        const doneNote = groupEl && groupEl.querySelector(".quest-group-done");
        if (doneNote) doneNote.remove();
        paintQuestMeter(groupEl);
        applyWorkoutLogToLocalState(log);
      }
    } catch (e) {
      unlockQuestCard(btn);
      showAlert(String(e.message || e), "err");
    }
  }

  function bindDailyQuestClicks() {
    // Document-level so m-shell re-layouts / innerHTML quest rewrites never drop the handler.
    if (document.documentElement.dataset.questDelegated === "1") return;
    document.documentElement.dataset.questDelegated = "1";
    document.addEventListener("click", onDailyQuestClick);
    document.addEventListener("click", (ev) => {
      const btn = ev.target.closest && ev.target.closest("[data-quest-retry]");
      if (!btn) return;
      ev.preventDefault();
      syncDailyTasksFromServer();
    });
  }

  /** Event-delegated collapse — survives quest re-renders after GT sync. */
  function onCollapsibleHeadClick(ev) {
    if (ev.target.closest && ev.target.closest(".quest-card")) return;
    const head = ev.target.closest && ev.target.closest("[data-collapse]");
    if (!head) return;
    // Head is the control; ignore clicks that only bubble through from outside
    if (!head.contains(ev.target)) return;
    const key = head.getAttribute("data-collapse");
    if (!key) return;
    const root = $("today-hub") || document;
    const body =
      root.querySelector(`[data-collapse-body="${key}"]`) ||
      document.querySelector(`[data-collapse-body="${key}"]`);
    if (!body) return;
    ev.preventDefault();
    ev.stopPropagation();
    const open = head.getAttribute("aria-expanded") !== "false";
    const nextOpen = !open;
    head.setAttribute("aria-expanded", nextOpen ? "true" : "false");
    body.hidden = !nextOpen;
    head.classList.toggle("is-collapsed", !nextOpen);
    collapseOpen[key] = nextOpen;
    persistCollapseOpen();
  }

  function applyStaticCollapseState(root) {
    const el = root || $("today-hub") || document;
    el.querySelectorAll("[data-collapse]").forEach((head) => {
      const key = head.getAttribute("data-collapse");
      if (!key || key === "quests") return; // quests re-rendered with state baked in
      if (!(key in collapseOpen)) return;
      // Strict true only — default-closed keys (lift/meal/targets) stay shut
      // unless the user explicitly expanded them this session.
      const open = collapseOpen[key] === true;
      head.setAttribute("aria-expanded", open ? "true" : "false");
      head.classList.toggle("is-collapsed", !open);
      const body =
        el.querySelector(`[data-collapse-body="${key}"]`) ||
        document.querySelector(`[data-collapse-body="${key}"]`);
      if (body) body.hidden = !open;
    });
  }

  function bindCollapsibles(root) {
    // Document-level so re-parenting / partial hub rewrites never drop the handler
    if (document.documentElement.dataset.collapseDelegated === "1") {
      applyStaticCollapseState(root);
      return;
    }
    document.documentElement.dataset.collapseDelegated = "1";
    document.addEventListener("click", onCollapsibleHeadClick);
    applyStaticCollapseState(root);
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
    const all = (store.food_logs || []).length
      ? store.food_logs
      : ((((data.health || {}).food_logs) || []).length
          ? data.health.food_logs
          : (store.food_logs_recent || store.food_logs_today || []));
    let logs = today ? all.filter((f) => civilDay(f.date) === today) : (store.food_logs_today || []);
    let heading = "Logged today";
    if (!logs.length) {
      const last = lastNutritionDate(data) || "";
      if (last) {
        logs = all.filter((f) => civilDay(f.date) === last);
        if (logs.length) heading = `Logged ${last}`;
      }
    }
    if (!box) return logs;
    if (!logs.length) {
      box.textContent =
        `local_today=${today || "—"}; last_nutrition_date=${lastNutritionDate(data) || "—"}; food_log_count=${all.length || 0}`;
      return logs;
    }
    box.innerHTML =
      `<div class="today-subh" style="font-size:0.8rem;margin-bottom:0.3rem">${heading}</div>` +
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

  function renderTodayHub(data) {
    const coach = data.coach || {};
    const today = coach.today || {};
    const adh = coach.adherence_7d || {};
    // coach.brief / coach.weekly_review still on payload for Ask Grok — not rendered on Today
    const nutStore = data.nutrition_store || {};

    if ($("today-hub-date")) {
      const rec = today.recommendation || "—";
      $("today-hub-date").textContent =
        `${today.date || (data.meta && data.meta.local_today) || ""} · recommendation: ${rec}`;
    }
    if ($("today-headline")) {
      $("today-headline").textContent =
        today.headline ||
        (today.motivations && today.motivations.overview) ||
        "";
    }
    if ($("today-overview")) {
      $("today-overview").textContent =
        (today.motivations && today.motivations.overview) ||
        "Rebuilt each load from live logs, stock, recovery, and planners.";
    }

    // Daily quests: paint local plan immediately; GT ensure only on first
    // Today load of a new local date (or retry). Refresh data / quiet poll
    // / meal rebuild must not re-run the rollover sweep.
    const incoming = today.daily_tasks || data.daily_tasks || null;
    const day = civilDay((data.meta && data.meta.local_today) || today.date || "");
    const alreadySyncedToday = !!(lastQuestSyncedDay && day && lastQuestSyncedDay === day);
    const daily =
      alreadySyncedToday && lastSyncedDailyTasks ? lastSyncedDailyTasks : incoming;
    // Bind collapse + quest delegation once (before/after quest re-renders)
    bindCollapsibles($("today-hub") || document);
    bindDailyQuestClicks();
    renderDailyPlanTasks(daily, today.actions || []);
    if (
      !alreadySyncedToday &&
      (!daily || daily.needs_sync || daily.source !== "google_tasks")
    ) {
      syncDailyTasksFromServer().then((ok) => {
        if (ok && day) lastQuestSyncedDay = day;
      });
    }

    // Targets with motivations + progress
    if ($("today-targets")) {
      const rows = today.targets || [];
      if (!rows.length) {
        // Fallback from nutrition_store
        const t = nutStore.targets || {};
        const c = loggedConsumedForToday(data) || nutStore.today_consumed || {};
        $("today-targets").innerHTML = `
          <div class="today-target-row"><div class="today-target-top">
            <span>Calories</span><span>${fmtNum(c.calories)} / ${fmtNum(t.calories)}</span>
          </div></div>`;
      } else {
        $("today-targets").innerHTML = rows
          .map((r) => {
            const unit = r.unit === "kcal" ? "" : r.unit || "";
            const pct =
              r.pct != null ? ` · ${r.pct}%` : "";
            return `<div class="today-target-row">
              <div class="today-target-top">
                <span>${r.label || r.id}</span>
                <span>${fmtNum(r.consumed)}${unit ? " " + unit : ""} / ${fmtNum(r.target)}${
              unit ? " " + unit : ""
            }${pct}</span>
              </div>
              <div class="muted" style="font-size:0.78rem">${fmtNum(r.remaining)}${
              unit ? " " + unit : ""
            } left</div>
              ${
                r.motivation
                  ? `<div class="today-target-why">${r.motivation}</div>`
                  : ""
              }
            </div>`;
          })
          .join("");
      }
    }

    // One-line badge only — full Recovery + sleep-battery card lives above pacing on Today
    if ($("today-recovery")) {
      const r = today.recovery || data.recovery || {};
      $("today-recovery").innerHTML = `
        <div class="badge ${recoveryClass(r.label)}">${r.label || "—"} · ${r.score != null ? Math.round(r.score) : "—"}</div>
        ${r.motivation ? `<p class="muted" style="font-size:0.8rem;margin:0.35rem 0 0">${r.motivation}</p>` : ""}
      `;
    }
    if ($("today-adherence")) {
      const p = adh.protein || {};
      const s = adh.sleep || {};
      const h = adh.hydration || {};
      const c = adh.calories || {};
      // Prefer nested objects from adherence_7d; fall back to today.adherence flat pcts
      const flat = today.adherence_7d || {};
      const fmt = (block, label, flatKey) => {
        if (block && block.pct != null)
          return `${label} ${block.pct}% (${block.hits ?? "—"}/${block.days_logged ?? "—"})`;
        if (flat[flatKey] != null) return `${label} ${flat[flatKey]}%`;
        return `${label} —`;
      };
      $("today-adherence").innerHTML = `
        <strong>7-day adherence</strong><br/>
        ${fmt(p, "Protein", "protein_pct")} · ${fmt(c, "Calories", "calories_pct")}<br/>
        ${fmt(s, "Sleep", "sleep_pct")} · ${fmt(h, "Hydration", "hydration_pct")}
      `;
    }

    // Meal plan from coach.today.meal (stock-only planner)
    if ($("today-meal")) {
      const meal = today.meal || {};
      const meals = meal.meals || [];
      const items = meal.items || [];
      let html = "";
      if (meal.empty || (!meals.length && !items.length)) {
        let copy = meal.message || "";
        if (meal.pantry_dark || meal.empty_reason === "pantry_unavailable") {
          copy = "Pantry unavailable";
        } else if (meal.stocked_count === 0 || meal.empty_reason === "no_in_stock") {
          copy = "No in-stock items";
        } else if (!copy) {
          copy = "No in-stock items";
        }
        html += `<p class="muted" style="margin:0 0 0.4rem;font-size:0.82rem">${copy}</p>`;
      } else if (meal.message) {
        html += `<p class="muted" style="margin:0 0 0.4rem;font-size:0.82rem">${meal.message}</p>`;
      }
      if (meals.length) {
        meals.forEach((bucket) => {
          const its = bucket.items || [];
          const clock = mealBucketClock(bucket);
          const clockBit = clock
            ? ` · <span class="today-meal-bucket-time">${clock}</span>`
            : "";
          html += `<div class="today-meal-bucket">
            <div class="today-meal-bucket-label">${bucket.label || "Meal"}${clockBit}</div>
            <ul style="margin:0;padding-left:1.1rem">`;
          its.forEach((it) => {
            const serve = formatPlanPortion(it);
            html += `<li><strong>${it.name || "Item"}</strong> · ${serve}
              · ${fmtNum(it.calories)} kcal · P${fmtNum(it.protein_g)}</li>`;
          });
          html += `</ul></div>`;
        });
      } else if (items.length) {
        html += `<ul style="margin:0;padding-left:1.1rem">`;
        items.forEach((it) => {
          html += `<li><strong>${it.name}</strong> · ${fmtNum(it.calories)} kcal · P${fmtNum(it.protein_g)}</li>`;
        });
        html += `</ul>`;
      }
      const pt = meal.planned_totals || {};
      if (pt.calories != null) {
        html += `<p class="muted" style="margin:0.45rem 0 0;font-size:0.82rem">
          Planned add: ${fmtNum(pt.calories)} kcal · P${fmtNum(pt.protein_g)}
          C${fmtNum(pt.carbs_g)} F${fmtNum(pt.fat_g)}
        </p>`;
      }
      $("today-meal").innerHTML = html;
    }

    if ($("today-purchases")) {
      const purchases = today.purchases || [];
      if (!purchases.length) {
        $("today-purchases").innerHTML =
          `<p class="muted" style="margin:0;font-size:0.82rem">No restock/add suggestions right now.</p>`;
      } else {
        $("today-purchases").innerHTML =
          `<div class="today-subh" style="font-size:0.8rem;margin-bottom:0.3rem">Buy / restock</div>` +
          purchases
            .slice(0, 6)
            .map((p) => {
              const badge =
                p.action === "restock"
                  ? `<span class="badge-restock">restock</span>`
                  : `<span class="badge-add">add</span>`;
              return `<div class="today-purchase">${badge} <strong>${
                p.name || "Item"
              }</strong>
                <div class="muted" style="font-size:0.78rem">${p.reason || ""}</div></div>`;
            })
            .join("");
      }
    }

    // Lead only — full exercise list lives in #workout-plan-result (single source)
    if ($("today-workout")) {
      const w =
        today.workout ||
        (data.workout_store && data.workout_store.plan) ||
        {};
      const focus = w.focus || {};
      const focusMuscles = Array.isArray(focus.muscles) ? focus.muscles : [];
      let focusLine = "";
      if (focusMuscles.length) {
        focusLine = `<p class="muted" style="font-size:0.82rem;margin:0.3rem 0">
          Focus: <strong>${focusMuscles
            .map((m) => String(m).replace(/_/g, " "))
            .join(", ")}</strong>${
          focus.reason ? ` — ${focus.reason}` : ""
        }</p>`;
      }
      const why = w.motivation
        ? `<p class="muted" style="font-size:0.8rem;margin:0.25rem 0 0.4rem">${w.motivation}</p>`
        : "";
      const rec = today.recommendation || w.recommendation || "train";
      if (w.is_rest_day || rec === "rest") {
        $("today-workout").innerHTML = `
          ${why}
          <p><strong>Rest day</strong> — ${w.message || "Recovery below threshold."}</p>
          ${focusLine}
        `;
      } else {
        $("today-workout").innerHTML = `
          ${why}
          <p class="muted" style="margin:0;font-size:0.85rem">
            Mode: <strong>${rec}</strong>
            · prescription below · use <em>Plan controls</em> to refresh or force session
          </p>
          ${focusLine}
        `;
      }
    }
    const loggedFoods = renderTodayLoggedFoods(data);
    if ($("today-macros")) {
      const n = today.nutrition || {};
      const cons = loggedConsumedForToday(data) || {};
      const rem = n.remaining || {};
      const localToday = civilDay((data.meta && data.meta.local_today) || today.date || "");
      const nLogs =
        cons.food_log_count != null
          ? cons.food_log_count
          : n.food_log_count != null
          ? n.food_log_count
          : loggedFoods.length;
      const pace =
        (today.calorie_bars && today.calorie_bars.pacing_summary) ||
        ((data.calorie_bars || {}).pacing || {}).summary ||
        "";
      const delta =
        (today.calorie_bars && today.calorie_bars.delta_summary) ||
        ((data.calorie_bars || {}).delta || {}).summary ||
        "";
      if (!hasLoggedMacros(cons)) {
        const last = lastNutritionDate(data) || "—";
        $("today-macros").innerHTML =
          `local_today=${localToday || "—"}; last_nutrition_date=${last}; food_log_count=${nLogs || 0}`;
      } else {
        const tgt = n.targets || nutStore.targets || {};
        const hasTargets = tgt.calories != null || tgt.protein_g != null;
        const hasRem =
          hasTargets &&
          (rem.calories != null ||
            rem.protein_g != null ||
            rem.carbs_g != null ||
            rem.fat_g != null);
        $("today-macros").innerHTML = `
        <strong>Logged so far</strong>${
          nLogs !== "" && nLogs != null ? ` (${nLogs} meal log${nLogs === 1 ? "" : "s"})` : ""
        }: ${fmtNum(cons.calories)} kcal · P${fmtNum(cons.protein_g)}
        C${fmtNum(cons.carbs_g)} F${fmtNum(cons.fat_g)}
        ${
          hasRem
            ? `<br/><strong>Remaining</strong>: ${fmtNum(rem.calories)} kcal · P${fmtNum(
                rem.protein_g
              )} C${fmtNum(rem.carbs_g)} F${fmtNum(rem.fat_g)}`
            : ""
        }
        ${pace ? `<br/><span style="font-size:0.8rem">${pace}</span>` : ""}
        ${delta ? `<br/><span style="font-size:0.8rem">${delta}</span>` : ""}
      `;
      }
    }
  }

  function prefillsFromWorkoutPlan(plan) {
    if (!plan || plan.is_rest_day) return [];
    return (plan.exercises || []).map((ex) => {
      const rx = ex.prescription || {};
      return {
        name: ex.name,
        sets: [
          {
            weight_lbs: rx.weight_lbs != null ? rx.weight_lbs : "",
            sets: rx.sets != null ? rx.sets : 3,
            reps: rx.reps != null ? rx.reps : 10,
          },
        ],
      };
    });
  }

  /* ---------- Mobile tab shell (≤720px) ---------- */
  /** Narrow phone density (CSS still uses 720px). Tab shell is always on. */
  const NARROW_MQ = "(max-width: 720px)";
  let mobileActiveTab = "today";

  function isNarrowViewport() {
    try {
      return window.matchMedia(NARROW_MQ).matches;
    } catch (_) {
      return false;
    }
  }

  /** Unified tab shell on web + phone — same Today / Trends / Kitchen / Log / More. */
  function useTabShell() {
    return true;
  }

  /** @deprecated alias — kept so any residual callers keep working */
  function isMobileShell() {
    return useTabShell();
  }

  function goMobileTab(tab) {
    mobileActiveTab = tab || "today";
    document.body.classList.add("m-shell");
    document.body.dataset.mActive = mobileActiveTab;
    const bar = $("mobile-tabbar");
    if (bar) {
      bar.hidden = false;
      bar.querySelectorAll(".tab-btn").forEach((btn) => {
        const on = btn.getAttribute("data-m-tab") === mobileActiveTab;
        btn.classList.toggle("active", on);
        if (on) btn.setAttribute("aria-current", "page");
        else btn.removeAttribute("aria-current");
      });
    }
    // Connections (Google reconnect) lives under More via data-m-panel — no extra hide logic
    syncMacroStripVisibility();
    window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    // After layout paints, resize charts that were built while hidden
    requestAnimationFrame(() => {
      requestAnimationFrame(() => resizeAllCharts());
    });
  }

  /** Map Lift/Meal/Targets pill → collapsible key in the hub grid. */
  const TODAY_PILL_COLLAPSE = {
    lift: "lift",
    meal: "meal-sum",
    targets: "targets",
  };

  function setPanelCollapse(key, open) {
    if (!key) return;
    const root = $("today-hub") || document;
    const head =
      root.querySelector(`[data-collapse="${key}"]`) ||
      document.querySelector(`[data-collapse="${key}"]`);
    const body =
      root.querySelector(`[data-collapse-body="${key}"]`) ||
      document.querySelector(`[data-collapse-body="${key}"]`);
    if (!head || !body) return;
    const next = !!open;
    head.setAttribute("aria-expanded", next ? "true" : "false");
    head.classList.toggle("is-collapsed", !next);
    body.hidden = !next;
    collapseOpen[key] = next;
    persistCollapseOpen();
  }

  function setTodayPill(name) {
    const pills = $("today-mobile-pills");
    if (!pills) return;
    pills.querySelectorAll(".pill").forEach((p) => {
      const on = p.getAttribute("data-today-pill") === name;
      p.classList.toggle("active", on);
      p.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll("#today-hub-grid .today-panel").forEach((panel) => {
      panel.classList.toggle(
        "today-panel-active",
        panel.getAttribute("data-today-panel") === name
      );
    });
    // When switching pills: force inactive sections collapsed so Meal/Targets
    // don't stay open from a previous visit. Active section keeps preference
    // (defaults all closed — user expands the subhead if they want content).
    Object.entries(TODAY_PILL_COLLAPSE).forEach(([pill, key]) => {
      if (pill === name) {
        setPanelCollapse(key, collapseOpen[key] === true);
      } else {
        // Leaving a pill always collapses it so the next visit starts closed
        setPanelCollapse(key, false);
      }
    });
  }

  function syncMobileShell() {
    const bar = $("mobile-tabbar");
    const pills = $("today-mobile-pills");
    document.body.classList.add("m-shell");
    if (bar) bar.hidden = false;
    if (pills) pills.hidden = false;
    goMobileTab(mobileActiveTab);
    const activePill = document.querySelector(
      "#today-hub-grid .today-panel.today-panel-active"
    );
    setTodayPill(
      (activePill && activePill.getAttribute("data-today-panel")) || "lift"
    );
  }

  function initMobileShell() {
    syncMobileShell();
    // Re-apply admin card visibility when crossing narrow breakpoint
    try {
      window.matchMedia(NARROW_MQ).addEventListener("change", syncMobileShell);
    } catch (_) {
      window.addEventListener("resize", syncMobileShell);
    }
    const bar = $("mobile-tabbar");
    if (bar) {
      bar.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-m-tab]");
        if (!btn) return;
        goMobileTab(btn.getAttribute("data-m-tab"));
      });
    }
    const pills = $("today-mobile-pills");
    if (pills) {
      pills.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-today-pill]");
        if (!btn) return;
        setTodayPill(btn.getAttribute("data-today-pill"));
      });
    }
    // More-tab admin mirrors desktop header buttons (phone only)
    if ($("btn-refresh-mobile")) {
      $("btn-refresh-mobile").addEventListener("click", () => loadDashboard(true));
    }
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    // Only register on secure contexts / localhost / private IPs used over Tailscale
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        /* offline shell is best-effort */
      });
    });
  }

  /** True after auth/status says we may load personal APIs (signed in, or legacy no-auth). */
  let bootAllowsData = false;
  /** First blocking /api/dashboard has settled (ok or fail). Quiet polls never flip this. */
  let firstDashboardSettled = false;

  function setFirstLoadVisible(visible, message) {
    const shell = $("app-shell");
    const el = $("today-first-load");
    if (shell) shell.classList.toggle("is-first-loading", !!visible);
    if (el) {
      el.hidden = !visible;
      el.inert = !visible;
      el.setAttribute("aria-hidden", visible ? "false" : "true");
      el.setAttribute("aria-busy", visible ? "true" : "false");
    }
    const msg = $("today-first-load-msg");
    if (msg && message) msg.textContent = message;
  }

  function finishFirstDashboardLoad() {
    if (firstDashboardSettled) return;
    firstDashboardSettled = true;
    setFirstLoadVisible(false);
  }

  function showLoginGate(message) {
    bootAllowsData = false;
    finishFirstDashboardLoad();
    clearAlerts();
    const gate = $("auth-gate");
    const shell = $("app-shell");
    const tabbar = $("mobile-tabbar");
    if (gate) gate.hidden = false;
    if (shell) shell.hidden = true;
    if (tabbar) tabbar.hidden = true;
    const err = $("auth-gate-error");
    if (err) {
      const params = new URLSearchParams(window.location.search);
      err.textContent = message || params.get("auth_error") || "";
    }
  }

  function showAppShell(user) {
    const gate = $("auth-gate");
    const shell = $("app-shell");
    if (gate) gate.hidden = true;
    if (shell) shell.hidden = false;
    if (!firstDashboardSettled) setFirstLoadVisible(true, "Loading Today…");
    const line = $("auth-user-line");
    if (line && user) {
      line.textContent = user.email
        ? `Signed in as ${user.display_name || user.email} · ${user.email}`
        : `Signed in · ${user.display_name || user.id || ""}`;
    }
  }

  function isAuthRequiredError(res, data, errMsg) {
    if (res && res.status === 401) return true;
    if (data && (data.error === "auth_required" || data.error === "unauthorized")) return true;
    const m = String(errMsg || "");
    return /HTTP 401|auth_required|unauthorized|session expired/i.test(m);
  }

  async function checkAuthAndBoot() {
    try {
      const res = await fetch("/api/auth/status", { cache: "no-store", credentials: "same-origin" });
      const st = await res.json();
      if (!st.auth_required) {
        // Legacy mode: show app without Google login
        bootAllowsData = true;
        showAppShell({ display_name: "local", email: "" });
        refreshAskAuthStatus();
        loadDashboard(false);
        return;
      }
      if (!st.authenticated) {
        // Expected path when signed out — login only, no dashboard fetch
        showLoginGate();
        return;
      }
      bootAllowsData = true;
      showAppShell(st.user);
      refreshAskAuthStatus();
      loadDashboard(false);
    } catch (e) {
      showLoginGate(`Auth check failed: ${e.message}`);
    }
  }

  let quietLoadInFlight = false;
  let blockingLoadInFlight = false;
  let dashboardLoadGen = 0;
  let lastLiveFingerprint = "";
  const QUIET_POLL_MS = 3 * 60 * 1000;

  async function loadDashboard(forceRefresh = false, opts) {
    // Guard: if used as a raw click handler, first arg is an Event (truthy).
    if (forceRefresh && typeof forceRefresh !== "boolean") {
      forceRefresh = false;
    }
    const quiet = !!(opts && opts.quiet);
    // Never hit /api/dashboard until boot confirmed auth (avoids 401 toast on cold open)
    if (!bootAllowsData) {
      if (!quiet) showLoginGate();
      return;
    }
    if (quiet && (quietLoadInFlight || blockingLoadInFlight)) return;
    if (quiet) quietLoadInFlight = true;
    else {
      blockingLoadInFlight = true;
      dashboardLoadGen += 1;
    }
    const loadGen = dashboardLoadGen;
    if (!quiet && $("btn-refresh")) $("btn-refresh").disabled = true;
    // Don't wipe success toasts from inventory remove/add mid-action.
    const meta = $("meta-line");
    const started = Date.now();
    let tick = null;
    if (!quiet && !firstDashboardSettled) {
      setFirstLoadVisible(true, forceRefresh ? "Refreshing Today…" : "Loading Today…");
    }
    if (!quiet) {
      if (meta) {
        meta.textContent = forceRefresh
          ? "Refreshing Google Health (forced)…"
          : "Loading dashboard (local + cache)…";
      }
      tick = setInterval(() => {
        const sec = Math.round((Date.now() - started) / 1000);
        if (meta) {
          meta.textContent = forceRefresh
            ? `Refreshing data… ${sec}s`
            : `Loading… ${sec}s (uses ~1h cache for Health)`;
        }
        if (!firstDashboardSettled) {
          setFirstLoadVisible(
            true,
            forceRefresh ? `Refreshing Today… ${sec}s` : `Loading Today… ${sec}s`
          );
        }
      }, 500);
    }
    try {
      const url = forceRefresh === true ? "/api/dashboard?refresh=1" : "/api/dashboard";
      const res = await fetch(url, { cache: "no-store", credentials: "same-origin" });
      if (res.status === 401) {
        showLoginGate("Session expired — sign in again.");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (isAuthRequiredError(res, data)) {
        showLoginGate("Session expired — sign in again.");
        return;
      }
      // Soft errors (partial data) live under meta.error — still render.
      if (data.error && !data.sessions && !data.meta) {
        throw new Error(data.error);
      }
      if (quiet && (blockingLoadInFlight || loadGen !== dashboardLoadGen)) return;
      const fp = liveFingerprint(data);
      if (quiet && fp === lastLiveFingerprint) {
        return;
      }
      lastLiveFingerprint = fp;
      render(data, { quiet });
      if (!quiet && data.meta && data.meta.error) {
        showAlert(`Partial load: ${data.meta.error}`, "warn");
      }
    } catch (e) {
      if (isAuthRequiredError(null, null, e && e.message)) {
        showLoginGate("Session expired — sign in again.");
        return;
      }
      if (quiet) return;
      clearAlerts();
      showAlert(`Failed to load dashboard: ${e.message}`, "err");
      if (meta) meta.textContent = `Load failed: ${e.message}`;
    } finally {
      if (tick) clearInterval(tick);
      if (!quiet && $("btn-refresh")) $("btn-refresh").disabled = false;
      if (quiet) quietLoadInFlight = false;
      else {
        blockingLoadInFlight = false;
        finishFirstDashboardLoad();
      }
    }
  }

  function startQuietPoll() {
    if (window.__fitdashQuietPoll) return;
    window.__fitdashQuietPoll = true;
    const tick = () => {
      if (document.visibilityState !== "visible") return;
      loadDashboard(false, { quiet: true });
    };
    setInterval(tick, QUIET_POLL_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") tick();
    });
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
        throw new Error(data.message || data.error || `HTTP ${res.status}`);
      }
      const prs = data.auto_prs || [];
      const prBit = prs.length ? ` · PRs: ${prs.join(", ")}` : "";
      const write = data.write || {};
      status.textContent = `Saved to ${write.path || "turso"} · verified=${write.verified_on_readback}${prBit}`;
      showAlert(
        prs.length
          ? `Workout saved. Auto-PR: ${prs.join(", ")}`
          : "Workout logged and re-read successfully.",
        "ok"
      );
      await loadDashboard(false);
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
    const servingGRaw = ($("ing-serving-g") && $("ing-serving-g").value.trim()) || "";
    const servingG = servingGRaw === "" ? null : Number(servingGRaw);
    const body = {
      name: $("ing-name").value.trim(),
      category: $("ing-category").value,
      serving_label: ($("ing-serving") && $("ing-serving").value.trim()) || "",
      calories: Number($("ing-cal").value),
      protein_g: Number($("ing-p").value),
      carbs_g: Number($("ing-c").value),
      fat_g: Number($("ing-f").value),
      in_stock: true,
    };
    // Prefer weighable grams; macros apply to this mass. Never invent grams.
    if (Number.isFinite(servingG) && servingG > 0) {
      body.serving_g = servingG;
      if (!body.serving_label) body.serving_label = `${Math.round(servingG)}g`;
    } else if (!body.serving_label) {
      body.serving_label = "1 serving";
    }
    const gramsErr = assertServingGramsOrExplain(body);
    if (gramsErr) {
      if (status) status.textContent = "";
      const gramsInput = $("ing-serving-g");
      if (gramsInput) gramsInput.focus();
      showAlert(gramsErr, "err");
      return;
    }
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
      if ($("ing-serving-g")) $("ing-serving-g").value = "";
      if ($("ing-serving")) $("ing-serving").value = "";
      if (data.inventory) {
        applyInventoryUpdate(data.inventory);
      }
      try {
        await generatePlan();
      } catch (_) {
        /* optional */
      }
    } catch (e) {
      if (status) status.textContent = "";
      showAlert(`Inventory save failed: ${e.message}`, "err");
    }
  }

  async function submitTargets(ev) {
    ev.preventDefault();
    const status = $("targets-status");
    if (status) status.textContent = "Saving…";
    const wgRaw = $("tgt-weight-goal") ? $("tgt-weight-goal").value : "";
    const body = {
      calories: Number($("tgt-cal").value),
      protein_g: Number($("tgt-p").value),
      carbs_g: Number($("tgt-c").value),
      fat_g: Number($("tgt-f").value),
      // Empty string clears the goal; omit is not used so chart stays in sync
      weight_goal_lbs: wgRaw === "" || wgRaw == null ? null : Number(wgRaw),
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

  function setMealPlanBusy(busy) {
    const banner = $("meal-plan-refreshing");
    if (banner) banner.hidden = !busy;

    const btn = $("btn-generate-meal");
    if (btn) {
      btn.disabled = !!busy;
      if (busy) btn.setAttribute("aria-busy", "true");
      else btn.removeAttribute("aria-busy");
    }

    const el = $("meal-plan-result");
    if (!el) return;
    el.classList.toggle("is-refreshing", !!busy);
    if (busy) el.setAttribute("aria-busy", "true");
    else el.removeAttribute("aria-busy");
  }

  /** Rebuild rest-of-day meal plan after inventory add/edit/remove/stock. Meal only. */
  async function generatePlan() {
    const box = $("meal-plan-result");
    if (box && box.getAttribute("aria-busy") === "true") return;
    setMealPlanBusy(true);
    try {
      const res = await fetch("/api/meal-plan/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      renderMealPlan(data.plan);
    } catch (e) {
      showAlert(`Meal plan failed: ${e.message}`, "err");
    } finally {
      setMealPlanBusy(false);
    }
  }
  window.generateMealPlan = generatePlan;

  const WORKOUT_PLAN_TRIGGER_IDS = [
    "btn-generate-workout",
    "btn-force-session-push",
    "btn-force-session-pull",
    "btn-force-session-legs",
  ];
  const WORKOUT_PLAN_REFRESH_LABEL = "Refreshing plan…";
  const WORKOUT_PLAN_IDLE_LABEL = "Refresh plan";

  function setWorkoutPlanBusy(busy) {
    WORKOUT_PLAN_TRIGGER_IDS.forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.disabled = !!busy;
      if (busy) {
        el.setAttribute("aria-busy", "true");
        el.classList.add("is-refreshing");
      } else {
        el.removeAttribute("aria-busy");
        el.classList.remove("is-refreshing");
      }
      if (id !== "btn-generate-workout") return;
      if (busy) {
        if (el.dataset.idleLabel == null) el.dataset.idleLabel = el.textContent;
        el.textContent = WORKOUT_PLAN_REFRESH_LABEL;
      } else {
        el.textContent = el.dataset.idleLabel || WORKOUT_PLAN_IDLE_LABEL;
        delete el.dataset.idleLabel;
      }
    });

    const banner = $("workout-plan-refreshing");
    if (banner) banner.hidden = !busy;

    ["today-workout", "workout-plan-result"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.classList.toggle("is-refreshing", !!busy);
      if (busy) el.setAttribute("aria-busy", "true");
      else el.removeAttribute("aria-busy");
    });
  }

  async function generateWorkoutPlan(sessionType) {
    const btn = $("btn-generate-workout");
    if (btn && btn.getAttribute("aria-busy") === "true") return;
    setWorkoutPlanBusy(true);
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
      setWorkoutPlanBusy(false);
    }
  }

  async function submitEquipmentInventory(ev) {
    ev.preventDefault();
    const status = $("eq-status");
    const maxRaw = $("eq-max") && $("eq-max").value;
    const body = {
      name: $("eq-name").value.trim(),
      tag: $("eq-tag").value,
      max_weight_lbs: maxRaw === "" || maxRaw == null ? null : Number(maxRaw),
    };
    try {
      const res = await fetch("/api/equipment/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      if (status) status.textContent = "Saved";
      showAlert(`Gear saved: ${body.name}`, "ok");
      $("eq-name").value = "";
      $("eq-max").value = "";
      if (data.equipment && state && state.workout_store) {
        state.workout_store.equipment = data.equipment;
        renderEquipmentInventory(state.workout_store);
        renderExerciseCatalog(state.workout_store);
      }
      await loadDashboard(false);
    } catch (e) {
      if (status) status.textContent = "";
      showAlert(`Gear save failed: ${e.message}`, "err");
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

  function formatAskContent(text, role) {
    const raw = text == null ? "" : String(text);
    // User messages stay plain text (no HTML injection from the input box).
    if (role === "user" || typeof marked === "undefined" || !marked.parse) {
      const div = document.createElement("div");
      div.className = "ask-body ask-body-plain";
      div.textContent = raw;
      return div;
    }
    const div = document.createElement("div");
    div.className = "ask-body ask-md";
    try {
      if (typeof marked.setOptions === "function") {
        marked.setOptions({ gfm: true, breaks: true });
      }
      // marked@12 exposes marked.parse; older builds used marked() as a function.
      const html =
        typeof marked.parse === "function" ? marked.parse(raw) : marked(raw);
      div.innerHTML = html;
    } catch (_) {
      div.className = "ask-body ask-body-plain";
      div.textContent = raw;
    }
    return div;
  }

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
      const body = formatAskContent(turn.content, turn.role);
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
        el.textContent = data.error || "Grok auth not ready — Connect SuperGrok in More";
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
      // Local coach actions mutate targets/inventory/plans — refresh UI.
      if (data.action && data.action.ok) {
        bits.push("dashboard reloaded");
        try {
          await loadDashboard(false);
        } catch (_) {
          /* non-fatal */
        }
      }
      if (status) status.textContent = "Done · " + bits.join(" · ");
    } catch (e) {
      askHistory.push({
        role: "assistant",
        content: `Error: ${e.message}\n\nIf SuperGrok expired, open More → Connect SuperGrok and retry.`,
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

  async function refreshGoogleAuth() {
    const btn = $("btn-google-auth");
    if (btn) btn.disabled = true;
    showAlert("Starting Google Health sign-in…", "ok");
    try {
      // Remote FitDash (Pi / Tailscale): localhost:8788 callback is unreachable from
      // the phone/Mac browser. Use the same public login OAuth (Health scopes) that
      // already redirects to FITDASH_PUBLIC_URL/api/auth/google/callback.
      const host = (location.hostname || "").toLowerCase();
      const isLocalHost =
        host === "localhost" || host === "127.0.0.1" || host === "::1";
      if (!isLocalHost) {
        showAlert(
          "Opening Google sign-in… you’ll return to FitDash over Tailscale HTTPS.",
          "ok"
        );
        window.location.assign("/api/auth/google/start");
        return;
      }
      const res = await fetch("/api/google-health/auth/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: true }),
      });
      let data = {};
      try {
        data = await res.json();
      } catch (_) {
        data = {};
      }
      if (res.status === 404 || data.error === "not found") {
        throw new Error(
          "Auth API not found — restart the dashboard (Stop the old server, then run Start Resistance Dashboard) so it loads the new code."
        );
      }
      if (!res.ok || !data.ok) {
        throw new Error(data.error || data.message || `HTTP ${res.status}`);
      }
      // Server may force public login path (remote deploy)
      if (data.use_same_window || data.status === "use_login") {
        window.location.assign(data.auth_url || "/api/auth/google/start");
        return;
      }
      if (data.auth_url) {
        window.open(data.auth_url, "_blank", "noopener,noreferrer");
      }
      showAlert(
        data.message ||
          "Complete Google consent in the new tab. Waiting for authorization…",
        "ok"
      );
      // Poll until flow finishes (ok / error) or ~5 minutes.
      const deadline = Date.now() + 5 * 60 * 1000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 1500));
        const stRes = await fetch("/api/google-health/auth/status", {
          cache: "no-store",
        });
        const st = await stRes.json();
        const flow = (st && st.flow) || {};
        if (flow.status === "ok" || (st.token_ok && flow.status !== "pending")) {
          showAlert(
            flow.message || "Google Health authorized. Refreshing data…",
            "ok"
          );
          await loadDashboard(true);
          return;
        }
        if (flow.status === "error") {
          throw new Error(flow.message || flow.error || "Authorization failed");
        }
      }
      throw new Error(
        "Timed out waiting for Google consent. More → Reconnect Google Health to try again."
      );
    } catch (e) {
      showAlert(`Google auth failed: ${e.message}`, "err");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function init() {
    if ($("log-date")) $("log-date").value = todayISO();
    if ($("exercise-rows") && !$("exercise-rows").children.length) addExerciseRow();
    bindInventoryListOnce();
    initMobileShell();
    registerServiceWorker();
    if ($("btn-add-ex")) $("btn-add-ex").addEventListener("click", () => addExerciseRow());
    if ($("log-form")) $("log-form").addEventListener("submit", submitWorkout);
    if ($("btn-refresh")) $("btn-refresh").addEventListener("click", () => loadDashboard(true));
    if ($("btn-google-auth")) {
      $("btn-google-auth").addEventListener("click", () => refreshGoogleAuth());
    }
    if ($("btn-focus-log")) $("btn-focus-log").addEventListener("click", () => {
      goMobileTab("log");
      $("log-card").scrollIntoView({ behavior: "smooth", block: "start" });
      $("session_type").focus();
    });
    bindDailyQuestClicks();
    if ($("btn-scroll-workout-plan")) {
      $("btn-scroll-workout-plan").addEventListener("click", () => {
        // Training settings live under More; prescription is on Today Lift
        goMobileTab("more");
        const el =
          $("training-settings-section") ||
          $("workout-goals-form") ||
          $("exercise-catalog-section");
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    if ($("btn-scroll-meal-plan")) {
      $("btn-scroll-meal-plan").addEventListener("click", () => {
        // Meal plan + today-so-far live on Today (Kitchen is setup only)
        goMobileTab("today");
        const el =
          $("meal-plan-card") ||
          $("meal-plan-result") ||
          $("today-so-far-card");
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    if ($("ingredient-form")) {
      $("ingredient-form").addEventListener("submit", submitIngredient);
    }
    if ($("targets-form")) {
      $("targets-form").addEventListener("submit", submitTargets);
    }

    if ($("equipment-form")) {
      $("equipment-form").addEventListener("submit", submitEquipmentInventory);
    }
    const eqList = $("equipment-list");
    if (eqList && !eqList.dataset.eqBound) {
      eqList.dataset.eqBound = "1";
      eqList.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("button[data-eq-action]");
        if (!btn) return;
        const action = btn.getAttribute("data-eq-action");
        const id = (btn.getAttribute("data-id") || "").trim();
        const name = (btn.getAttribute("data-name") || "").trim();
        if (action === "edit") {
          if ($("eq-name")) $("eq-name").value = name;
          if ($("eq-tag") && btn.getAttribute("data-tag")) {
            $("eq-tag").value = btn.getAttribute("data-tag");
          }
          if ($("eq-max")) $("eq-max").value = btn.getAttribute("data-max") || "";
          $("eq-name") && $("eq-name").focus();
          return;
        }
        if (action !== "remove") return;
        btn.disabled = true;
        try {
          const res = await fetch("/api/equipment/remove", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id, name }),
          });
          const data = await res.json();
          if (!res.ok || !data.ok) throw new Error(data.error || res.status);
          showAlert(`Removed ${name || id}`, "ok");
          if (data.equipment && state && state.workout_store) {
            state.workout_store.equipment = data.equipment;
            renderEquipmentInventory(state.workout_store);
            renderExerciseCatalog(state.workout_store);
          }
          await loadDashboard(false);
        } catch (e) {
          showAlert(`Remove failed: ${e.message}`, "err");
        } finally {
          btn.disabled = false;
        }
      });
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
    startQuietPoll();
    // Auth gate first — do not fetch /api/dashboard or /api/ask/* until signed in
    checkAuthAndBoot();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
