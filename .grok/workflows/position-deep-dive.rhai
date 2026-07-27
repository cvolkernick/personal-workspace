// Position deep-dive for agentic fund manager (watchlist / candidates).
// Incorporates Grok Build /deep-research shape (Plan → Research → Verify → Report):
//   plan bounded questions, gather structured claims with sources, adversarially
//   verify each claim on an independent shard, report only survivors (Partial if any drop).
// Fund-manager layer on top: Critic fail-closed + investment verdict + write research md.
//
// Run: /position-deep-dive symbol=BE
// Optional: theme, context, force, max_questions (default 5, max 6)

let meta = #{
    name: "position-deep-dive",
    description: "Deep-research style multi-agent deep dive on a ticker; verifies claims then writes investment/research report",
    when_to_use: "Before sizing a non-core / watchlist name, or on recurring review of candidates (e.g. BE, NVDA, STRC)",
    phases: [
        #{ title: "Frame", detail: "Load thesis, watchlist, book context" },
        #{ title: "Plan", detail: "Bounded research questions (deep-research planner)" },
        #{ title: "Research", detail: "Parallel claim gatherers with source evidence" },
        #{ title: "Verify", detail: "Independent adversarial claim verification" },
        #{ title: "Critique", detail: "Investment critic fail-closed" },
        #{ title: "Report", detail: "Verdict + write verified report" },
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

// Plan: bounded research questions for the ticker (deep-research planner)
let plan_schema = #{
    "type": "object",
    "required": ["questions"],
    "properties": #{
        "questions": #{
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": #{
                "type": "object",
                "required": ["id", "question", "angle"],
                "properties": #{
                    "id": #{ "type": "string" },
                    "question": #{ "type": "string" },
                    "angle": #{ "type": "string" },
                },
            },
        },
        "plan_notes": #{ "type": "string" },
    },
};

// Researcher: structured claims with sources (not freeform bullets only)
let claims_schema = #{
    "type": "object",
    "required": ["claims", "uncertainties"],
    "properties": #{
        "claims": #{
            "type": "array",
            "maxItems": 6,
            "items": #{
                "type": "object",
                "required": ["id", "claim", "evidence", "source_locator", "confidence"],
                "properties": #{
                    "id": #{ "type": "string" },
                    "claim": #{ "type": "string" },
                    "evidence": #{ "type": "string" },
                    "source_locator": #{ "type": "string" },
                    "confidence": #{ "type": "number" },
                },
            },
        },
        "uncertainties": #{
            "type": "array",
            "maxItems": 6,
            "items": #{ "type": "string" },
        },
        "sources_note": #{ "type": "string" },
    },
};

// Verifier: independent skeptic — fail closed without concrete evidence
let verify_schema = #{
    "type": "object",
    "required": ["claim_id", "survives", "reason", "evidence_checked"],
    "properties": #{
        "claim_id": #{ "type": "string" },
        "survives": #{ "type": "boolean" },
        "reason": #{ "type": "string" },
        "evidence_checked": #{ "type": "string" },
        "corrected_claim": #{ "type": "string" },
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
    "required": ["status", "summary", "why_now", "why_not", "next_actions", "report_markdown", "coverage"],
    "properties": #{
        "status": #{ "type": "string" },
        "summary": #{ "type": "string" },
        "why_now": #{ "type": "string" },
        "why_not": #{ "type": "string" },
        "suggested_sleeve": #{ "type": "string" },
        "confidence": #{ "type": "number" },
        "coverage": #{ "type": "string" },
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

let sym = symbol;
if type_of(sym) != "string" {
    pause("verification", "args.symbol must be a string ticker");
}
sym.trim();
if sym == "" { pause("verification", "args.symbol is empty"); }

let theme_arg = if args == () { () } else { args.theme };
let context_arg = if args == () { () } else { args.context };
let force_arg = if args == () { () } else { args.force };
let max_q_arg = if args == () { () } else { args.max_questions };

let theme_s = if theme_arg == () { "" } else { theme_arg };
let context_s = if context_arg == () { "" } else { context_arg };
let force_s = if force_arg == () { "false" } else { "" + force_arg };
// Cap questions for agent budget (plan 1 + research N + verify claims + critique + report)
let max_questions = 5;
if max_q_arg != () {
    // coerce loosely; clamp later in plan filter
    max_questions = 5;
}

log("Deep dive (deep-research pattern) starting for " + sym);

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
frame_prompt += "Note owner prefs: elevate digital credit STRC/SATA; multi-miner diversification is intentional.\n";
frame_prompt += "Return structured fields: symbol (uppercase), theme, thesis_fit from watchlist if present,\n";
frame_prompt += "sleeve_if_owned (default stocks_growth for energy/AI equities; btc_digital_credit for STRC/SATA/miners),\n";
frame_prompt += "on_watchlist, already_held, policy_notes summarizing auto_buy=false and deep_dive rules, open_questions (up to 8).\n";

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

// ========== Plan (deep-research planner) ==========
phase("Plan");

let plan_p = "";
plan_p += "You are the deep-research PLANNER for equity " + sym + ".\n";
plan_p += "Produce a bounded set of 4-5 high-value research questions that, if answered with sources,\n";
plan_p += "would decide whether this name is buy-ready for a small agentic fund (~40% BTC/digital credit, ~60% stocks).\n";
plan_p += "FRAME JSON:\n" + json_encode(framed) + "\n";
plan_p += "Each question needs: id (q1,q2,...), question (full sentence), angle (one of: market, fundamental,\n";
plan_p += "thesis_fit, risk, catalyst, peer, valuation, liquidity, governance).\n";
plan_p += "Prefer falsifiable questions over vague ones. Do NOT answer them — only plan.\n";
plan_p += "Do NOT place trades. Use tools only if you need to peek at existing research files.\n";

let plan_r = agent(plan_p, #{
    label: "plan:" + sym,
    capability_mode: "read-only",
    output_schema: plan_schema,
});

if plan_r == () || !plan_r.success || plan_r.output == () || plan_r.output.questions == () {
    pause("infra", "Plan agent failed — resume to retry.");
}

let questions = plan_r.output.questions;
// Enforce budget: at most 5 research shards
let q_use = [];
let qi = 0;
for q in questions {
    if qi < 5 {
        q_use.push(q);
    }
    qi += 1;
}
if q_use.len() == 0 {
    pause("verification", "Planner returned zero questions.");
}
if qi > 5 {
    log("Truncated plan from " + qi.to_string() + " to " + q_use.len().to_string() + " questions for agent budget");
}
log("Plan: " + q_use.len().to_string() + " research questions");

// ========== Research fan-out (claims + sources) ==========
phase("Research");

let common_ctx = "";
common_ctx += "Symbol: " + sym + "\n";
common_ctx += "Theme: " + framed.theme + "\n";
common_ctx += "Thesis fit: " + framed.thesis_fit + "\n";
common_ctx += "Sleeve if owned: " + framed.sleeve_if_owned + "\n";
common_ctx += "On watchlist: " + ("" + framed.on_watchlist) + "\n";
common_ctx += "Already held: " + ("" + framed.already_held) + "\n";
common_ctx += "Policy: " + framed.policy_notes + "\n";
common_ctx += "Book: modernized ~40% BTC/digital-credit (elevate STRC/SATA; multi-miner diversification OK)\n";
common_ctx += "+ ~60% stocks/growth; agentic account only; no day-trading mandate.\n";
common_ctx += "You MUST use web_search and/or open_page (and X search if useful) — do not answer from memory alone.\n";
common_ctx += "If Robinhood MCP tools are available, prefer live quote snippets; otherwise web.\n";
common_ctx += "Do NOT place trades. Every claim needs a source_locator (URL, filing, or citable page).\n";

let rjobs = [];
for q in q_use {
    let qid = if q.id == () { "q" } else { q.id };
    let qtext = if q.question == () { "" } else { q.question };
    let qangle = if q.angle == () { "general" } else { q.angle };
    let rp = "";
    rp += common_ctx;
    rp += "ROLE: Deep-research RESEARCHER for " + sym + " (angle=" + qangle + ").\n";
    rp += "Answer this question with structured CLAIMS, each backed by evidence you actually fetched:\n";
    rp += "QUESTION [" + qid + "]: " + qtext + "\n";
    rp += "Return claims[] each with: id (e.g. " + qid + "-c1), claim, evidence (what you found),\n";
    rp += "source_locator (URL or precise citation), confidence 0-1.\n";
    rp += "Max 4 claims for this question. Also list uncertainties[] for gaps you could not close.\n";
    rp += "Empty claims is only valid if tools failed after genuine attempts — then explain in uncertainties.\n";
    rjobs.push(#{
        prompt: rp,
        label: "research:" + qid,
        capability_mode: "read-only",
        output_schema: claims_schema,
    });
}

let research = parallel(rjobs);

// Flatten claims; track uncertainties and failed shards
let all_claims = [];
let all_uncertainties = [];
let failed_shards = 0;
let ri = 0;
for r in research {
    if r == () || !r.success || r.output == () {
        failed_shards += 1;
        all_uncertainties.push("research shard " + ri.to_string() + " failed or empty");
    } else {
        if r.output.claims != () {
            for c in r.output.claims {
                all_claims.push(c);
            }
        }
        if r.output.uncertainties != () {
            for u in r.output.uncertainties {
                all_uncertainties.push(u);
            }
        }
    }
    ri += 1;
}

if all_claims.len() == 0 {
    pause("infra", "No claims gathered — all research shards empty/failed. Resume to retry.");
}
log("Research: " + all_claims.len().to_string() + " claims; failed_shards=" + failed_shards.to_string());

// Cap claims verified for budget (verify is 1 agent per claim)
let claims_to_verify = [];
let ci = 0;
for c in all_claims {
    if ci < 12 {
        claims_to_verify.push(c);
    }
    ci += 1;
}
if ci > 12 {
    log("Truncated claims for verify from " + ci.to_string() + " to 12");
    all_uncertainties.push("claim cap: only first 12 of " + ci.to_string() + " claims verified");
}

// ========== Verify (independent adversarial shards) ==========
phase("Verify");

let vjobs = [];
for c in claims_to_verify {
    let cid = if c.id == () { "claim" } else { c.id };
    let ctext = if c.claim == () { "" } else { c.claim };
    let cev = if c.evidence == () { "" } else { c.evidence };
    let cloc = if c.source_locator == () { "" } else { c.source_locator };
    let vp = "";
    vp += "You are an independent deep-research VERIFIER (skeptic). Fail closed.\n";
    vp += "Symbol context: " + sym + "\n";
    vp += "CLAIM ID: " + cid + "\n";
    vp += "CLAIM: " + ctext + "\n";
    vp += "RESEARCHER EVIDENCE: " + cev + "\n";
    vp += "SOURCE LOCATOR: " + cloc + "\n";
    vp += "Your job: re-check with tools (web_search/open_page/X as needed). Do NOT trust the researcher.\n";
    vp += "Set survives=true ONLY if you independently inspect supporting evidence for the claim.\n";
    vp += "If source is missing, paywalled with no corroboration, or claim overreaches, survives=false.\n";
    vp += "evidence_checked must describe what YOU opened/read. reason explains the verdict.\n";
    vp += "Optional corrected_claim if the spirit is true but wording must change (still set survives carefully).\n";
    vp += "claim_id must equal " + cid + ".\n";
    vjobs.push(#{
        prompt: vp,
        label: "verify:" + cid,
        capability_mode: "read-only",
        output_schema: verify_schema,
    });
}

let verdicts = parallel(vjobs);

let survivors = [];
let dropped = [];
let vi = 0;
for v in verdicts {
    let orig = claims_to_verify[vi];
    let oid = if orig.id == () { "claim" } else { orig.id };
    if v != () && v.success && v.output != () && v.output.survives == true
        && v.output.evidence_checked != () && v.output.evidence_checked != "" {
        // attach verifier note
        let sclaim = orig.claim;
        if v.output.corrected_claim != () && v.output.corrected_claim != "" {
            sclaim = v.output.corrected_claim;
        }
        survivors.push(#{
            id: oid,
            claim: sclaim,
            evidence: orig.evidence,
            source_locator: orig.source_locator,
            confidence: orig.confidence,
            verifier_reason: v.output.reason,
            evidence_checked: v.output.evidence_checked,
        });
    } else {
        let dreason = "verifier failed or no evidence";
        if v != () && v.success && v.output != () && v.output.reason != () {
            dreason = v.output.reason;
        }
        dropped.push(#{
            id: oid,
            claim: orig.claim,
            reason: dreason,
        });
    }
    vi += 1;
}

let partial = false;
if failed_shards > 0 || dropped.len() > 0 || all_uncertainties.len() > 0 {
    partial = true;
}
log("Verify: " + survivors.len().to_string() + "/" + claims_to_verify.len().to_string()
    + " claims survived; partial=" + ("" + partial));

let survivors_json = json_encode(survivors);
let dropped_json = json_encode(dropped);
let uncert_json = json_encode(all_uncertainties);

// ========== Critique (investment fail-closed) ==========
phase("Critique");

let crit_p = "";
crit_p += "You are the Critic for fund-manager position deep-dive on " + sym + ".\n";
crit_p += "You only get VERIFIED claims (deep-research survivors). Treat dropped claims as unproven.\n";
crit_p += "Adversarially challenge size-in. Default blocks_buy=true unless verified evidence is strong.\n";
crit_p += "Frame: " + framed.policy_notes + "\n";
crit_p += "VERIFIED CLAIMS JSON:\n" + survivors_json + "\n";
crit_p += "DROPPED CLAIMS JSON:\n" + dropped_json + "\n";
crit_p += "UNCERTAINTIES JSON:\n" + uncert_json + "\n";
crit_p += "coverage_partial=" + ("" + partial) + " — if true, lean harder against ready/promoted.\n";
crit_p += "Set blocks_buy, reasons (max 8), max_size_guidance, missing_evidence.\n";
crit_p += "No trades. Fail closed on hype or thin verified evidence.\n";

let crit = agent(crit_p, #{
    label: "critic:" + sym,
    capability_mode: "read-only",
    output_schema: critique_schema,
});

let crit_out = if crit != () && crit.success { crit.output } else { () };
let crit_json = if crit_out == () {
    "{\"blocks_buy\":true,\"reasons\":[\"critic failed\"],\"max_size_guidance\":\"pass\"}"
} else {
    json_encode(crit_out)
};

// ========== Report (only survivors + investment verdict) ==========
phase("Report");

let coverage_label = if partial { "Partial" } else { "Full" };

let syn_p = "";
syn_p += "You are the synthesizer for position deep-dive on " + sym + " (deep-research style report).\n";
syn_p += "CRITICAL: Base factual claims ONLY on VERIFIED claims JSON. Do not reintroduce dropped claims as fact.\n";
syn_p += "Mark coverage as " + coverage_label + " in the report header and in coverage field (Partial|Full).\n";
syn_p += "status MUST be one of: monitor | ready | pass | held | promoted\n";
syn_p += "  monitor = stay on watchlist, no buy yet\n";
syn_p += "  ready = eligible for Thesis/Risk to propose a small size (not an order by itself)\n";
syn_p += "  pass = do not buy for now\n";
syn_p += "  held = already in book; reaffirm or trim guidance only\n";
syn_p += "  promoted = recommend adding to core allowlist (rare)\n";
syn_p += "Respect critic: if blocks_buy true, status should be monitor or pass, not ready/promoted.\n";
syn_p += "If coverage is Partial, prefer monitor/pass over ready unless survivors are decisive.\n";
syn_p += "FRAME:\n" + json_encode(framed) + "\n";
syn_p += "PLAN QUESTIONS:\n" + json_encode(q_use) + "\n";
syn_p += "VERIFIED CLAIMS:\n" + survivors_json + "\n";
syn_p += "DROPPED CLAIMS (coverage limits — list in report):\n" + dropped_json + "\n";
syn_p += "RESEARCHER UNCERTAINTIES:\n" + uncert_json + "\n";
syn_p += "CRITIC:\n" + crit_json + "\n";
syn_p += "report_markdown requirements — VERBOSE permanent artifact:\n";
syn_p += "  # " + sym + " deep dive\n";
syn_p += "  **Coverage: " + coverage_label + "** (deep-research verified claims only)\n";
syn_p += "  ## Executive findings (read this first) — 5+ numbered findings grounded in verified claims\n";
syn_p += "  ## Verified claims table (claim | source_locator | confidence)\n";
syn_p += "  ## Coverage limitations (dropped claims, failed shards, uncertainties)\n";
syn_p += "  ## Conclusions (actionable table: status, buy?, promote?, max size)\n";
syn_p += "  ## Frame / policy context\n";
syn_p += "  ## Company and product findings\n";
syn_p += "  ## Market and trading findings\n";
syn_p += "  ## Fundamental findings (with numbers when verified)\n";
syn_p += "  ## Bull case + confidence\n";
syn_p += "  ## Bear case + confidence\n";
syn_p += "  ## Critic fail-closed + promotion criteria\n";
syn_p += "  ## Comparison to core book / opportunity cost\n";
syn_p += "  ## Verdict (verbose + confidence)\n";
syn_p += "  ## Next actions by Scout/Thesis/Risk/Critic/Executor\n";
syn_p += "  ## Sources (from verified source_locators)\n";
syn_p += "  mention agentic-only book; research is not an order.\n";
syn_p += "summary/why_now/why_not: full sentences.\n";
syn_p += "coverage field: exactly Partial or Full.\n";
syn_p += "Also set watchlist_update.status and note.\n";
syn_p += "After schema output, write the report to investment/research/" + sym + "_deep_dive.md\n";
syn_p += "using the write tool under investment/research/ only.\n";

let syn = agent(syn_p, #{
    label: "report:" + sym,
    capability_mode: "read-write",
    output_schema: verdict_schema,
});

if syn == () || !syn.success || syn.output == () {
    pause("infra", "Report agent failed — verified claims may still be in journal; resume to retry.");
}

let verdict = syn.output;

let report_body = if verdict.report_markdown == () { "" } else { verdict.report_markdown };
let scratch = write_scratch_file("deep_dive_report.md", report_body);

let report_path = "investment/research/" + sym + "_deep_dive.md";

// Ensure coverage is Partial if we know partial even if model said Full
let cov = if verdict.coverage == () { coverage_label } else { "" + verdict.coverage };
if partial {
    cov = "Partial";
}

log("Verdict status=" + verdict.status + " coverage=" + cov + " for " + sym);

complete(#{
    symbol: sym,
    status: verdict.status,
    summary: verdict.summary,
    why_now: verdict.why_now,
    why_not: verdict.why_not,
    confidence: verdict.confidence,
    coverage: cov,
    claims_gathered: all_claims.len(),
    claims_verified: survivors.len(),
    claims_dropped: dropped.len(),
    research_shards_failed: failed_shards,
    next_actions: verdict.next_actions,
    watchlist_update: verdict.watchlist_update,
    report_path: report_path,
    scratch_report: scratch,
    on_watchlist: framed.on_watchlist,
    already_held: framed.already_held,
    force: force_s,
    pattern: "deep-research",
});
