"""Generator for Horizon's Team & Systems Directory (/directory).

Run: python3 build.py  -> writes index.html next to this file.
Then: bash make_pdf.sh -> team-directory.pdf (gitignored, built on prism).
"""
import math, html, os
HERE = os.path.dirname(os.path.abspath(__file__))
E = html.escape
U = '<span class="unc">unconfirmed</span>'

AREAS = {
 "core":   ("Chief of staff", "#1f2a44", "#e9ecf5"),
 "ops":    ("Ops / Fleet", "#2f6f6a", "#e3f1ef"),
 "money":  ("Money", "#9a6b16", "#f8efdc"),
 "eng":    ("Engineering", "#3b5b9a", "#e6ecf8"),
 "qa":     ("QA / Process", "#6a4c93", "#efe9f6"),
 "health": ("Health", "#b2533e", "#f9e8e3"),
 "gtm":    ("Growth / GTM", "#4f7a32", "#ebf3e3"),
 "content":("Content", "#8a3d6b", "#f5e6ef"),
 "ext":    ("Outside Grok Bot", "#555b66", "#eceef1"),
}

# ---------- MEMBERS ----------
M = []
def m(area, name, role, owns, routines, talks, reach, note=None):
    M.append(dict(area=area, name=name, role=role, owns=owns, routines=routines, talks=talks, reach=reach, note=note))

m("core","Grok.btc⚡","Your chief of staff and orchestrator. Everyone on the team reports to Grok; Grok reports to you.",
 ["The full map of your goals, and one combined daily brief to you",
  "Routing work to the right teammate, and staffing (adding, reshaping or parking seats)",
  "The engineering gate: reviews each pull request against the issue's checklist, then merges or sends it back",
  "Spending the Grok Bot weekly allowance carefully (thin turns, coarse schedules)",
  "The direct line with GVG on GitHub issue #718"],
 ["Daily 1:38 AM · Turo overnight booking check (calendar stays current)",
  "Daily 8:12 AM · Keeps Pulse's 7-day calendar filled",
  "Daily 7:44 AM and 7:44 PM · Publishes the Daily Brief editions on Horizon",
  "Daily 8:03 AM and 8:03 PM · Sends Chris the Daily Brief link plus a voice-note rundown",
  "Daily 9:05 AM and 5:05 PM · Reads Task Extractor reports and files the action items",
  "Daily 9:38 AM · Inbox action pass (Turo to Helm, Boardy notes, Tasks stars)",
  "Daily 9:48 AM · YNAB pass: hands new transactions to Nakatoshi",
  "Daily 9:57 AM · Reads new comments on #718 (GVG line)",
  "Weekdays 8:06 AM · Places “Grok ·” topic focus blocks on your calendar",
  "Weekdays 4:26 PM · Autonomous SDLC pass (picks up to 3 small slices of work)",
  "Mondays 8:18 · usage audit · 8:42 · COS Monday report · 8:48 · market research · 9:02 · YouTube groom health · 9:04 · topic-box briefs · 9:13 · Launch scan · 9:33 · Holistic Fitness panel · 9:35 · Compose Kickstarter watch",
  "Mon/Wed/Fri 9:27 AM · Panamerica Sunbiz watch (ends once you're listed, or Nov 8) · 10:48 AM · LinkedIn discovery reply watch",
  "Thu 10 AM · Cape Coral mowing block · Sun 10 AM · Lehigh mowing block (both via Pulse, soft)",
  "Monthly: 1st 8:07 AM · Rivian Turo reconcile (asks you for last month's report) · 10th 1 PM · Thaís USDC transfer · Oct/Nov/Dec 1st 9:26 AM · FLOP airdrop watch",
  "When a Discord message arrives · forwards it to Nakatoshi",
  "Daily 12:24 PM · Daily sprint check "+U],
 "You (1:1), every teammate, GVG (on #718), Buzz/GrokBuild (through GitHub issues)",
 "Grok Bot chat (your main 1:1) · X Chat @grok_btc · signs team-inbox mail as “Ops Desk” · GitHub as GrokBot-BTC")

m("ops","Helm","Turo fleet manager for Panamerica Auto.",
 ["Turo host operations: guest messages, bookings, tolls and invoices",
  "The Turo calendar on the Panamerica account: two events per trip (pickup ready, drop-off and turnover)",
  "The 2023 Rivian R1S booking sheet (one row per reservation)",
  "Auto Fleet page tidiness, organized car by car",
  "Our cars only: Rivian R1S, 2022 and 2024 Corollas, Tesla Model 3. Never SafeWheels cars"],
 ["Daily 8:00 AM · Outcomes brief to Grok (quiet if nothing's happening)",
  "Real time · Turo guest messages arrive from the prism Turo relay (904 phone notifications plus Gmail push)",
  "Right away · Any invoice that's ready to send goes to Grok, so you get one ping"],
 "Grok, Pulse (trip ends anchor your day), Alexandra (urgent Turo items)",
 "Grok Bot seat · watches the Panamerica Gmail · writes the Turo calendar")

m("ops","Alexandra","Panamerica's voice: Bland AI phone calls and texts from the 904 business line.",
 ["Outbound calls through Bland AI (an AI phone-call service), always after confirming number, purpose and talking points",
  "The only teammate allowed to send SMS from the 904 line",
  "“Roadside” first-contact texts to people and dealers selling cars, offering rental management or payment takeover",
  "Reports every call and reply back to Grok"],
 ["When woken · inbound texts and call results come in through the bland relay on prism",
  "Roadside send passes during the day (send reports post to #718) · exact schedule "+U],
 "Grok (1:1 only, never group rooms), Helm",
 "Grok Bot seat · sends through the bland relay on prism · sits in the GTM rooms")

m("ops","Pulse","Time manager. Owns your calendar and the shape of your week.",
 ["Your Personal calendar and the Time Allocator (the week planner)",
  "A rolling 7 days of blocks: Drive, Sleep (8–9 h), Duchess walks (out of the midday heat), Gym, Laundry every 5 days, soft mowing blocks",
  "Fitting in Frankenfit sessions, Nourish meal times and Restore wind-downs, never invented ones",
  "Building the day around Turo drop-offs and Lyft hinge days"],
 ["Daily 8:00 AM · Outcomes brief to Grok",
  "When Grok's 8:12 AM check finds gaps · writes the missing days",
  "One-off reminders handed over from GVG (Bland trial cancel 10/14, Devin revisit 10/16, HDHP enrollment 11/1) "+U],
 "Grok, Frankenfit, Nourish, Restore, Helm",
 "Grok Bot seat · writes to your Personal Google Calendar")

m("money","Nakatoshi","Fund manager and treasurer. Sound-money, Bitcoin-first.",
 ["FCC (Financial Command Center) and the books, the live money ledger",
  "YNAB: categorizes and approves your transactions within your category map",
  "The Agentic Fund watchlist and target weights (changes only when you ask)",
  "Interest Spectrum coaching: where the next free dollar should go",
  "Never trades or moves money unless you order it"],
 ["Daily 9:48 AM · YNAB pass (Grok kicks it)",
  "When a Discord message arrives · gets a short packet from Grok",
  "Coinbase send reconciliation (moved over from GVG, reported live 10/9) · schedule "+U],
 "Grok, Meridian (macro context), Scrip (where you actually spend)",
 "Grok Bot seat · FCC on prism · Horizon room")

m("money","Meridian","Keeper of the Horizon macro model.",
 ["The Horizon Global Macro Model and dashboard: the big-picture state of rates, growth, inflation, energy, tech and risk",
  "Spotting regime shifts early",
  "Capital implications only. Hands those to Nakatoshi and never trades"],
 ["Daily 8:00 AM · Horizon brief to Grok (quiet if nothing moved)"],
 "Grok, Nakatoshi",
 "Grok Bot seat · Horizon on prism · Horizon room")

m("money","Scrip","Household deal finder.",
 ["Honest ways to pay less: discounted gift cards, coupons, real cash-back",
  "Research only. Anything that costs money waits for you"],
 ["No standing schedule (its daily save brief is off)",
  "Joins in whenever you ask Grok to find something to buy",
  "Price watch and monthly subscription scan handed over from GVG "+U],
 "Grok, Nakatoshi",
 "Grok Bot seat · reached through Grok")

m("eng","Forge","Principal engineer for your personal-workspace monorepo (the single code repository behind your apps).",
 ["Shipping code in small, reversible slices: branch, test, pull request, then Grok's gate",
  "Deploying to prism when ordered (Mac is for development, prism runs the real thing)",
  "Heavy engineering through Grok Build"],
 ["Daily 8:00 AM · Outcomes brief to Grok",
  "GitHub Actions failures and Pi alert (#701) triage handed over from GVG "+U],
 "Grok, Cadence, Assay",
 "Grok Bot seat · GitHub cvolkernick/personal-workspace")

m("eng","Cadence","Scrum master. Keeps the engineering backlog tidy and flowing.",
 ["Grooming GitHub Issues (the formal backlog) and stamping them status:ready when they're buildable",
  "Keeping work in progress at 3 or fewer",
  "The daily scrum digest"],
 ["Daily 8:00 AM · Scrum brief to Grok",
  "Issue close-out watch handed over from GVG "+U],
 "Grok, Forge, Assay",
 "Grok Bot seat · Devops Team room · GitHub Issues")

m("qa","Assay","Quality checker across all systems.",
 ["Finding weak proof: missing tests, Mac vs prism mismatches, thin checklists, silent failures",
  "Filing suggestions with a pass/fail test attached. Suggests, never implements",
  "Never softens a red finding"],
 ["Daily 8:00 AM · QA brief to Grok"],
 "Grok, Forge, Cadence",
 "Grok Bot seat · Devops Team room")

m("qa","Jevons","The “Jev” decision classifier: a quick second opinion before the team acts.",
 ["Fast choice and score judgments for routing and urgency",
  "Runs in shadow mode: gives structured answers, and Grok still decides"],
 ["On call, no schedule (Grok used it for the GVG handoff calls)"],
 "Grok",
 "Grok Bot seat · reached through Grok")

m("health","Frankenfit","Strength coach and keeper of FitDash's training side.",
 ["Your workout programming: progressive overload in a cutting and recomposition phase, home plus commercial gym",
  "FitDash training logic",
  "Spotting Facebook Marketplace home-gym gear that's actually worth it (told to Grok, never to you directly)"],
 ["Daily 8:00 AM · Outcomes brief to Grok",
  "Mondays · Holistic Fitness panel post (Grok kicks it at 9:33 AM)",
  "Gym scheduling: took over from GVG's daily gym cron, which was switched off 10/11"],
 "Grok, Nourish, Restore, Pulse",
 "Grok Bot seat · Holistic Fitness room · FitDash")

m("health","Nourish","Nutrition specialist.",
 ["Targets, macros, food logs and meal plans (currently 2,100 kcal and 210 g protein)",
  "FitDash nutrition and meal-time suggestions",
  "Not a doctor, and gives no diagnoses"],
 ["Daily 8:00 AM · Nutrition brief to Grok",
  "Mondays · Holistic Fitness panel post"],
 "Grok, Frankenfit, Restore, Pulse",
 "Grok Bot seat · Holistic Fitness and Meal plan rooms")

m("health","Restore","Sleep and recovery specialist.",
 ["Sleep, hydration, readiness, deloads, and knowing when to back off",
  "Reads Hidrate and Google Health data",
  "Raises red flags through Grok and never diagnoses"],
 ["Daily 8:00 AM · Recovery brief to Grok",
  "Mondays · Holistic Fitness panel post"],
 "Grok, Frankenfit, Nourish, Pulse",
 "Grok Bot seat · Holistic Fitness room")

m("gtm","Launch","Productization lead. Turns internal tools into honest public products.",
 ["Finding real public value, product-market fit and a path to market. FitDash comes first",
  "Readiness, positioning, packaging and pricing design",
  "Follow-ups on the LinkedIn discovery conversations"],
 ["Daily 8:00 AM · Outcomes brief to Grok",
  "Mondays 9:13 AM · Productization scan (Grok kicks it)"],
 "Grok, Herald, Lens, Quarry",
 "Grok Bot seat · GTM, Panamerica GTM and Market Research rooms")

m("gtm","Herald","Public voice for products Launch has cleared.",
 ["Narrative, channels, landing-page copy and campaigns, only for real offers",
  "No posts until Launch unlocks a slice"],
 ["Daily 8:05 AM · Outcomes brief to Grok"],
 "Grok, Launch, Byline",
 "Grok Bot seat · GTM and Market Research rooms")

m("gtm","Quarry","Market scout.",
 ["Finding underserved markets and supply/demand gaps across the US and digital",
  "Research only. Hands finds to Lens"],
 ["No schedule of its own (by design since 10/1)",
  "Mondays · feeds Grok's 8:48 AM market research check"],
 "Grok, Lens",
 "Grok Bot seat · Market Research room")

m("gtm","Lens","Market analyst. Ranks and stress-tests Quarry's finds.",
 ["Short ranked packets: demand signals, competition, buildability, money path, risk",
  "No numbers without sources"],
 ["Daily 8:00 AM · Outcomes brief to Grok",
  "Mondays · market research check"],
 "Grok, Quarry, Launch",
 "Grok Bot seat · Market Research room")

m("content","Byline","Your X long-form writer.",
 ["The topic backlog: energy, Bitcoin and sound money, AI leverage, autonomy, robotics, capital discipline",
  "Copy-ready drafts in your voice, plus a header image",
  "Sends draft review packets straight to you (one of only two exceptions to “everything goes through Grok”)",
  "Never posts to X itself"],
 ["Daily 8:00 AM · Article-lane brief to Grok",
  "Daily draft kick is paused until a topic is queued"],
 "Grok, you (review packets only), Herald",
 "Grok Bot seat · GTM rooms · your 1:1 for drafts")

m("ext","GVG (Great Value Grok)","A separate agent outside Grok Bot. Its role is moving to social media marketing and outreach.",
 ["Its share of inbox briefings, being retired into the Daily Brief",
  "Marketplace and Craigslist vehicle-lead discovery for Panamerica",
  "Panamerica Facebook post drafts",
  "The Ausome Movemint lawn-care schedule watch",
  "Role change to social, marketing and outreach director, from Grok's 10/9 handoff plan "+U],
 ["Daily ~8:00 AM · inbox-morning-briefing (retiring into the Daily Brief)",
  "Daily 8:00 PM · evening-wind-down-debrief (retiring into the Daily Brief)",
  "Daily 7:49 AM · marketplace-lead-discovery-sweep",
  "Every 2 h · watches #718 for Grok.btc's messages (as of 10/3) "+U,
  "Every 4 h · Ausome Movemint schedule watch, Messenger lead-reply watch (as of 10/3) "+U,
  "Mon/Wed/Fri 8:49 AM · Panamerica Facebook draft (as of 10/3) "+U],
 "Grok (comments on #718, tagged “GVG:” and “Grok.btc:”), you",
 "Runs in its own environment, outside Grok Bot · GitHub issue #718")

m("ext","Buzz / GrokBuild","The engineering worker that builds what's been specced.",
 ["Picks up GitHub issues labeled status:ready that name it as the worker",
  "Implements and opens pull requests. Grok reviews and merges"],
 ["Sweeps the status:ready queue · cadence "+U],
 "Grok, Cadence (through GitHub labels)",
 "Buzz on your Mac and the Grok Build harness · GitHub cvolkernick/personal-workspace")

ROOMS = [
 ("Devops Team", "Grok, Cadence, Forge, Assay"),
 ("Holistic Fitness", "Frankenfit, Nourish, Restore (team-only; Grok isn't a member)"),
 ("Meal plan nutrition review", "Grok, Nourish, Frankenfit"),
 ("Horizon", "Grok, Meridian, Nakatoshi"),
 ("GTM", "Grok, Launch, Herald, Byline, Alexandra"),
 ("Panamerica GTM + agreements", "Grok, Launch, Herald, Byline, Alexandra"),
 ("Market Research", "Grok, Quarry, Lens, Launch, Herald"),
]

# ---------- SYSTEMS ----------
SYS = [
 ("Infrastructure", [
  ("prism", "Your home Raspberry Pi, and the “real” server everything runs on. Reachable only over Tailscale. Hosts FCC, Horizon, Auto Fleet, the Time Allocator, the bland relay, the Turo relay, the YouTube groom timer and the heartbeat."),
  ("finley", "Backup host. Runs the hourly puller (copies snapshots off prism at :20) and holds the B2 knowledge graph."),
  ("Tailscale", "A private network that links your devices, so prism stays off the public internet."),
  ("Your Mac", "Development and review terminal. Not a host or a data store. Also runs Buzz."),
  ("The bucket (Cloudflare R2)", "Planned off-site, encrypted copy of the backups. Live status "+U+"."),
 ]),
 ("Dashboards and apps", [
  ("Horizon", "The Daily Brief paper (morning and evening editions) plus Meridian's macro model, at its own address on prism."),
  ("FCC", "Financial Command Center: the live money ledger, Capital Flows and the Interest Spectrum. Nakatoshi's home. Tailscale only."),
  ("FitDash", "Training, nutrition and recovery app, hosted on Vercel. The first product meant to go public."),
  ("Auto Fleet", "One card per car: bookings, money and maintenance together. Linked from FCC, kept separate from it."),
  ("Time Allocator", "Pulse's week planner. Your Personal calendar is the live view of the day."),
  ("Orchestra", "Parked as a product (8/27). Grok is the orchestrator instead."),
 ]),
 ("Relays (always on, on prism)", [
  ("bland relay", "Passes Bland AI call results and inbound 904 texts to Alexandra, and sends her outgoing texts with built-in safety rules (opt-out handling, rate limits, first-contact limits)."),
  ("Turo relay", "Turns Turo app notifications from the 904 phone, plus a Gmail push of Turo emails, into events for Helm within about a minute."),
 ]),
 ("Where work and notes live", [
  ("GitHub", "cvolkernick/personal-workspace. Issues are the engineering backlog. #718 is the GVG↔Grok line, #701 collects Pi alerts. Machine account: GrokBot-BTC."),
  ("Notion", "Todo List, the Mac tasks page and the Errands database. The Daily Brief reads the Todo List and Mac tasks."),
  ("Google", "Two Gmail accounts (personal and Panamerica), Calendars (Personal, Turo on Panamerica, Family), Drive (Task Extractor reports, the Rivian sheet), Google Tasks (the “Grok Bot” voice-jot list)."),
  ("Team inbox", "The shared Grok Bot team mailbox. Each bot signs with its own sender name; Grok signs as “Ops Desk”."),
 ]),
 ("Money and outside services", [
  ("YNAB", "Budgeting app. Nakatoshi categorizes and approves daily."),
  ("Plaid", "Links the bank accounts into prism."),
  ("Coinbase", "USDC wallet for scheduled transfers."),
  ("Link", "Per-purchase virtual cards that you approve."),
  ("Bland AI", "AI phone calls for Alexandra. Trial cancel reminder set for 10/14."),
  ("SMS Gateway app", "Turns the 904 Android phone into a texting gateway."),
  ("Vercel", "Hosts FitDash and the Panamerica website."),
  ("OpenRouter", "Where Jevons' classifier model runs."),
  ("Discord", "A webhook forwards your Discord messages to Grok, then Nakatoshi."),
 ]),
]

# ---------- PARTNERS ----------
PARTNERS = [
 ("The business", [
  ("Panamerica Auto, LLC", "Your Florida car rental business, operating as a Turo fleet in SWFL. Website panamericafleet.com."),
  ("Thaís", "Co-owner and authorized member of the LLC. Grok is watching the state registry for you to be added as a member."),
 ]),
 ("Fleet and platform", [
  ("Turo", "The rental platform. The fleet runs on the host account “Mike”."),
  ("Mike", "Holds the Turo host account; also runs his own MiKrafts shop (not ours to market)."),
  ("Our cars", "2023 Rivian R1S · 2022 Toyota Corolla · 2024 Toyota Corolla · Tesla Model 3."),
  ("Vivek", "Owner of the Rivian, under a vehicle use agreement."),
  ("SafeWheels (Alex)", "A fleet partner listed on the Panamerica site. Their cars are NOT ours, so the team ignores them."),
  ("Lenders", "Santander, Capital One, GM Financial and Wells Fargo carry the car loans."),
 ]),
 ("Vendors and networks", [
  ("Ausome Movemint", "Lawn care. GVG watches their schedule."),
  ("Boardy", "AI networking service. Sends Briefs and meeting notes, and makes intros."),
  ("Wheelbase", "Demo call on Mon 10/12 with Kellie. Relationship "+U+"."),
  ("Ride.auto", "Listings to sort out, per a Notion to-do. Relationship "+U+"."),
  ("Lyft and Grubhub", "Gig income streams that feed the flywheel."),
  ("LinkedIn discovery", "Three AI-reliability discovery notes (Harmonic, Rillet, Graphite). Replies go to Launch."),
 ]),
]

# ---------- DAY ----------
DAY = [
 ("1:38 AM","Grok","Turo overnight check","Catches evening bookings and puts them on the Turo calendar."),
 ("all night","prism / finley","Relays and backups","Turo and 904 relays stay on. The finley puller copies prism every hour at :20."),
 ("~6:30 AM","Alexandra / Grok","Roadside send pass","First-contact texts. Reports post to #718. Schedule "+U+"."),
 ("7:49 AM","GVG","Marketplace lead sweep","Finds vehicle leads for Panamerica outreach."),
 ("8:00 AM","Specialists","Outcomes briefs to Grok","Each seat sends one short note if something's worth knowing. Quiet otherwise."),
 ("8:06–8:12 AM","Grok → Pulse","Calendar upkeep","Topic focus blocks (weekdays), then the 7-day calendar fill."),
 ("7:44 AM","Grok","Daily Brief, morning edition","Day ahead, fleet, business, roadside, tasks and what shipped, published on Horizon."),
 ("9:05 AM","Grok","Task Extractor","Pulls action items from the Drive report and files them."),
 ("9:38 AM","Grok","Inbox action pass","Turo to Helm, Boardy notes to tasks, task stars and next steps."),
 ("9:48 AM","Grok → Nakatoshi","YNAB pass","New transactions categorized and approved."),
 ("9:57 AM","Grok","#718 check","Reads and answers GVG's asks."),
 ("4:26 PM","Grok","Autonomous SDLC (weekdays)","Picks up to 3 small slices. Forge builds, Assay checks, Grok merges."),
 ("5:05 PM","Grok","Task Extractor, evening","Second pass on the day's report."),
 ("7:44 PM","Grok","Daily Brief, evening edition","Tomorrow, due soon, loose ends, roadside replies, fleet."),
 ("late night","Cadence","Nightly grooming","Backlog grooming. No nightly schedule found on the box: "+U+"."),
]

ASK = [
 ("Anything at all, or not sure who","Grok"),
 ("A Turo booking, guest, toll or invoice","Helm (through Grok)"),
 ("Calling or texting someone from the 904 line","Alexandra (through Grok)"),
 ("Moving something on your calendar or week","Pulse"),
 ("Money, YNAB, FCC, fund weights","Nakatoshi"),
 ("What the macro picture means","Meridian"),
 ("A cheaper way to buy something","Scrip"),
 ("A bug or feature in your apps","Grok files it; Forge or Buzz builds it"),
 ("Backlog status, what's ready","Cadence"),
 ("“Is this actually working?”","Assay"),
 ("Workouts or gym gear","Frankenfit"),
 ("Food and macros","Nourish"),
 ("Sleep, hydration, rest days","Restore"),
 ("Turning a tool into a product","Launch, with Herald on messaging"),
 ("New market ideas","Quarry finds, Lens ranks"),
 ("An X article","Byline"),
 ("Social posts, Marketplace leads, lawn schedule","GVG"),
]

PAUSED = [
 ("Grok","FitDash pace nags (11 AM/3 PM/7 PM)","Paused 9/13"),
 ("Frankenfit, Restore","FitDash pace nags","Off"),
 ("Grok","Daily XA draft kick (8:26 AM)","Paused until a topic is queued"),
 ("Grok","Home gym Marketplace (7:30 PM)","Paused (cost)"),
 ("Grok","Initiative opportunities, Weekly roster keep/cut","Folded into the COS Monday report"),
 ("Grok","Ready queue pickup health","Stood down; GVG took it 9/13"),
 ("Grok","Panamerica ownership plans reminder","Off"),
 ("Grok","Nicole rent transfers, $25 daily and $350 biweekly","Paused (per the Daily Brief transfer list; the box copy still shows biweekly on)"),
 ("Grok","JR-strcUSX $35 reminder","Retired"),
 ("Grok","Daily brief 9:00 AM 1:1 synthesis","Replaced by the Daily Brief editions "+U),
 ("Nakatoshi","Own daily brief, YNAB in-map pass, YNAB cooldown","Off (Grok's 9:48 pass drives it)"),
 ("Assay","#256 / #257 pull-request QA triggers","Off"),
 ("Byline","Weekday X-articles scout","Off"),
 ("Scrip","Daily save brief","Off"),
 ("GVG","Daily gym scheduling cron","Disabled 10/11 (Frankenfit's scheduler now)"),
 ("GVG","inbox-morning-briefing, evening-wind-down-debrief","Being retired into the Daily Brief"),
 ("GVG","ops-eng-pipeline-check, life-compass-761-watch, watch-723","Proposed for retirement 10/9 "+U),
 ("Parked products","Orchestra · Walmart grocery routine · Quo SMS desk","Parked until you unpark them"),
]

# ---------- SVG ----------
def svg():
    W,H=1200,900
    cx,cy=600,420
    out=[f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Ecosystem map" class="eco">']
    out.append('<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#9aa1ad"/></marker></defs>')
    pods=[("money",-90,"Nakatoshi · Meridian · Scrip"),
          ("eng",-38,"Forge · Cadence"),
          ("qa",14,"Assay · Jevons"),
          ("health",66,"Frankenfit · Nourish · Restore"),
          ("gtm",114,"Launch · Herald · Quarry · Lens"),
          ("content",166,"Byline"),
          ("ops",218,"Helm · Alexandra · Pulse")]
    rx,ry=300,262
    # left column ext
    lx,lw=24,170
    out.append(f'<text x="{lx}" y="70" class="colh">OUTSIDE GROK BOT</text>')
    ext=[("GVG","Great Value Grok","social · leads · lawn"),("Buzz / GrokBuild","eng worker","builds ready issues")]
    ey=[150,560]
    for (t,s,d),y in zip(ext,ey):
        out.append(f'<rect x="{lx}" y="{y}" width="{lw}" height="92" rx="14" fill="#eceef1" stroke="#c9cdd4"/>')
        out.append(f'<text x="{lx+16}" y="{y+30}" class="pt">{E(t)}</text><text x="{lx+16}" y="{y+52}" class="ps">{E(s)}</text><text x="{lx+16}" y="{y+72}" class="ps">{E(d)}</text>')
    # GitHub hub between
    gy=360
    out.append(f'<rect x="{lx}" y="{gy}" width="{lw}" height="84" rx="14" fill="#fff" stroke="#555b66" stroke-dasharray="4 4"/>')
    out.append(f'<text x="{lx+16}" y="{gy+30}" class="pt">GitHub</text><text x="{lx+16}" y="{gy+52}" class="ps">#718 direct line</text><text x="{lx+16}" y="{gy+70}" class="ps">issues = backlog</text>')
    out.append(f'<line x1="{lx+lw/2}" y1="242" x2="{lx+lw/2}" y2="{gy}" stroke="#9aa1ad" stroke-dasharray="5 5"/>')
    out.append(f'<line x1="{lx+lw/2}" y1="{gy+84}" x2="{lx+lw/2}" y2="560" stroke="#9aa1ad" stroke-dasharray="5 5"/>')
    # right column systems
    sx,sw=1006,170
    out.append(f'<text x="{sx}" y="70" class="colh">SYSTEMS</text>')
    systems=[("prism","home Pi · Tailscale"),("Horizon","Daily Brief · macro"),("FCC","finance dashboard"),("FitDash · Auto Fleet","Time Allocator"),("Notion · Google","todos · mail · calendar"),("finley","backup host")]
    for i,(t,s) in enumerate(systems):
        y=100+i*112
        out.append(f'<rect x="{sx}" y="{y}" width="{sw}" height="78" rx="14" fill="#f4f6f8" stroke="#c9cdd4"/>')
        out.append(f'<text x="{sx+16}" y="{y+32}" class="pt">{E(t)}</text><text x="{sx+16}" y="{y+54}" class="ps">{E(s)}</text>')
    # bottom partners
    out.append(f'<rect x="230" y="800" width="740" height="76" rx="16" fill="#fbf8f2" stroke="#e2d6bf"/>')
    out.append('<text x="254" y="830" class="pt">Partners &amp; vendors</text>')
    out.append('<text x="254" y="854" class="ps">Panamerica Auto · Turo (host “Mike”) · Ausome Movemint · Boardy · Bland AI · lenders · Wheelbase / Ride.auto (unconfirmed)</text>')
    # connectors grok -> pods
    gyk=cy+92
    for a,ang,names in pods:
        r=math.radians(ang)
        px,py=cx+rx*math.cos(r), cy+ry*math.sin(r)
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{px:.0f}" y2="{py:.0f}" stroke="#cfd4dc" stroke-width="1.5"/>')
    # GVG <-> Grok link
    out.append(f'<path d="M{lx+lw},{gy+42} C 330,{gy+42} 420,{cy+40} {cx-86},{cy+110}" fill="none" stroke="#555b66" stroke-width="1.5" stroke-dasharray="6 5" marker-end="url(#ar)"/>')
    out.append(f'<text x="270" y="{gy+30}" class="lbl">GVG ↔ Grok on #718</text>')
    # systems link
    out.append(f'<path d="M{cx+70},{cy+92} C 820,{cy+120} 900,{150} {sx},139" fill="none" stroke="#9aa1ad" stroke-width="1.2" stroke-dasharray="3 5"/>')
    # partners link
    out.append(f'<line x1="{cx}" y1="{cy+120}" x2="{cx}" y2="800" stroke="#e2d6bf" stroke-width="1.5" stroke-dasharray="3 5"/>')
    # pods
    pw,ph=196,74
    for a,ang,names in pods:
        r=math.radians(ang)
        px,py=cx+rx*math.cos(r), cy+ry*math.sin(r)
        lab,fg,bg=AREAS[a]
        out.append(f'<rect x="{px-pw/2:.0f}" y="{py-ph/2:.0f}" width="{pw}" height="{ph}" rx="16" fill="{bg}" stroke="{fg}" stroke-opacity=".35"/>')
        out.append(f'<text x="{px:.0f}" y="{py-6:.0f}" text-anchor="middle" class="pt" fill="{fg}">{E(lab)}</text>')
        out.append(f'<text x="{px:.0f}" y="{py+16:.0f}" text-anchor="middle" class="ps">{E(names)}</text>')
    # Chris + Grok
    out.append(f'<circle cx="{cx}" cy="{cy}" r="74" fill="#1f2a44"/>')
    out.append(f'<text x="{cx}" y="{cy-4}" text-anchor="middle" class="ct">Chris</text><text x="{cx}" y="{cy+20}" text-anchor="middle" class="cs">decides</text>')
    out.append(f'<rect x="{cx-86}" y="{cy+88}" width="172" height="44" rx="22" fill="#fff" stroke="#1f2a44" stroke-width="1.5"/>')
    out.append(f'<text x="{cx}" y="{cy+109}" text-anchor="middle" class="pt">Grok.btc⚡</text><text x="{cx}" y="{cy+125}" text-anchor="middle" class="ps">chief of staff</text>')
    out.append('</svg>')
    return "\n".join(out)

def card(x):
    lab,fg,bg=AREAS[x["area"]]
    li=lambda L:"".join("<li>"+safe(s)+"</li>" for s in L)
    wide=' wide' if x["area"]=="core" else ''
    return f'''<article class="card{wide}" style="--fg:{fg};--bg:{bg}">
<div class="tag">{E(lab)}</div>
<h3>{E(x["name"])}</h3>
<p class="role">{E(x["role"])}</p>
<div class="kv"><h4>Owns</h4><ul>{li(x["owns"])}</ul></div>
<div class="kv"><h4>Recurring routines <span class="et">ET</span></h4><ul class="rt">{li(x["routines"])}</ul></div>
<div class="kv two"><div><h4>Talks to</h4><p>{E(x["talks"])}</p></div><div><h4>How to reach / where they live</h4><p>{E(x["reach"])}</p></div></div>
</article>'''

def safe(s):
    return s if 'class="unc"' in s else E(s)

order=["core","ops","money","eng","qa","health","gtm","content","ext"]
cards_html=card([x for x in M if x['area']=='core'][0])+'<div class="cards">'
for a in order[1:]:
    group=[x for x in M if x["area"]==a]
    if not group: continue
    lab,fg,bg=AREAS[a]
    cards_html+=f'<h3 class="grp" style="--fg:{fg}"><span>{E(lab)}</span></h3>'+"".join(card(x) for x in group)
cards_html+='</div>'

rooms_html="".join(f'<li><b>{E(n)}</b><span>{E(mm)}</span></li>' for n,mm in ROOMS)
sys_html=""
for h,items in SYS:
    sys_html+=f'<div class="sysblk"><h3>{E(h)}</h3><dl>'+"".join(f'<dt>{E(n)}</dt><dd>{safe(d)}</dd>' for n,d in items)+'</dl></div>'
par_html=""
for h,items in PARTNERS:
    par_html+=f'<div class="sysblk"><h3>{E(h)}</h3><dl>'+"".join(f'<dt>{E(n)}</dt><dd>{safe(d)}</dd>' for n,d in items)+'</dl></div>'
day_html="".join(f'<li><time>{E(t)}</time><div><b>{E(w)}</b> <span class="who">{E(who)}</span><p>{safe(d)}</p></div></li>' for t,who,w,d in DAY)
ask_html="".join(f'<tr><td>{E(q)}</td><td>{E(a)}</td></tr>' for q,a in ASK)
pz_html="".join(f'<tr><td>{E(w)}</td><td>{E(r)}</td><td>{safe(s)}</td></tr>' for w,r,s in PAUSED)

UNC = [
 "Grok's Daily sprint check at 12:24 PM (seen only in a 10/3 planning note, not in the box's routine list)",
 "Alexandra's roadside send-pass schedule (~6:30 AM per GVG's spec, plus afternoon passes)",
 "Pulse adopting GVG's one-off reminders (Bland 10/14, Devin 10/16, HDHP 11/1)",
 "Nakatoshi's Coinbase send reconciliation schedule",
 "Scrip taking over GVG's price watch and subscription scan",
 "Forge taking over GitHub Actions failure and #701 alert triage",
 "Cadence taking over the issue close-out watch",
 "Cadence's nightly grooming: no nightly routine found",
 "GVG's role change to social, marketing and outreach director (10/9 plan)",
 "GVG's #718 watch (2 h), Ausome and Messenger watches (4 h), Facebook drafts (M/W/F), as of its 10/3 inventory",
 "GVG's retirement of ops-eng-pipeline-check, life-compass-761 and watch-723",
 "Buzz/GrokBuild sweep cadence",
 "The 9:00 AM Daily brief 1:1 being replaced by the Daily Brief editions",
 "Cloudflare R2 off-site bucket live status",
 "Wheelbase and Ride.auto relationships",
]
unc_html="".join(f"<li>{E(u)}</li>" for u in UNC)

CSS = open(os.path.join(HERE, 'style.css')).read()
doc=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Team &amp; Systems Directory · ChrisV.btc</title><style>{CSS}</style></head><body>
<header class="mast">
 <div class="kicker">Reference guide · as of Sunday, October 11, 2026</div>
 <h1>Team &amp; Systems Directory</h1>
 <p class="dek">Who's on your team, what each one owns, when they work, and how it all fits together. Every time is Eastern.</p>
 <nav><a href="#map">Map</a><a href="#team">Team</a><a href="#systems">Systems</a><a href="#partners">Partners</a><a href="#day">A day</a><a href="#ask">Who to ask</a><a href="#appendix">Paused</a></nav>
</header>
<main>
<section id="map" class="sec">
 <div class="sechead"><span class="num">01</span><h2>The big picture</h2></div>
 <p class="lede">You make the calls. Grok turns them into work and hands it to the right specialist. Specialists report back to Grok, and Grok briefs you once. GVG and Buzz work alongside the team through GitHub. Everything runs on prism, your home server.</p>
 <figure class="fig">{svg()}</figure>
 <div class="rules">
  <div><b>The one rule</b><p>Specialists → Grok → you. Only two things skip Grok: an emergency STOP, and Byline's article drafts for your review.</p></div>
  <div><b>Quiet by default</b><p>Every daily brief is “send only if it matters.” Silence means nothing needs you.</p></div>
  <div><b>Money is yours</b><p>No teammate buys, trades or sends money unless you've ordered it or set up a standing transfer.</p></div>
 </div>
</section>

<section id="team" class="sec">
 <div class="sechead"><span class="num">02</span><h2>The team</h2></div>
 <p class="lede">One card per member, grouped by area. Schedules come from each bot's routine files on the box and are shown in ET. Anything marked <span class="unc">unconfirmed</span> couldn't be verified.</p>
 {cards_html}
 <div class="rooms"><h3>Team rooms (group chats, not bots)</h3><ul>{rooms_html}</ul></div>
</section>

<section id="systems" class="sec">
 <div class="sechead"><span class="num">03</span><h2>Systems &amp; services</h2></div>
 <p class="lede">The places where work happens and data lives. prism is home base. The rest either runs on it or plugs into it.</p>
 <div class="sysgrid">{sys_html}</div>
</section>

<section id="partners" class="sec">
 <div class="sechead"><span class="num">04</span><h2>Partners &amp; people</h2></div>
 <p class="lede">Business relationships only. People appear by first name and role.</p>
 <div class="sysgrid">{par_html}</div>
</section>

<section id="day" class="sec">
 <div class="sechead"><span class="num">05</span><h2>How a typical day flows</h2></div>
 <p class="lede">Overnight checks, the 8 AM round of team notes, the morning paper, the inbox and money passes, then the evening edition.</p>
 <ol class="timeline">{day_html}</ol>
</section>

<section id="ask" class="sec">
 <div class="sechead"><span class="num">06</span><h2>Who to ask for what</h2></div>
 <p class="lede">In practice you can always just tell Grok. This table shows who ends up doing the work.</p>
 <table class="ask"><thead><tr><th>When you need…</th><th>Goes to</th></tr></thead><tbody>{ask_html}</tbody></table>
</section>

<section id="appendix" class="sec small">
 <div class="sechead"><span class="num">A</span><h2>Paused &amp; retired</h2></div>
 <table class="pz"><thead><tr><th>Who</th><th>Routine</th><th>Status</th></tr></thead><tbody>{pz_html}</tbody></table>
 <h3 class="unch">Couldn't confirm</h3>
 <ul class="unclist">{unc_html}</ul>
 <p class="src">Sources: each bot's profile and routine files on the shared box (the box copy was last refreshed in mid-September, so newer changes come from the Daily Brief checklist, the 10/3 and 10/9 GVG handoff notes, the repo's relay docs, and facts you confirmed). No secrets, phone numbers or addresses are included.</p>
</section>
</main>
<footer>Made for Chris by Grok Bot · Team &amp; Systems Directory · October 2026</footer>
</body></html>'''
open(os.path.join(HERE, 'index.html'), 'w', encoding='utf-8').write(doc)
print(len(doc))
