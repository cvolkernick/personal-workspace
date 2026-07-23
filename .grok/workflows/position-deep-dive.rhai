// Position deep-dive for agentic fund manager (watchlist / candidates).
// Run: /position-deep-dive symbol=BE
// Optional: theme, context, force

let meta = #{
    name: "position-deep-dive",
    description: "Multi-agent deep dive on a ticker the fund manager is considering; writes investment/research report",
    when_to_use: "Before sizing a non-core / watchlist name, or on recurring review of candidates (e.g. BE energy)",
    phases: [
        #{ title: "Frame", detail: "Load thesis, watchlist, book context" },
        #{ title: "Research", detail: "Market + thesis + risk fan-out" },
        #{ title: "Critique", detail: "Adversarial challenge" },
        #{ title: "Synthesize", detail: "Verdict + write report" },
    ],
};

let frame_schema = #{
    "type": "object",
    "required": ["symbol", "theme", "thesis_fit", "sleeve_if_owned", "on_watchlist", "already_held", "policy_notes"],
    "properties": #{
        "symbol": #{ "type": "string" },
        "theme": #{ "type": "string" },
        "thesis_fit": #{ "type": "string" },
        "sleeve_if_owned": #{ "type": "string" },
        "on_watchlist": #{ "type": "boolean" },
        "already_held": #{ "type": "boolean" },
        "policy_notes": #{ "type": "string" },
        "open_questions": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 8 },
    },
};

let research_schema = #{
    "type": "object",
    "required": ["bullets", "confidence"],
    "properties": #{
        "bullets": #{
            "type": "array",
            "maxItems": 10,
            "items": #{ "type": "string" },
        },
        "confidence": #{ "type": "number" },
        "sources_note": #{ "type": "string" },
        "key_risks": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 8 },
        "key_catalysts": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 8 },
    },
};

let critique_schema = #{
    "type": "object",
    "required": ["blocks_buy", "reasons", "max_size_guidance"],
    "properties": #{
        "blocks_buy": #{ "type": "boolean" },
        "reasons": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 8 },
        "max_size_guidance": #{ "type": "string" },
        "missing_evidence": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 6 },
    },
};

let verdict_schema = #{
    "type": "object",
    "required": ["status", "summary", "why_now", "why_not", "next_actions", "report_markdown"],
    "properties": #{
        "status": #{ "type": "string" },
        "summary": #{ "type": "string" },
        "why_now": #{ "type": "string" },
        "why_not": #{ "type": "string" },
        "suggested_sleeve": #{ "type": "string" },
        "confidence": #{ "type": "number" },
        "next_actions": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 8 },
        "report_markdown": #{ "type": "string" },
        "watchlist_update": #{
            "type": "object",
            "properties": #{
                "status": #{ "type": "string" },
                "note": #{ "type": "string" },
            },
        },
    },
};

// --- args ---
let symbol = if args == () { () } else { args.symbol };
if symbol == () { pause("verification", "Pass args.symbol (ticker), e.g. symbol=BE"); }

// Normalize symbol to uppercase string for paths/prompts
let sym = symbol;
if type_of(sym) != "string" {
    pause("verification", "args.symbol must be a string ticker");
}
// basic trim
sym.trim();
if sym == "" { pause("verification", "args.symbol is empty"); }

let theme_arg = if args == () { () } else { args.theme };
let context_arg = if args == () { () } else { args.context };
let force_arg = if args == () { () } else { args.force };

let theme_s = if theme_arg == () { "" } else { theme_arg };
let context_s = if context_arg == () { "" } else { context_arg };
let force_s = if force_arg == () { "false" } else { "" + force_arg };

log("Deep dive starting for " + sym);

// ========== Frame ==========
phase("Frame");

let frame_prompt = "";
frame_prompt += "You are the Scout/frame agent for an agentic Robinhood fund manager deep-dive.\n";
frame_prompt += "Symbol under review: " + sym + "\n";
if theme_s != "" {
    frame_prompt += "Suggested theme: " + theme_s + "\n";
}
if context_s != "" {
    frame_prompt += "Extra context: " + context_s + "\n";
}
frame_prompt += "Workspace root is the personal-workspace treasury worktree.\n";
frame_prompt += "REQUIRED: use read_file / grep / list_dir (read-only tools) on:\n";
frame_prompt += "  - investment/watchlist.json\n";
frame_prompt += "  - investment/fund_manager.json\n";
frame_prompt += "  - investment/positions.md\n";
frame_prompt += "  - investment/README.md\n";
frame_prompt += "  - investment/research/ (any existing " + sym + " report)\n";
frame_prompt += "  - treasury/snapshots/fund_manager_latest.json (if present) for held positions\n";
frame_prompt += "Do NOT place trades. Do NOT invent file contents — read them.\n";
frame_prompt += "Return structured fields: symbol (uppercase), theme, thesis_fit from watchlist if present,\n";
frame_prompt += "sleeve_if_owned (default stocks_growth for energy/AI equities), on_watchlist, already_held,\n";
frame_prompt += "policy_notes summarizing auto_buy=false and deep_dive rules, open_questions (up to 8).\n";

let frame = agent(frame_prompt, #{
    label: "frame:" + sym,
    capability_mode: "read-only",
    output_schema: frame_schema,
});

if frame == () || !frame.success || frame.output == () {
    pause("infra", "Frame agent failed — resume to retry or fix workspace paths.");
}

let framed = frame.output;
log("Framed " + framed.symbol + " on_watchlist=" + ("" + framed.on_watchlist));

// ========== Research fan-out ==========
phase("Research");

let common_ctx = "";
common_ctx += "Symbol: " + sym + "\n";
common_ctx += "Theme: " + framed.theme + "\n";
common_ctx += "Thesis fit (from watchlist/frame): " + framed.thesis_fit + "\n";
common_ctx += "Sleeve if owned: " + framed.sleeve_if_owned + "\n";
common_ctx += "On watchlist: " + ("" + framed.on_watchlist) + "\n";
common_ctx += "Already held: " + ("" + framed.already_held) + "\n";
common_ctx += "Policy: " + framed.policy_notes + "\n";
common_ctx += "Portfolio thesis: modernized 40% BTC/digital-credit complex + 60% stocks/growth;\n";
common_ctx += "energy is an open opportunistic theme under stocks/growth, not a mandated sleeve.\n";
common_ctx += "Agentic account only; no day-trading mandate.\n";
common_ctx += "You may use web_search / open_page for public info and company context.\n";
common_ctx += "If Robinhood MCP tools are available, prefer live quote/fundamental snippets; otherwise web.\n";
common_ctx += "Do NOT place trades. Cite concrete facts; mark uncertainty.\n";

let market_p = "";
market_p += common_ctx;
market_p += "ROLE: Market / company snapshot for " + sym + ".\n";
market_p += "Cover: what the company does, recent price regime (direction/vol), liquidity, sector peers,\n";
market_p += "and 3-5 near-term catalysts or news items if findable. Prefer tools over memory.\n";
market_p += "Return bullets (max 10), confidence 0-1, sources_note, key_catalysts, key_risks.\n";

let thesis_p = "";
thesis_p += common_ctx;
thesis_p += "ROLE: Thesis agent — does " + sym + " fit the investment book?\n";
thesis_p += "Map to energy / AI power / growth themes. Bull case only with evidence.\n";
thesis_p += "Contrast with core allowlist (TSLA/SPCX/miners/MSTR etc.) — why this name vs those.\n";
thesis_p += "Return bullets, confidence 0-1, sources_note, key_catalysts, key_risks.\n";

let risk_p = "";
risk_p += common_ctx;
risk_p += "ROLE: Risk agent — bear case and size discipline for " + sym + ".\n";
risk_p += "Cover: business risks, valuation/leverage concerns if known, concentration vs small agentic NAV,\n";
risk_p += "correlation with existing book (BTC-complex + growth), liquidity, binary events.\n";
risk_p += "Return bullets, confidence 0-1, sources_note, key_risks (primary), key_catalysts.\n";

let research = parallel([
    #{ prompt: market_p, label: "market:" + sym, capability_mode: "read-only", output_schema: research_schema },
    #{ prompt: thesis_p, label: "thesis:" + sym, capability_mode: "read-only", output_schema: research_schema },
    #{ prompt: risk_p, label: "risk:" + sym, capability_mode: "read-only", output_schema: research_schema },
]);

let market_out = if research[0] != () && research[0].success { research[0].output } else { () };
let thesis_out = if research[1] != () && research[1].success { research[1].output } else { () };
let risk_out = if research[2] != () && research[2].success { research[2].output } else { () };

if market_out == () && thesis_out == () && risk_out == () {
    pause("infra", "All research agents failed — resume to retry.");
}

// Encode partial results for critique prompt
let market_json = if market_out == () { "{\"error\":\"missing\"}" } else { json_encode(market_out) };
let thesis_json = if thesis_out == () { "{\"error\":\"missing\"}" } else { json_encode(thesis_out) };
let risk_json = if risk_out == () { "{\"error\":\"missing\"}" } else { json_encode(risk_out) };

// ========== Critique ==========
phase("Critique");

let crit_p = "";
crit_p += "You are the Critic for fund-manager position deep-dive on " + sym + ".\n";
crit_p += "Adversarially challenge the research. Default to blocks_buy=true unless evidence is strong.\n";
crit_p += "Frame notes: " + framed.policy_notes + "\n";
crit_p += "MARKET JSON:\n" + market_json + "\n";
crit_p += "THESIS JSON:\n" + thesis_json + "\n";
crit_p += "RISK JSON:\n" + risk_json + "\n";
crit_p += "Set blocks_buy, reasons (max 8), max_size_guidance (e.g. starter only / pass / wait for X),\n";
crit_p += "missing_evidence (what would change your mind).\n";
crit_p += "No trades. Prefer fail-closed on hype or thin evidence.\n";

let crit = agent(crit_p, #{
    label: "critic:" + sym,
    capability_mode: "read-only",
    output_schema: critique_schema,
});

let crit_out = if crit != () && crit.success { crit.output } else { () };
let crit_json = if crit_out == () { "{\"blocks_buy\":true,\"reasons\":[\"critic failed\"],\"max_size_guidance\":\"pass\"}" } else { json_encode(crit_out) };

// ========== Synthesize + write ==========
phase("Synthesize");

let syn_p = "";
syn_p += "You are the synthesizer for position deep-dive on " + sym + ".\n";
syn_p += "Produce a final verdict and a full markdown report for the fund manager team.\n";
syn_p += "status MUST be one of: monitor | ready | pass | held | promoted\n";
syn_p += "  monitor = stay on watchlist, no buy yet\n";
syn_p += "  ready = eligible for Thesis/Risk to propose a small size (not an order by itself)\n";
syn_p += "  pass = do not buy for now\n";
syn_p += "  held = already in book; reaffirm or trim guidance only\n";
syn_p += "  promoted = recommend adding to core allowlist (rare)\n";
syn_p += "Respect critic: if blocks_buy true, status should be monitor or pass, not ready/promoted.\n";
syn_p += "FRAME:\n" + json_encode(framed) + "\n";
syn_p += "MARKET:\n" + market_json + "\n";
syn_p += "THESIS:\n" + thesis_json + "\n";
syn_p += "RISK:\n" + risk_json + "\n";
syn_p += "CRITIC:\n" + crit_json + "\n";
syn_p += "report_markdown requirements — be VERBOSE (this is the permanent research artifact):\n";
syn_p += "  # " + sym + " deep dive\n";
syn_p += "  ## Executive findings (read this first) — 5+ numbered findings\n";
syn_p += "  ## Conclusions (actionable table: status, buy?, promote?, max size)\n";
syn_p += "  ## Frame / policy context\n";
syn_p += "  ## Company and product findings\n";
syn_p += "  ## Market and trading findings\n";
syn_p += "  ## Fundamental findings (with numbers when available)\n";
syn_p += "  ## Bull case (expanded) + confidence\n";
syn_p += "  ## Bear case (expanded) + confidence\n";
syn_p += "  ## Critic fail-closed + promotion criteria\n";
syn_p += "  ## Comparison to core book / opportunity cost\n";
syn_p += "  ## Verdict (verbose paragraphs + confidence table)\n";
syn_p += "  ## Next actions by Scout/Thesis/Risk/Critic/Executor\n";
syn_p += "  ## Sources\n";
syn_p += "  mention agentic-only book and that this is research not an order.\n";
syn_p += "summary/why_now/why_not fields: full sentences, not one-liners.\n";
syn_p += "Also set watchlist_update.status and note for humans/agents to merge into watchlist.json.\n";
syn_p += "next_actions: concrete team steps (e.g. re-quote, wait for earnings, starter size proposal).\n";
syn_p += "After you output the schema, ALSO write the same report body to file using write tool:\n";
syn_p += "  path: investment/research/" + sym + "_deep_dive.md\n";
syn_p += "Use capability write for that file only under investment/research/.\n";

let syn = agent(syn_p, #{
    label: "synthesize:" + sym,
    capability_mode: "read-write",
    output_schema: verdict_schema,
});

if syn == () || !syn.success || syn.output == () {
    pause("infra", "Synthesize agent failed — research may still be partial; resume to retry.");
}

let verdict = syn.output;

// Scratch copy of report for workflow UI
let report_body = if verdict.report_markdown == () { "" } else { verdict.report_markdown };
let scratch = write_scratch_file("deep_dive_report.md", report_body);

// Ensure workspace file exists even if agent forgot write tool
// (agent should have written; we still complete with path convention)
let report_path = "investment/research/" + sym + "_deep_dive.md";

log("Verdict status=" + verdict.status + " for " + sym);

complete(#{
    symbol: sym,
    status: verdict.status,
    summary: verdict.summary,
    why_now: verdict.why_now,
    why_not: verdict.why_not,
    confidence: verdict.confidence,
    next_actions: verdict.next_actions,
    watchlist_update: verdict.watchlist_update,
    report_path: report_path,
    scratch_report: scratch,
    on_watchlist: framed.on_watchlist,
    already_held: framed.already_held,
    force: force_s,
});
