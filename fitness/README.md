# Fitness Repository

> **Visual dashboard available:** Open [../dashboard/index.html](../dashboard/index.html) (serve from repo root with `python -m http.server`). All charts + latest sessions + KPIs in one beautiful view.

Comprehensive fitness tracking system for TruColors.

## 📁 Directories

### [workouts/](workouts/)
- **push.md** - Push day exercises and PRs
- **pull.md** - Pull day exercises and PRs  
- **legs.md** - Leg day exercises and PRs
- Charts and progress visualization

### [charts/](charts/)
- Workout progress charts (push/pull/legs)
- Body composition trends (weight, body fat)
- Sleep tracking
- Calorie trends
- Activity visualizations

### [data/](data/)
- Fitbit data exports and reports
- Daily tracking CSVs
- Body fat measurements

### [nutrition/](nutrition/)
- Meal planning
- High-protein value analysis
- Water intake tracking
- **Applied** calorie/macro targets: `nutrition/targets.json` (FitDash SoT)
- **Coach-owned recommendations:** [nutrition/COACH_TARGETS.md](nutrition/COACH_TARGETS.md) — subroutine recommends from goals vs data; apply is explicit. Do not treat the April 1,700 kcal table in `nutrition/README.md` as live.

### [Fitbit/](Fitbit/)
- Fitbit sync scripts

---

*Last updated: 2026-08-27 (coach-owned targets contract; live applied numbers still `nutrition/targets.json`)*