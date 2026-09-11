# GetHelpFrom.ai questions (generated from questions.json)
SoT is `questions.json`. This file is a human table. Do not edit independently.
## Q0
| id | prompt | type | options | branching |
|---|---|---|---|---|
| Q0 | What should we focus on? | single | My personal life, My business, Both | personal / business / both sequential |

## Stage 1 buckets
| track | id | label |
|---|---|---|
| personal | `money_bills` | Money & bills |
| personal | `home_paper` | Home & paperwork |
| personal | `time_cal` | Time & calendar |
| personal | `messages` | Messages & inbox |
| personal | `health` | Health & habits |
| personal | `learning` | Learning |
| business | `leads` | Leads & sales |
| business | `ops` | Operations & fulfillment |
| business | `biz_money` | Money & admin |
| business | `marketing` | Marketing & content |
| business | `support` | Customer support |
| business | `team` | Team & hiring |

## Thin context
| id | tracks | prompt | type | options |
|---|---|---|---|---|
| role | personal | Which fits you today? | single | employed, gig, self_employed, student, retired, between_roles |
| biz_role | business | Your role? | single | owner, ops, sales, marketing, finance, other |
| team_size | business | Team size? | single | just_me, 2-10, 11-50, 51-200, 200+ |
| hours | personal, business | Hours/week this mess eats? | single | 0-2, 3-5, 6-10, 10+ |
| tools | personal, business | What do you already use? (any that apply) | multi | gmail, outlook, google_calendar, apple_calendar, spreadsheets, quickbooks, jobber, housecall_pro, shopify, hubspot, slack, phone, none |
| trust | personal, business | AI that drafts is fine. AI that sends? | likert | 1, 2, 3, 4, 5 |
| urgency | personal, business | How hot is this? | single | curious, this_quarter, weekly_pain, losing_sleep_or_money |
| budget | business | If we actually built the top pick, budget band? | single | <200/mo, 200-750, 750-2k, 2k+, project |
| worth | personal | If this just handled itself, worth to you monthly? | single | <25, 25-75, 75-200, 200+, not_sure |

## Stage 2 sub-areas (4 per bucket)
| bucket | sub id | label |
|---|---|---|
| `money_bills` | `medical_bills_eobs` | Medical bills & EOBs |
| `money_bills` | `subscriptions_auto_pays` | Subscriptions piling up |
| `money_bills` | `who_paid_what` | Who paid what |
| `money_bills` | `late_fees_tax_packets` | Late fees & tax packets |
| `home_paper` | `school_forms` | School / kid forms |
| `home_paper` | `parent_care_papers` | Parent / elder paperwork |
| `home_paper` | `mail_the_junk_drawer` | Mail in the junk drawer |
| `home_paper` | `family_calendar_mix_ups` | Family calendar mix-ups |
| `time_cal` | `double_booked_days` | Double-booked days |
| `time_cal` | `meetings_that_should_be_email` | Meetings that should be email |
| `time_cal` | `focus_time_gets_eaten` | Focus time gets eaten |
| `time_cal` | `family_calendar_vs_work` | Family calendar vs work |
| `messages` | `inbox_never_at_zero` | Inbox never at zero |
| `messages` | `i_ll_reply_later` | I'll reply later |
| `messages` | `follow_ups_that_slip` | Follow-ups that slip |
| `messages` | `work_and_family_in_one_pocket` | Work and family in one pocket |
| `health` | `habit_ghost` | Apps I ghost after 4 days |
| `health` | `meals` | What's for dinner |
| `health` | `sleep` | Sleep keeps sliding |
| `health` | `meds_appts` | Meds / appointments |
| `learning` | `course_hoard` | Bought courses, never finished |
| `learning` | `cert_lapse` | Certs / licenses slipping |
| `learning` | `notes_lost` | Notes I can't find later |
| `learning` | `skill_practice` | No time to actually practice |
| `leads` | `slow_first_reply` | Leads wait hours |
| `leads` | `leads_slip` | Warm leads go cold |
| `leads` | `quoting_slow` | Quoting takes forever |
| `leads` | `dms_on_three_apps` | DMs on three apps |
| `ops` | `dispatch_chaos` | Who goes where tomorrow |
| `ops` | `inventory_mismatch` | Stock doesn't match sales |
| `ops` | `returns_exceptions` | Returns & exceptions |
| `ops` | `nightly_copy_paste` | Copy-paste between tools |
| `biz_money` | `invoice_chase` | Invoices not chased |
| `biz_money` | `month_end_close` | Books never close |
| `biz_money` | `payroll_1099s` | Payroll / 1099s |
| `biz_money` | `tax_packets_receipts` | Tax packets & receipts |
| `marketing` | `post_drought` | We never post |
| `marketing` | `review_ask` | Don't ask for reviews |
| `marketing` | `photos_die` | Job photos die on the phone |
| `marketing` | `same_caption` | Same caption every time |
| `support` | `missed_calls_after_hours` | Missed calls / after hours |
| `support` | `status_chase_texts` | Status-chase texts |
| `support` | `reviews_nobody_asks_for` | Reviews nobody asks for |
| `support` | `complaints_i_see_too_late` | Complaints I see too late |
| `team` | `unread_apps` | Applications unread |
| `team` | `paper_onboard` | Day-one is a folder |
| `team` | `crew_texts` | Crew comms are 14 group texts |
| `team` | `no_show` | No-shows / call-outs |

Stage 3 cards: `scenarios.json` (192). Scoring feed: scenario → pain → solution.
