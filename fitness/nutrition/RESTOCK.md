# FitDash restock → cart

Shopping is not a reminder. FitDash restock / out-of-stock needs land in the
**right retailer cart or list**. Google Tasks are not the grocery SoT.
Google Keep is fallback only when a cart write is blocked (session/auth).

## Default path

1. FitDash restock SoT (`today.purchases` / `GET /api/restock`) emits a
   **venue-tagged** list: `walmart` | `costco` | `other`.
2. Agent or job `POST /api/restock/cart` (or `push_items`) auto-adds:
   - **Walmart** → signed-in Walmart cart (`WALMART_SESSION_COOKIE`). No checkout.
   - **Costco** → Costco shopping list/cart (`COSTCO_SESSION_COOKIE`). No checkout.
   - **other** → park honestly (not a supported retailer writer).
3. Prefer auto-add. Do not create a Google Task. Do not open Keep first.
4. After Chris confirms the order, `POST /api/restock/confirm` marks pantry
   in-stock/qty when writable. **No GT complete step.**

## Fallback

If Walmart/Costco write fails with auth/session:

- Items go on a thin Keep holding checklist titled **FitDash restock hold**,
  lines like `[walmart] Restock: Broccoli`.
- The same lines are stored in `~/.config/resistance-dashboard/restock_hold.json`.
- `POST /api/restock/retry` replays the hold into carts.

Keep credentials: `GOOGLE_KEEP_HOLD_WEBHOOK` or `GOOGLE_KEEP_MASTER_TOKEN` +
`GOOGLE_KEEP_EMAIL`. Missing Keep creds are reported honestly; the hold file
is still the retry queue.

## Venue SOP (named exceptions only)

Do not invent brand picks. Search query is the ingredient name. Cheapest-unit
wins among search hits.

| Venue | When |
|-------|------|
| Costco | Named exceptions only: chicken breast, Greek yogurt, brown rice, whey, oats, whole eggs (bulk cheapest-unit). |
| Walmart | Remaining grocery staples (default). |
| other | Prepared / restaurant / generic “staples” placeholders — park, do not guess a store. |

Explicit `venue` on an inventory/suggestion row wins.

## Google Tasks

FitDash owns daily quests (lifts, meals, hydration, sleep, Duchess, weigh-in,
restock). **Do not create or recreate grocery / restock GTs.** Day rollover
and quest sync purge leftover FitDash grocery GTs and must not bring them
back.

Set `FITDASH_QUEST_GT_SYNC=0` to also stop creating lifts/meals/sleep/cardio
GTs. GTs stay for outside one-offs and Grok Bot voice capture.

## Non-goals

- No auto-checkout / pay without Chris.
- No grocery-as-Google-Tasks rebuild.
- No brand SKU invention beyond cheapest-unit / named-exception above.
