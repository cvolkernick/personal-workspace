// Fund-manager multi-agent research (portfolio, strategy, watchlist, candidates).
// Run: /fund-manager-research
// Optional args: focus (e.g. "energy"), max_candidates (default 5), include_held (bool-ish)

let meta = #{
    name: "fund-manager-research",
    description: "Multi-agent review of agentic portfolio, strategy, watchlist; propose watchlist adds and deep-dive priorities",
    when_to_use: "Daily/periodic fund-manager research pass; discover thematic candidates; prioritize position-deep-dive",
    phases: [
        #{ title: "Frame", detail: "Load policy, book, watchlist, research corpus" },
        #{ title: "Parallel analysis", detail: "Book health + theme scan + watchlist health" },
        #{ title: "Discover", detail: "Propose new watchlist candidates (not orders)" },
        #{ title: "Prioritize", detail: "Deep-dive queue + synthesize report" },
    ],
};

let frame_schema = #{
    "type": "object",
    "required": ["nav_usd", "held_symbols", "watchlist_symbols", "weights_note", "policy_notes"],
    "properties": #{
        "nav_usd": #{ "type": "number" },
        "held_symbols": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 40 },
        "watchlist_symbols": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 40 },
        "weights_note": #{ "type": "string" },
        "policy_notes": #{ "type": "string" },
        "open_research_gaps": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 10 },
    },
};

let analysis_schema = #{
    "type": "object",
    "required": ["findings", "conclusions", "confidence"],
    "properties": #{
        "findings": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 12 },
        "conclusions": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 8 },
        "confidence": #{ "type": "number" },
        "risks": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 8 },
    },
};

let candidates_schema = #{
    "type": "object",
    "required": ["candidates"],
    "properties": #{
        "candidates": #{
            "type": "array",
            "maxItems": 8,
            "items": #{
                "type": "object",
                "required": ["symbol", "theme", "why", "priority"],
                "properties": #{
                    "symbol": #{ "type": "string" },
                    "name": #{ "type": "string" },
                    "theme": #{ "type": "string" },
                    "why": #{ "type": "string" },
                    "priority": #{ "type": "string" },
                    "sleeve_if_owned": #{ "type": "string" },
                    "already_on_watchlist": #{ "type": "boolean" },
                    "suggest_deep_dive": #{ "type": "boolean" },
                },
            },
        },
        "notes": #{ "type": "string" },
    },
};

let plan_schema = #{
    "type": "object",
    "required": ["summary", "deep_dive_queue", "watchlist_add_proposals", "portfolio_conclusions", "report_markdown"],
    "properties": #{
        "summary": #{ "type": "string" },
        "deep_dive_queue": #{
            "type": "array",
            "maxItems": 8,
            "items": #{
                "type": "object",
                "required": ["symbol", "reason"],
                "properties": #{
                    "symbol": #{ "type": "string" },
                    "reason": #{ "type": "string" },
                    "urgency": #{ "type": "string" },
                },
            },
        },
        "watchlist_add_proposals": #{
            "type": "array",
            "maxItems": 8,
            "items": #{
                "type": "object",
                "required": ["symbol", "theme", "why"],
                "properties": #{
                    "symbol": #{ "type": "string" },
                    "theme": #{ "type": "string" },
                    "why": #{ "type": "string" },
                    "name": #{ "type": "string" },
                },
            },
        },
        "portfolio_conclusions": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 10 },
        "do_not_trade": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 10 },
        "report_markdown": #{ "type": "string" },
    },
};

let focus = if args == () { "" } else { if args.focus == () { "" } else { args.focus } };
let max_c = if args == () { "5" } else { if args.max_candidates == () { "5" } else { "" + args.max_candidates } };

log("fund-manager-research start focus=" + focus);

// ========== Frame ==========
phase("Frame");

let frame_p = "";
frame_p += "You are Scout/frame for the agentic Robinhood fund manager RESEARCH pass (not trading).\n";
frame_p += "Workspace: personal-workspace treasury worktree.\n";
frame_p += "REQUIRED: read with tools (read_file/grep/list_dir):\n";
frame_p += "  investment/fund_manager.json\n";
frame_p += "  investment/watchlist.json\n";
frame_p += "  investment/positions.md\n";
frame_p += "  investment/README.md\n";
frame_p += "  investment/research/ (list reports)\n";
frame_p += "  treasury/snapshots/fund_manager_latest.json and/or robinhood_latest.json if present\n";
frame_p += "Extract: nav_usd, held_symbols, watchlist_symbols, weights_note, policy_notes (auto_buy false, deep-dive rules),\n";
frame_p += "open_research_gaps (e.g. stale deep-dives, empty energy sleeve, thin stocks allowlist).\n";
frame_p += "Do NOT place trades. Do not invent holdings — read snapshots.\n";
if focus != "" {
    frame_p += "Owner focus hint: " + focus + "\n";
}

let frame = agent(frame_p, #{
    label: "fm-frame",
    capability_mode: "read-only",
    output_schema: frame_schema,
});

if frame == () || !frame.success || frame.output == () {
    pause("infra", "Frame failed — resume when investment/ and snapshots are available.");
}

let framed = frame.output;
let frame_json = json_encode(framed);
log("Framed nav and watchlist");

// ========== Parallel analysis ==========
phase("Parallel analysis");

let book_p = "";
book_p += "ROLE: Portfolio health analyst for agentic fund manager.\n";
book_p += "FRAME JSON:\n" + frame_json + "\n";
book_p += "Assess 40/60 modernized targets, concentration, cash, whether book is too small for satellites.\n";
book_p += "Use read-only tools to re-check fund_manager_latest / positions if needed.\n";
book_p += "Return findings (verbose bullets), conclusions, confidence 0-1, risks. No trades.\n";

let theme_p = "";
theme_p += "ROLE: Strategy / theme analyst.\n";
theme_p += "FRAME JSON:\n" + frame_json + "\n";
theme_p += "Read investment/README.md themes: BTC/digital credit, AI stack, energy, growth.\n";
theme_p += "Where is the book strong vs thin vs unexpressed (esp. energy)?\n";
if focus != "" {
    theme_p += "Emphasize focus: " + focus + "\n";
}
theme_p += "You may web_search for current theme context. Return findings, conclusions, confidence, risks. No trades.\n";

let wl_p = "";
wl_p += "ROLE: Watchlist health analyst.\n";
wl_p += "FRAME JSON:\n" + frame_json + "\n";
wl_p += "Read investment/watchlist.json and investment/research/*_deep_dive.md summaries.\n";
wl_p += "For each watchlist name: status, last_deep_dive freshness, ready vs monitor vs pass.\n";
wl_p += "Flag which need re-deep-dive. Return findings, conclusions, confidence, risks. No trades.\n";

let analyses = parallel([
    #{ prompt: book_p, label: "book-health", capability_mode: "read-only", output_schema: analysis_schema },
    #{ prompt: theme_p, label: "theme-scan", capability_mode: "read-only", output_schema: analysis_schema },
    #{ prompt: wl_p, label: "watchlist-health", capability_mode: "read-only", output_schema: analysis_schema },
]);

let book_out = if analyses[0] != () && analyses[0].success { analyses[0].output } else { () };
let theme_out = if analyses[1] != () && analyses[1].success { analyses[1].output } else { () };
let wl_out = if analyses[2] != () && analyses[2].success { analyses[2].output } else { () };

let book_json = if book_out == () { "{\"error\":\"missing\"}" } else { json_encode(book_out) };
let theme_json = if theme_out == () { "{\"error\":\"missing\"}" } else { json_encode(theme_out) };
let wl_json = if wl_out == () { "{\"error\":\"missing\"}" } else { json_encode(wl_out) };

// ========== Discover candidates ==========
phase("Discover");

let disc_p = "";
disc_p += "ROLE: Watchlist discovery (Thesis-oriented). Propose NEW equities to MONITOR — not buy.\n";
disc_p += "Max candidates roughly: " + max_c + "\n";
disc_p += "FRAME:\n" + frame_json + "\n";
disc_p += "BOOK ANALYSIS:\n" + book_json + "\n";
disc_p += "THEME ANALYSIS:\n" + theme_json + "\n";
disc_p += "WATCHLIST ANALYSIS:\n" + wl_json + "\n";
disc_p += "Rules:\n";
disc_p += "- Prefer themes in strategy: energy, AI stack, digital credit adjacent, BTC infra — not random meme names.\n";
disc_p += "- Do NOT re-propose symbols already on watchlist unless priority change; set already_on_watchlist true if you mention them.\n";
disc_p += "- Do NOT propose pure duplicates of held names unless a different instrument is justified.\n";
disc_p += "- Each candidate: symbol, name, theme, why (1-3 sentences), priority high|med|low, sleeve_if_owned, suggest_deep_dive bool.\n";
disc_p += "- Use web_search to validate that tickers are real equities and theme-plausible.\n";
disc_p += "- This is NOT an order list. auto_buy remains false.\n";
if focus != "" {
    disc_p += "Bias discovery toward: " + focus + "\n";
}

let disc = agent(disc_p, #{
    label: "discover-candidates",
    capability_mode: "read-only",
    output_schema: candidates_schema,
});

let disc_out = if disc != () && disc.success { disc.output } else { () };
let disc_json = if disc_out == () { "{\"candidates\":[]}" } else { json_encode(disc_out) };

// ========== Prioritize + write ==========
phase("Prioritize");

let plan_p = "";
plan_p += "You are synthesizer for fund-manager-research. Produce a VERBOSE markdown report and structured plan.\n";
plan_p += "FRAME:\n" + frame_json + "\n";
plan_p += "BOOK:\n" + book_json + "\n";
plan_p += "THEME:\n" + theme_json + "\n";
plan_p += "WATCHLIST:\n" + wl_json + "\n";
plan_p += "CANDIDATES:\n" + disc_json + "\n";
plan_p += "Requirements for report_markdown:\n";
plan_p += "  # Fund manager research — date in title if known\n";
plan_p += "  ## Executive findings and conclusions (verbose)\n";
plan_p += "  ## Portfolio / 40-60 health\n";
plan_p += "  ## Strategy themes coverage\n";
plan_p += "  ## Watchlist status (incl. BE and others)\n";
plan_p += "  ## Proposed watchlist adds (not buys) with rationale\n";
plan_p += "  ## Deep-dive queue (ordered) — invoke /position-deep-dive symbol=X\n";
plan_p += "  ## Explicit do-not-trade list\n";
plan_p += "  ## Next actions by Scout/Thesis/Risk/Critic/Executor\n";
plan_p += "Be verbose on findings and conclusions — fund team will reuse this.\n";
plan_p += "deep_dive_queue: prioritize stale watchlist names and best new candidates.\n";
plan_p += "watchlist_add_proposals: only NEW symbols worth human/agent merge into watchlist.json (status monitor).\n";
plan_p += "portfolio_conclusions: actionable for rebalance thinking without placing orders here.\n";
plan_p += "do_not_trade: names that must not be bought from this research alone.\n";
plan_p += "After schema output, WRITE report to investment/research/fund_manager_research_latest.md using write tool.\n";
plan_p += "Also append a short dated section or copy to investment/research/fund_manager_research_log.md if practical.\n";
plan_p += "No broker orders.\n";

let plan = agent(plan_p, #{
    label: "fm-research-synthesize",
    capability_mode: "read-write",
    output_schema: plan_schema,
});

if plan == () || !plan.success || plan.output == () {
    pause("infra", "Synthesize failed — resume to retry research plan.");
}

let out = plan.output;
let body = if out.report_markdown == () { "" } else { out.report_markdown };
let scratch = write_scratch_file("fund_manager_research.md", body);

log("fund-manager-research complete");

complete(#{
    summary: out.summary,
    deep_dive_queue: out.deep_dive_queue,
    watchlist_add_proposals: out.watchlist_add_proposals,
    portfolio_conclusions: out.portfolio_conclusions,
    do_not_trade: out.do_not_trade,
    report_path: "investment/research/fund_manager_research_latest.md",
    scratch_report: scratch,
    focus: focus,
    invoke_deep_dive: "For each queue item run /position-deep-dive symbol=SYM",
});
