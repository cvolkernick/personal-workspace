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

  function addSetRow(setsWrap, prefill = {}) {
    const row = document.createElement("div");
    row.className = "set-row";
    row.innerHTML = `
      <label>Weight (lbs)
        <input type="number" class="set-weight" required min="0" step="0.5" value="${prefill.weight_lbs ?? ""}" />
      </label>
      <label>Sets
        <input type="number" class="set-sets" required min="1" step="1" value="${prefill.sets ?? 1}" />
      </label>
      <label>Reps
        <input type="number" class="set-reps" required min="1" step="1" value="${prefill.reps ?? 10}" />
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
    // Monthly volume = last 30 calendar days, one bar per day (0 if no session).
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
              maxTicksLimit: 10,
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
    // Align calories chart x-axis with weight chart: full ~90-day civil span.
    const calSpanDays = 90;
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
    // Prefer full series (not downsampled) so 90d axis is dense where data exists.
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
      // Chronological; % of calories from P/C/F (4/4/9 kcal per gram).
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
      const splits = macroDays.map((n) => {
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
        const last = splits.length ? splits[splits.length - 1] : null;
        const lastRoll = (() => {
          for (let i = pRoll.length - 1; i >= 0; i--) {
            if (pRoll[i] != null && cRoll[i] != null && fRoll[i] != null) {
              return { p: pRoll[i], c: cRoll[i], f: fRoll[i] };
            }
          }
          return null;
        })();
        if (!last || last.p == null) {
          $("macros-note").textContent =
            "Calorie share from protein / carbs / fat (4 / 4 / 9 kcal per gram). Lines = 7-day rolling avg %.";
        } else {
          const rollTxt = lastRoll
            ? ` · 7d avg P ${lastRoll.p}% · C ${lastRoll.c}% · F ${lastRoll.f}%`
            : "";
          $("macros-note").textContent =
            `Latest day: P ${last.p}% · C ${last.c}% · F ${last.f}% ` +
            `(${Math.round(last.grams.p)} / ${Math.round(last.grams.c)} / ${Math.round(last.grams.f)} g)` +
            rollTxt +
            ` · bars = daily split, lines = ${rollWin}d rolling avg`;
        }
      }
    }

    destroyChart(hydrationChart);
    if ($("chart-hydration")) {
      const hydVals = hydration.map((h) => h.water_ml);
      const hydRoll7 = rollingAverage(hydVals, 7);
      const hydTrend = linearTrend(hydVals);
      const hSlope = trendSlopePerDay(hydVals);
      const lastHydRoll =
        [...hydRoll7].reverse().find((v) => v != null && !Number.isNaN(v)) ?? null;
      hydrationChart = new Chart($("chart-hydration"), {
        data: {
          labels: hydration.map((h) => h.date),
          datasets: [
            {
              type: "bar",
              label: "Water (ml)",
              data: hydVals,
              backgroundColor: "rgba(61,156,240,0.45)",
              borderRadius: 6,
              order: 3,
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
          ],
        },
        options: chartDefaults(),
      });
      if ($("hydration-trend-note")) {
        if (lastHydRoll == null) {
          $("hydration-trend-note").textContent =
            "Need hydration logs for rolling average.";
        } else {
          const slopeTxt =
            hSlope == null
              ? ""
              : ` · trend ${hSlope >= 0 ? "+" : ""}${Math.round(hSlope * 7)} ml/week`;
          $("hydration-trend-note").textContent =
            `Latest 7d avg: ${Math.round(lastHydRoll).toLocaleString()} ml${slopeTxt} · ${hydration.length} days`;
        }
      }
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

  function applyInventoryUpdate(inventory) {
    if (!state) state = {};
    if (!state.nutrition_store) state.nutrition_store = {};
    state.nutrition_store.inventory = inventory;
    renderInventory(state.nutrition_store);
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
      const iid = String(ing.id || "").replace(/"/g, "&quot;");
      const iname = String(ing.name || "").replace(/"/g, "&quot;");
      li.innerHTML = `
        <div class="title">${ing.name} ${stock ? "" : "<span class='muted'>(out)</span>"}</div>
        <div class="meta">${ing.category || "other"} · ${ing.serving_label || "1 serving"} ·
          ${fmtNum(ing.calories)} kcal · P${fmtNum(ing.protein_g)} C${fmtNum(ing.carbs_g)} F${fmtNum(ing.fat_g)}</div>
        <div class="actions" style="margin-top:0.35rem">
          <button type="button" class="btn-stock" data-action="stock" data-id="${iid}" data-name="${iname}" data-stock="${stock ? "0" : "1"}">
            ${stock ? "Mark out of stock" : "Mark in stock"}
          </button>
          <button type="button" class="btn-remove" data-action="remove" data-id="${iid}" data-name="${iname}">Remove</button>
        </div>
      `;
      list.appendChild(li);
    });
  }

  /** One delegated listener — survives re-renders and avoids dead buttons. */
  function bindInventoryListOnce() {
    const list = $("inventory-list");
    if (!list || list.dataset.bound === "1") return;
    list.dataset.bound = "1";
    list.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("button[data-action]");
      if (!btn || !list.contains(btn)) return;
      ev.preventDefault();
      ev.stopPropagation();
      const action = btn.getAttribute("data-action");
      const id = (btn.getAttribute("data-id") || "").trim();
      const name = (btn.getAttribute("data-name") || "").trim();
      if (!id && !name) {
        showAlert("Remove failed: missing ingredient id", "err");
        return;
      }
      btn.disabled = true;
      try {
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
          // Refresh meal plan only (no full remote pull)
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
        showAlert(`${action === "remove" ? "Remove" : "Stock update"} failed: ${e.message}`, "err");
        btn.disabled = false;
      }
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
    const latestN = nutrition.length
      ? [...nutrition].sort((a, b) => String(a.date).localeCompare(String(b.date))).slice(-1)[0]
      : null;
    if ($("stat-calories")) {
      // Calorie share: P×4 + C×4 + F×9 (same basis as macro split chart).
      let pPct = null;
      let cPct = null;
      let fPct = null;
      if (latestN) {
        const p = Number(latestN.protein_g) || 0;
        const c = Number(latestN.carbs_g) || 0;
        const f = Number(latestN.fat_g) || 0;
        const totK = p * 4 + c * 4 + f * 9;
        if (totK > 0) {
          pPct = Math.round((p * 4 * 1000) / totK) / 10;
          cPct = Math.round((c * 4 * 1000) / totK) / 10;
          fPct = Math.round((f * 9 * 1000) / totK) / 10;
        }
      }
      $("stat-calories").textContent =
        latestN && latestN.calories != null ? fmtNum(latestN.calories) : "—";
      $("stat-protein").textContent =
        latestN && latestN.protein_g != null
          ? `${fmtNum(latestN.protein_g)} g${pPct != null ? ` · ${pPct}%` : ""}`
          : "—";
      $("stat-carbs").textContent =
        latestN && latestN.carbs_g != null
          ? `${fmtNum(latestN.carbs_g)} g${cPct != null ? ` · ${cPct}%` : ""}`
          : "—";
      $("stat-fat").textContent =
        latestN && latestN.fat_g != null
          ? `${fmtNum(latestN.fat_g)} g${fPct != null ? ` · ${fPct}%` : ""}`
          : "—";
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
        $("nutrition-note").textContent =
          `Nutrition days: ${n} · burned-calorie days: ${b} · green band = surplus (intake > burned), red = deficit`;
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
    // Guard: if used as a raw click handler, first arg is an Event (truthy).
    if (forceRefresh && typeof forceRefresh !== "boolean") {
      forceRefresh = false;
    }
    $("btn-refresh").disabled = true;
    // Don't wipe success toasts from inventory remove/add mid-action.
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
      const url = forceRefresh === true ? "/api/dashboard?refresh=1" : "/api/dashboard";
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
      const prs = data.auto_prs || [];
      const prBit = prs.length ? ` · PRs: ${prs.join(", ")}` : "";
      status.textContent = `Saved to ${data.write.path} · verified=${data.write.verified_on_readback}${prBit}`;
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
    bindInventoryListOnce();
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
