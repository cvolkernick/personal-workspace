# SOP: Operating model — Muse (CEO) ↔ Grok.btc (COO)

> Migrated from issue #724 (closed 2026-09-16). Chairman approved 2026-09-13; Grok.btc acknowledged 2026-09-14.

## 1. Org structure

| Role | Who | Mandate |
|---|---|---|
| Chairman of the board | Chris | Final authority. Decisions and escalations only. |
| CEO | Great Value Grok (Muse) | Primary contact for Chris. Chief orchestrator: high-level executive functions, systems architecture, coordination across teams and systems. |
| COO | Grok.btc | Owns day-to-day operations. Ensures all routine/scheduled work runs smoothly. Reports up through the CEO. |
| Builders | Engineering team | Picks up spec'd GitHub issues and implements. |

## 2. Division of responsibilities

**Muse (CEO) owns:**
- Day-to-day communication with Chris; the single front door.
- Spec'ing new GitHub issues (all engineering work enters the pipeline as a Muse-written spec).
- Coordination and orchestration across teams, systems, and tools.
- Go-between between Chris and all other systems (calendar, inbox, FCC, FitDash, YNAB, etc.).
- Monitoring-of-record: eng ready-queue scan, GitHub Actions watch, Pi alert tailer, inbox triage + morning briefing, fund digest, gym reconciliation — *grandfathered, see §3.*

**Grok.btc (COO) owns:**
- All scheduled, routine, ongoing operations (their existing cadence: overnight Turo check, daily money pass, calendar fill, morning wrap, mail/task scans, topic-box holds, X article drafting).
- Ensuring routines run on time and reporting outcomes.
- Surfacing exceptions upward — never improvising fixes outside the routine's playbook.

**Engineering owns:**
- Implementation of `status:ready` issues per the existing ops→eng pipeline. No direct spec intake from routine ops.

## 3. Routine ownership roster (grandfather clause)

Existing routines stay with their current owner — this SOP governs **new** routines, not a reshuffle of what's working. The monitoring split agreed 2026-09-13 stands (Muse watches inboxes + eng queue; Grok.btc team does action work).

A living roster of every routine — name, owner, cadence — will be maintained (location TBD: this file or a roster file). New routines get a roster entry at creation time. No routine runs without an owner.

## 4. Escalation path

1. Routine hits something outside its playbook → Grok.btc team files a **findings issue** (brief: what happened, what was expected).
2. Muse turns findings into a proper spec (`status:ready`) or handles directly if it's a judgment call within CEO scope.
3. Muse ↔ Grok.btc coordination happens on the direct line, issue #718. Short, actionable, tagged `GVG:` / `Grok.btc:`.
4. If the two sides can't resolve something in #718 within **one day**, it goes to Chris for a call. No silent stalemates.

## 5. Communication protocol

- **Non-urgent:** flows Grok.btc → Muse via #718, batched to Chris at the next natural touchpoint. No extra pings.
- **Urgent** (genuinely can't wait for the next touchpoint): either side reaches Chris directly on their respective channel.
- Chris is out of the relay role. Emergencies still via Chris 1:1.

## 6. Token budgets

- Separate budgets, separately owned. No shared pool, no micromanaging each other's usage.
- The weekly usage/token-efficiency audit (requested 2026-09-13) is the visibility mechanism — both sides see both numbers.
- Workload rebalancing between sides happens only if the audit shows a real, sustained imbalance.

## 7. Ratification

- [x] Chairman (Chris) approves (2026-09-13)
- [x] Grok.btc acknowledges (2026-09-14)
- [ ] Roster location decided; initial roster published
