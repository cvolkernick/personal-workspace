# Loaded by bland_relay.py into the bland_relay module namespace (#1107).
# Do not import this file directly.
if __name__ not in ("bland_relay", "__main__"):
    raise RuntimeError("load relay_turo.py through bland_relay.py")

# ---------------------------------------------------------------------------
# Turo notifications (#1104)
#
# The same notification-relay app that posts Google Messages notifications to
# the RCS route also posts Turo app notifications (package
# com.relayrides.android.relayrides). The RCS route hands those to
# turo_handle(): normalize (prefer big_text), dedupe, append to a JSONL inbox,
# drop events suppressed by TURO_SUPPRESS_RE or an optional
# TURO_VEHICLE_ALLOW_RE, classify urgency with regexes only,
# then in the background: owner_alert SMS (urgent only, one fixed number),
# Helm webhook (every non-suppressed event) and the Alexandra forward (urgent
# only, kind "turo_urgent"). Each background task is isolated, so a webhook
# failure never blocks the owner alert.
TURO_PACKAGE = "com.relayrides.android.relayrides"
# owner_alert texts only TURO_OWNER_ALERT_TO (env, strict E.164). No number
# lives in code; unset/invalid disables owner alerts. Logs show the last 4 only.
TURO_DEDUPE_DEFAULT_S = 24 * 60 * 60
TURO_PREFIX_WINDOW_S = 10 * 60
TURO_SEEN_MAX = 2000
TURO_ALERT_TEXT_CHARS = 140
TURO_GUEST_MAX = 64
TURO_TEXT_MAX = 4000
TURO_SCHEMA = "panamerica.turo.event.v1"
# Outbound payloads carry only what the consumer needs (internal ev keeps more
# for dedupe/suppression/inbox).
TURO_HELM_FIELDS = ("kind", "schema", "event_id", "guest", "reservation_id", "vehicle", "plate",
                    "text", "received_at", "urgent", "urgent_class")
TURO_ALEX_FIELDS = ("kind", "guest", "reservation_id", "vehicle", "text", "urgent_class", "received_at")
TURO_CLASSES = ("safety", "accident_damage", "lockout", "no_start", "charging")  # priority order
TURO_URGENT_DEFAULTS = {
    "lockout": (
        r"locked? (myself )?out|lock ?out|can'?t (get in|unlock|open (the )?(car|door))|"
        r"won'?t (unlock|open)|(doors?|car) (is |are )?(still )?locked|"
        r"(key ?card|phone ?key|key ?fob|digital key|the app|app|key) (is )?(not|isn'?t|won'?t|doesn'?t|didn'?t) work|"
        r"\bno key\b|lost (the |my )?key"
    ),
    "no_start": (
        r"won'?t (start|turn on|move|go into (drive|gear))|(doesn'?t|does not|not|isn'?t|didn'?t) start(ing)?\b|"
        r"dead (battery|12 ?v)|\b12 ?v\b|no power|screen (is )?(black|dead|blank)|stuck in park|"
        r"(car|it|truck|rivian) (is )?dead\b"
    ),
    "accident_damage": (
        r"accident|crash(ed)?\b|collision|(got|was|been|i) hit\b|hit (a|by|another|me)\b|rear.?ended|"
        r"fender|damage(d)?\b|\bdents?\b|\bdented\b|scratch|broken (window|glass|mirror|windshield)|"
        r"cracked (window|glass|windshield)|police|\btow(ed|ing| truck)?\b|flat tire|blown tire|airbags?\b"
    ),
    "charging": (
        r"won'?t charge|not charging|can'?t charge|doesn'?t charge|"
        r"charg(er|ing) (fail|error|issue|problem|broken|not work)|supercharger|"
        r"(battery|range|charge) (is )?(very |really |super )?(low|at \d)|\b\d{1,2} ?% (left|battery|charge)|"
        r"ran out of (charge|battery|range)|out of (charge|battery)|stranded"
    ),
    "safety": (
        r"emergency|\b911\b|unsafe|danger|\bsmoke\b|smoking (from|under|out)|\bfire\b|on fire|burning|"
        r"injur|\bhurt\b|ambulance|hospital|\bhelp!|\burgent\b|\basap\b|stuck on (the )?(road|highway|freeway|interstate)"
    ),
}
TURO_URGENT_NEG_DEFAULT = (
    r"before (i |we )?(return|drop.?off|pick.?up)|already (there|on file|documented|noted|in the photos)|"
    r"pre.?existing|instructions?|no smoking|smoking (allowed|policy)|non.?smok|"
    r"(thanks|thank you) (so much )?for (the|your|all the) help|"
    r"\bno (damage|scratch(es)?|dents?|issues?|problems?)\b|in case of (an )?(emergency|accident)"
)
TURO_SUPPRESS_DEFAULT = r"safe ?wheels"
TURO_TRIP_CTX_RE = re.compile(
    r"(?:trip|reservation|booking|res)\b\s*(?:#|id|no\.?|number|num)?\s*[:#]?\s*(\d{7,9})(?!\d)", re.IGNORECASE)
TURO_TRIP_BARE_RE = re.compile(r"(?<![\d$.,:/-])(\d{7,9})(?![\d.,:/-]?\d)")
TURO_PLATES = {"24EWUH": "Toyota Corolla 24EWUH", "25EWUH": "Toyota Corolla 25EWUH"}
TURO_VEHICLE_RE = re.compile(
    r"\b(?:(?:19|20)\d{2}\s+)?(?:"
    r"rivian(?:\s+r1[st])?|r1[st]|toyota(?:\s+\w+)?|corolla|tesla(?:\s+model\s*[3sxy])?|model\s*[3sxy]\b|"
    r"honda(?:\s+\w+)?|nissan(?:\s+\w+)?|hyundai(?:\s+\w+)?|kia(?:\s+\w+)?|ford(?:\s+\w+)?|"
    r"chevy(?:\s+\w+)?|chevrolet(?:\s+\w+)?|bmw(?:\s+\w+)?|mercedes(?:-benz)?(?:\s+\w+)?|"
    r"jeep(?:\s+\w+)?|subaru(?:\s+\w+)?|mazda(?:\s+\w+)?|volkswagen(?:\s+\w+)?|vw\s+\w+|audi(?:\s+\w+)?|"
    r"lexus(?:\s+\w+)?|polestar(?:\s+\d)?|lucid(?:\s+air)?)\b", re.IGNORECASE)
TURO_TITLE_SPLIT_RE = re.compile(r"\s+(?:•|·|\||–|—|-)\s+")
TURO_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
TURO_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TURO_SENTENCE_RE = re.compile(r"(?<=[.!?\n])\s+|\n+")
# Cross-source dedupe (#1142). A Turo app guest-message notification reads
# "About <Vehicle> from <Guest>:\n<body>"; the Gmail copy carries the same
# guest, vehicle and body. xseen entries only ever match the other source.
TURO_ABOUT_RE = re.compile(r"\AAbout (?P<v>[^\n]{1,80}?) from (?P<g>[^:\n]{1,64}):[ \t]*\n(?P<b>.+)\Z", re.DOTALL)
# A shorter body only matches a longer one from the other source when it is a
# prefix of it AND either ends with a truncation marker or is >= 60% as long.
TURO_XPREFIX_RATIO = 0.6
TURO_XPREFIX_MIN = 8
TURO_XKEEP = 400
TURO_TRUNC_RE = re.compile(r"\s*(?:…|\.{3})\s*\Z")
TURO_EMAIL_PATH = "/turo/email"
TURO_EMAIL_MAX_BODY = 64 * 1024
TURO_EMAIL_SRC = "gmail"

_turo_lock = threading.Lock()
_turo_prune = {"at": 0.0}
_turo_regex_cache = {}


def _turo_int(name, default, lo, hi):
    return _int_env(name, default, lo, hi)


def turo_disabled():
    return env("TURO_HOOK_DISABLED") == "1"


def _turo_state_path():
    return _state_dir() / "turo_state.json"


def turo_inbox_path():
    override = env("TURO_INBOX_PATH")
    return Path(override) if override else _state_dir() / "turo_inbox.jsonl"


def _turo_compile(name, default):
    """Env override (case-insensitive) or the code default. A bad override logs and falls back."""
    raw = env(name) or default
    hit = _turo_regex_cache.get((name, raw))
    if hit is not None:
        return hit
    try:
        rx = re.compile(raw, re.IGNORECASE) if raw else None
    except re.error:
        log.warning("%s is not a valid regex; using the default", name)
        rx = re.compile(default, re.IGNORECASE) if default else None
    _turo_regex_cache[(name, raw)] = rx
    return rx


def turo_urgent_patterns():
    return {c: _turo_compile("TURO_URGENT_RE_" + c.upper(), TURO_URGENT_DEFAULTS[c]) for c in TURO_CLASSES}


def classify_turo(text):
    """-> (class or None, matched term or None). Regex only. A sentence matching
    TURO_URGENT_NEG is skipped, so "the scratch was already there" is not urgent
    but "already there. I just got rear-ended" still is."""
    if not text:
        return None, None
    neg = _turo_compile("TURO_URGENT_NEG", TURO_URGENT_NEG_DEFAULT)
    sentences = [s for s in TURO_SENTENCE_RE.split(str(text)) if s and s.strip()]
    kept = [s for s in sentences if not (neg and neg.search(s))]
    pats = turo_urgent_patterns()
    for cls in TURO_CLASSES:
        rx = pats.get(cls)
        if rx is None:
            continue
        for s in kept:
            m = rx.search(s)
            if m:
                return cls, m.group(0)[:40]
    return None, None


def turo_suppressed(*fields):
    rx = _turo_compile("TURO_SUPPRESS_RE", TURO_SUPPRESS_DEFAULT)
    if rx is None:
        return False
    hay = " \n".join(str(f) for f in fields if f)
    return bool(hay and rx.search(hay))


def turo_vehicle_allow_rx():
    """Compiled TURO_VEHICLE_ALLOW_RE, or None when unset, blank, or invalid.

    An invalid pattern is the same as unset: log and do not filter.
    """
    raw = env("TURO_VEHICLE_ALLOW_RE")
    if not raw:
        return None
    key = ("TURO_VEHICLE_ALLOW_RE", raw)
    if key in _turo_regex_cache:
        return _turo_regex_cache[key]
    try:
        rx = re.compile(raw, re.IGNORECASE)
    except re.error:
        log.warning("TURO_VEHICLE_ALLOW_RE is not a valid regex; vehicle allowlist is off")
        rx = None
    _turo_regex_cache[key] = rx
    return rx


def turo_vehicle_allow_suppressed(ev):
    """True when the optional allowlist is set and the parsed vehicle misses it.

    No parsed vehicle passes through, so Helm can match the guest to a trip.
    """
    rx = turo_vehicle_allow_rx()
    if rx is None:
        return False
    vehicle = ev.get("vehicle") if isinstance(ev, dict) else None
    if not vehicle:
        return False
    return rx.search(str(vehicle)) is None


def turo_is_suppressed(ev, *fields):
    """TURO_SUPPRESS_RE, then the optional vehicle allowlist. Either one drops the event."""
    return turo_suppressed(*fields) or turo_vehicle_allow_suppressed(ev)


def parse_turo_reservation(*fields):
    """7-9 digit trip number. Labeled ("Trip #12345678") anywhere first, then a
    bare number in the order given (title, sub_text, text). None if absent."""
    for f in fields:
        if f:
            m = TURO_TRIP_CTX_RE.search(str(f))
            if m:
                return m.group(1)
    for f in fields:
        if f:
            m = TURO_TRIP_BARE_RE.search(str(f))
            if m:
                return m.group(1)
    return None


def parse_turo_vehicle(*fields):
    """-> (vehicle or None, plate or None). Our plates map to a canonical name."""
    hay = " \n".join(str(f) for f in fields if f)
    if not hay:
        return None, None
    up = re.sub(r"[\s-]", "", hay.upper())
    for plate, name in TURO_PLATES.items():
        if plate in up:
            return name, plate
    m = TURO_VEHICLE_RE.search(hay)
    if not m:
        return None, None
    v = " ".join(m.group(0).split())
    low = v.lower()
    if re.search(r"rivian|r1s|r1t", low):
        return ("Rivian R1T" if "r1t" in low else "Rivian R1S"), None
    if "corolla" in low:
        return "Toyota Corolla", None
    return v[:40], None


def _turo_guest(title):
    """Title minus a " • vehicle" style suffix. -> (guest or None, suffix or None)."""
    t = " ".join(str(title or "").split())
    if not t or _placeholder(t):
        return None, None
    parts = TURO_TITLE_SPLIT_RE.split(t, maxsplit=1)
    guest = parts[0].strip()[:TURO_GUEST_MAX] or None
    rest = parts[1].strip() if len(parts) > 1 else None
    return guest, rest


def _turo_posted_iso(posted):
    """postedAt (epoch ms or s) -> UTC ISO; any other string passes through."""
    if posted in (None, ""):
        return None
    try:
        v = float(posted)
    except (TypeError, ValueError):
        return str(posted)[:40]
    if v > 1e12:
        v /= 1000.0
    if v <= 0:
        return None
    return _iso(v)


def turo_redact(text):
    s = TURO_EMAIL_RE.sub("[email]", str(text or ""))
    return TURO_PHONE_RE.sub("[phone]", s)


def _turo_field(raw, keys):
    v = _first(raw, keys) if isinstance(raw, dict) else None
    if v is None or _placeholder(v):
        return None
    s = str(v).strip()
    return s or None


def turo_event(text, title=None, sub_text=None, category=None, nkey=None, posted_at=None, now=None):
    """Shared Turo normalizer for every source (#1104, #1142). -> event dict, or None without text."""
    now = time.time() if now is None else now
    if not text:
        return None
    text = text[:TURO_TEXT_MAX]
    guest, title_rest = _turo_guest(title)
    vehicle, plate = parse_turo_vehicle(title_rest, sub_text, text)
    cls, term = classify_turo(text)
    tkey = _text_key(text)
    eid = hashlib.sha256(f"{nkey or ''}|{tkey}".encode("utf-8")).hexdigest()[:16]
    return {
        "kind": "turo_event",
        "schema": TURO_SCHEMA,
        "src": "turo",
        "event_id": eid,
        "guest": guest,
        "reservation_id": parse_turo_reservation(title, sub_text, text),
        "vehicle": vehicle,
        "plate": plate,
        "text": text,
        "sub_text": sub_text,
        "category": category,
        "posted_at": _turo_posted_iso(posted_at),
        "received_at": _iso(now),
        "key": nkey,
        "urgent": cls is not None,
        "urgent_class": cls,
        "matched_term": term,
        "line": LINE_904,
    }


def normalize_turo(parsed, now=None):
    """Notification -> Turo event dict, or None when there is no text."""
    raw = parsed.get("raw") if isinstance(parsed.get("raw"), dict) else {}
    text = _turo_field(raw, ("big_text", "bigtext")) or _turo_field(raw, ("text", "content", "message", "body"))
    if not text:
        return None
    return turo_event(
        text,
        title=_turo_field(raw, ("title", "sender", "conversation", "name", "contact")),
        sub_text=_turo_field(raw, ("sub_text", "subtext", "summary_text")),
        category=_turo_field(raw, ("category",)),
        nkey=_turo_field(raw, ("key", "notification_key", "id")),
        posted_at=parsed.get("posted_at"),
        now=now,
    )


def _turo_dup_locked(st, ev, now):
    """Caller holds _turo_lock. True when ev repeats a recent event; else records it."""
    window = _turo_int("TURO_DEDUPE_WINDOW_S", TURO_DEDUPE_DEFAULT_S, 60, 7 * 24 * 3600)
    tkey = _text_key(ev["text"])
    h = hashlib.sha256(f"{ev.get('key') or ''}|{tkey}".encode("utf-8")).hexdigest()
    th = hashlib.sha256(tkey.encode("utf-8")).hexdigest()
    gkey = (ev.get("guest") or "").casefold()
    seen = [r for r in (st.get("seen") or []) if isinstance(r, dict) and now - float(r.get("at") or 0) <= window]
    for r in seen:
        if r.get("h") == h:
            st["seen"] = seen
            return True
        if r.get("g") == gkey and r.get("th") == th:
            st["seen"] = seen
            return True
        # A shorter copy of a text already seen from this guest (re-post of a
        # truncated notification). A longer text is new content and passes.
        if (r.get("g") == gkey and now - float(r.get("at") or 0) <= TURO_PREFIX_WINDOW_S
                and len(tkey) >= 8 and str(r.get("p") or "").startswith(tkey[:200])
                and len(tkey) < int(r.get("n") or 0)):
            st["seen"] = seen
            return True
    seen.append({"at": now, "h": h, "th": th, "g": gkey, "p": tkey[:200], "n": len(tkey)})
    st["seen"] = seen[-TURO_SEEN_MAX:]
    return False


def _turo_first_name(name):
    parts = str(name or "").split()
    return parts[0].casefold() if parts else ""


def _turo_key_reservation(nkey):
    """Trip number inside a Turo notification key ("0|pkg|<trip>|null|uid"), else None."""
    for part in str(nkey or "").split("|"):
        if re.fullmatch(r"\d{7,9}", part):
            return part
    return None


def turo_xinfo(ev):
    """Cross-source identity of a guest message (#1142), or None for other events.

    -> {"g": first name, "v": vehicle, "r": reservation or "", "p": first 400 chars of
    text_key(body) without a trailing "…"/"...", "n": its full length, "h": its hash,
    "tr": True when a truncation marker was stripped}. Gmail events carry the fields
    directly; relay events parse "About V from G:\n body" (single-message
    notifications only; stacked/summary notifications don't cross-dedupe).
    """
    if ev.get("src") == TURO_EMAIL_SRC:
        guest, vehicle, res, body = ev.get("guest"), ev.get("vehicle"), ev.get("reservation_id"), ev.get("text")
    else:
        m = TURO_ABOUT_RE.match(str(ev.get("text") or "").strip())
        if not m:
            return None
        guest, body = m.group("g"), m.group("b")
        vehicle = parse_turo_vehicle(m.group("v"))[0]
        res = ev.get("reservation_id") or _turo_key_reservation(ev.get("key"))
    g, t = _turo_first_name(guest), _text_key(body)
    if not g or not t:
        return None
    trunc = bool(TURO_TRUNC_RE.search(t))
    if trunc:
        t = TURO_TRUNC_RE.sub("", t)
        if not t:
            return None
    return {"g": g, "v": (vehicle or "").casefold(), "r": str(res or ""), "p": t[:TURO_XKEEP], "n": len(t),
            "h": hashlib.sha256(t.encode("utf-8")).hexdigest()[:32], "tr": trunc}


def _turo_xmatch(a, b):
    """Same guest, compatible vehicle/trip, and the same body: identical, or the
    shorter one is a prefix of the longer and (marked truncated or >= 60% long)."""
    if a.get("g") != b.get("g"):
        return False
    if a.get("v") and b.get("v") and a["v"] != b["v"]:
        return False
    if a.get("r") and b.get("r") and a["r"] != b["r"]:
        return False
    if a.get("h") and a.get("h") == b.get("h"):
        return True
    na, nb = int(a.get("n") or 0), int(b.get("n") or 0)
    short, long_ = (a, b) if na < nb else (b, a)
    ns, nl = min(na, nb), max(na, nb)
    if ns < TURO_XPREFIX_MIN or ns == nl:
        return False
    if ns > TURO_XKEEP and not short.get("tr"):
        return False  # only 400 chars are kept: an unmarked long prefix can't be verified
    ps, pl = str(short.get("p") or ""), str(long_.get("p") or "")
    k = min(ns, TURO_XKEEP)
    if len(ps) < k or len(pl) < k or ps[:k] != pl[:k]:
        return False
    return bool(short.get("tr")) or ns >= TURO_XPREFIX_RATIO * nl


def _turo_xdup_locked(st, ev, now):
    """Caller holds _turo_lock. -> the other source's record when ev already
    arrived from it (relay vs Gmail); that record is consumed so it can match
    only once. Else records ev and returns None. An entry never matches its
    own source, so single-source behavior is unchanged."""
    xi = turo_xinfo(ev)
    if xi is None:
        return None
    window = _turo_int("TURO_XDEDUPE_WINDOW_S",
                       _turo_int("TURO_DEDUPE_WINDOW_S", TURO_DEDUPE_DEFAULT_S, 60, 7 * 24 * 3600),
                       60, 7 * 24 * 3600)
    src = ev.get("src") or "turo"
    xseen = [r for r in (st.get("xseen") or [])
             if isinstance(r, dict) and now - float(r.get("at") or 0) <= window]
    for i, r in enumerate(xseen):
        if r.get("src") != src and _turo_xmatch(xi, r):
            st["xseen"] = xseen[:i] + xseen[i + 1:]
            return r
    xseen.append(dict(xi, at=now, src=src))
    st["xseen"] = xseen[-TURO_SEEN_MAX:]
    return None


def _turo_claim_locked(st, ev, now):
    """-> "new", "duplicate" or "cross_duplicate". Records ev when new."""
    if _turo_dup_locked(st, ev, now):
        return "duplicate"
    first = _turo_xdup_locked(st, ev, now)
    if first is not None:
        log.info("turo cross-source duplicate event=%s src=%s first_src=%s age_s=%d", ev.get("event_id"),
                 ev.get("src"), first.get("src"), int(now - float(first.get("at") or now)))
        return "cross_duplicate"
    return "new"


def turo_claim(ev, now=None):
    """Atomically record ev. False when it is a duplicate (same or other source)."""
    now = time.time() if now is None else now
    with _turo_lock:
        st = _load_json(_turo_state_path())
        verdict = _turo_claim_locked(st, ev, now)
        try:
            _save_json(_turo_state_path(), st)
        except OSError as e:
            log.error("turo state write failed: %s", type(e).__name__)
    return verdict == "new"


def turo_peek(ev, now=None):
    """Shadow mode: the claim verdict without writing state."""
    now = time.time() if now is None else now
    with _turo_lock:
        st = json.loads(json.dumps(_load_json(_turo_state_path())))
        return _turo_claim_locked(st, ev, now)


def turo_prune_inbox(now=None, force=False):
    """Drop inbox lines older than TURO_RETENTION_DAYS (default 30). Never raises."""
    now = time.time() if now is None else now
    if not force and now - _turo_prune["at"] < 24 * 3600:
        return 0
    _turo_prune["at"] = now
    days = _turo_int("TURO_RETENTION_DAYS", 30, 1, 3650)
    cutoff = _iso(now - days * 24 * 3600)
    p = turo_inbox_path()
    try:
        with _turo_lock:
            if not p.exists():
                return 0
            keep, dropped = [], 0
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    at = str(json.loads(line).get("received_at") or "")
                except (ValueError, AttributeError):
                    at = ""
                if at and at < cutoff:
                    dropped += 1
                else:
                    keep.append(line)
            if dropped:
                tmp = p.with_suffix(".tmp")
                tmp.write_text("".join(x + "\n" for x in keep), encoding="utf-8")
                os.chmod(tmp, 0o600)
                os.replace(tmp, p)
            return dropped
    except OSError as e:
        log.error("turo inbox prune failed: %s", type(e).__name__)
        return 0


def turo_append_inbox(ev, suppressed, now=None):
    """JSONL inbox line (0600). Phone numbers and emails in text are redacted."""
    row = dict(ev)
    row["text"] = turo_redact(ev.get("text"))
    row["sub_text"] = turo_redact(ev.get("sub_text")) if ev.get("sub_text") else ev.get("sub_text")
    row["suppressed"] = bool(suppressed)
    try:
        with _turo_lock:
            _append_jsonl(turo_inbox_path(), row)
    except OSError as e:
        log.error("turo inbox write failed: %s", type(e).__name__)
    turo_prune_inbox(now)


def turo_owner_alert_to():
    """-> (strict E.164 owner number, None) or (None, reason). Never logs the number."""
    raw = env("TURO_OWNER_ALERT_TO")
    if not raw:
        return None, "owner_unset"
    if not valid_e164(raw):
        return None, "owner_invalid"
    return raw, None


def owner_alert_message(ev):
    text = " ".join(str(ev.get("text") or "").split())[:TURO_ALERT_TEXT_CHARS]
    guest = ev.get("guest") or "guest"
    return f"URGENT Turo [{ev.get('urgent_class')}]: {guest}: {text}"


def owner_alert(ev, now=None):
    """Templated 904 SMS to TURO_OWNER_ALERT_TO (the only recipient), urgent events only.

    Skips the 72-hour inbound window only for that exact env number (it is the
    sole recipient). Unset or non-E.164 TURO_OWNER_ALERT_TO disables alerts.
    Respects SMS_SEND_DISABLED, TURO_OWNER_ALERT_DISABLED, STOP, alert dedupe,
    TURO_ALERT_GUEST_MAX per TURO_ALERT_GUEST_WINDOW_S, TURO_ALERT_HOUR_MAX and the
    relay-wide RECIPIENT_MAX/GLOBAL_MAX. Returns "sent" or the skip reason.
    """
    now = time.time() if now is None else now
    if not ev.get("urgent") or not ev.get("urgent_class"):
        return "not_urgent"
    if env("TURO_OWNER_ALERT_DISABLED") == "1":
        log.warning("turo owner alert skipped: TURO_OWNER_ALERT_DISABLED=1")
        return "alert_disabled"
    to, why = turo_owner_alert_to()
    if to is None:
        if why == "owner_unset":
            log.warning("turo owner alert disabled: TURO_OWNER_ALERT_TO unset")
        else:
            log.warning("turo owner alert disabled: TURO_OWNER_ALERT_TO is not valid E.164 (+1NXXNXXXXXX)")
        return why
    if env("SMS_SEND_DISABLED") == "1":
        log.warning("turo owner alert skipped: SMS_SEND_DISABLED=1")
        return "send_disabled"
    if not gateway_configured():
        log.error("turo owner alert skipped: gateway not configured")
        return "gateway_unconfigured"
    guest_w = _turo_int("TURO_ALERT_GUEST_WINDOW_S", 600, 0, 24 * 3600)
    guest_max = _turo_int("TURO_ALERT_GUEST_MAX", 1, 1, 100)
    hour_max = _turo_int("TURO_ALERT_HOUR_MAX", 6, 1, 100)
    dedupe_w = _turo_int("TURO_DEDUPE_WINDOW_S", TURO_DEDUPE_DEFAULT_S, 60, 7 * 24 * 3600)
    gkey = (ev.get("guest") or "").casefold()
    th = hashlib.sha256(_text_key(ev.get("text")).encode("utf-8")).hexdigest()
    with _sms_lock:
        st = _load_json(_sms_state_path())
        inbound = st.get("inbound") if isinstance(st.get("inbound"), dict) else {}
        if (inbound.get(to) or {}).get("opt_out"):
            log.warning("turo owner alert skipped: owner opted out (STOP)")
            return "opt_out"
        keep_s = max(dedupe_w, GLOBAL_WINDOW_S, guest_w)
        alerts = [a for a in (st.get("turo_alerts") or [])
                  if isinstance(a, dict) and now - float(a.get("at") or 0) < keep_s]
        reason = None
        if any(a.get("g") == gkey and a.get("th") == th and now - float(a["at"]) < dedupe_w for a in alerts):
            reason = "duplicate"
        elif sum(1 for a in alerts if a.get("g") == gkey and now - float(a["at"]) < guest_w) >= guest_max:
            reason = "rate_limited_guest"
        elif sum(1 for a in alerts if now - float(a["at"]) < GLOBAL_WINDOW_S) >= hour_max:
            reason = "rate_limited_hour"
        sends = [{"to": r.get("to"), "at": float(r.get("at") or 0)} for r in (st.get("sends") or [])
                 if isinstance(r, dict) and now - float(r.get("at") or 0) < GLOBAL_WINDOW_S]
        if reason is None:
            per = sum(1 for r in sends if r.get("to") == to and now - r["at"] < RECIPIENT_WINDOW_S)
            if per >= RECIPIENT_MAX or len(sends) >= GLOBAL_MAX:
                reason = "rate_limited"
        if reason:
            log.warning("turo owner alert skipped: %s class=%s", reason, ev.get("urgent_class"))
            return reason
        # Reserve before the gateway call, like reserve_send_ex: a failed call
        # still consumes the slot so retries cannot multiply SMS.
        alerts.append({"at": now, "g": gkey, "th": th})
        sends.append({"to": to, "at": now})
        st["turo_alerts"] = alerts
        st["sends"] = sends
        try:
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)
            return "state_write_failed"
    msg = owner_alert_message(ev)
    result, err = gateway_send(to, msg)
    if result is None:
        log.error("turo owner alert gateway failed to=***%s error=%s", _tail4(to), _scrub(err))
        alert_failure("turo-owner-alert", err, 1, "Turo owner alert SMS failed")
        return "gateway_failed"
    log.info("turo owner alert sent to=***%s class=%s len=%d id=%s", _tail4(to), ev.get("urgent_class"),
             len(msg), result.get("id") or "-")
    return "sent"


def _turo_timeout():
    return float(_turo_int("TURO_WEBHOOK_TIMEOUT_S", 5, 1, 15))


def helm_auth_header():
    """-> (header name, value) or None. Default mirrors ALEXANDRA_ALERT_AUTH:
    "Authorization: Bearer <key>" (a key that already has a scheme is sent as is).
    HELM_TURO_WEBHOOK_HEADER picks another header (value = the bare key)."""
    key = env("HELM_TURO_WEBHOOK_KEY")
    if not key:
        return None
    name = env("HELM_TURO_WEBHOOK_HEADER") or "Authorization"
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name):
        log.warning("HELM_TURO_WEBHOOK_HEADER invalid; using Authorization")
        name = "Authorization"
    if name.lower() == "authorization" and " " not in key:
        return name, "Bearer " + key
    return name, key


def post_json(url, obj, headers=None, timeout=5.0, tries=2):
    """-> (status or None, error, attempts). Short timeouts; never raises."""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "User-Agent": "bland-relay/1"}
    hdrs.update(headers or {})
    last = ""
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if 200 <= r.status < 300:
                    return r.status, "", attempt
                last = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__
        if attempt < tries:
            time.sleep(1)
    return None, last, tries


def helm_push(ev):
    """POST one non-suppressed event to HELM_TURO_WEBHOOK_URL. Unset URL = JSONL only."""
    url = env("HELM_TURO_WEBHOOK_URL")
    if not url:
        return "unset"
    hdr = helm_auth_header()
    try:
        payload = {k: ev.get(k) for k in TURO_HELM_FIELDS}
        status, err, attempts = post_json(url, payload, dict([hdr]) if hdr else None, _turo_timeout())
    except Exception as e:  # noqa: BLE001
        status, err, attempts = None, type(e).__name__, 1
    if status is not None:
        log.info("turo helm push ok event=%s urgent=%s -> %d", ev.get("event_id"), ev.get("urgent"), status)
        return "ok"
    log.error("turo helm push failed event=%s error=%s", ev.get("event_id"), _scrub(err))
    alert_failure("turo-helm-webhook", err, attempts, "Turo Helm webhook failed")
    return "failed"


def alexandra_turo_push(ev):
    """Urgent events to Alexandra through the existing forward()/ALEXANDRA_ALERT_URL."""
    if not ev.get("urgent"):
        return "not_urgent"
    if env("TURO_ALEX_PUSH_DISABLED") == "1":
        return "disabled"
    if not env("ALEXANDRA_ALERT_URL"):
        return "unset"
    obj = {k: ev.get(k) for k in TURO_ALEX_FIELDS}
    obj["kind"] = "turo_urgent"
    try:
        status, err, attempts = forward(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                                        timeout=_turo_timeout())
    except Exception as e:  # noqa: BLE001
        status, err, attempts = None, type(e).__name__, 1
    if status is not None:
        log.info("turo alexandra push ok event=%s -> %d", ev.get("event_id"), status)
        return "ok"
    log.error("turo alexandra push failed event=%s error=%s", ev.get("event_id"), _scrub(err))
    alert_failure("turo-alexandra-push", err, attempts, "Turo urgent push to Alexandra failed")
    return "failed"


def _turo_safe(fn, *args):
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001 - one task must never take down another
        log.error("turo %s crashed: %s", getattr(fn, "__name__", "task"), type(e).__name__)


def turo_ingest(ev, now, suppress_fields, shadow=False):
    """Shared tail for every Turo source (#1142): dedupe, suppress, inbox, then
    owner alert + Alexandra (urgent only) and Helm in the background.
    -> (http status, response body). shadow=True logs the verdict and does nothing."""
    if shadow:
        verdict = turo_peek(ev, now)
        sup = turo_is_suppressed(ev, *suppress_fields)
        log.info("turo shadow src=%s event=%s verdict=%s suppressed=%s urgent=%s class=%s",
                 ev.get("src"), ev["event_id"], verdict, sup, ev["urgent"], ev["urgent_class"] or "-")
        return 200, {"ok": True, "turo": "shadow", "verdict": verdict, "suppressed": sup}
    if not turo_claim(ev, now):
        log.info("turo duplicate event=%s", ev["event_id"])
        return 200, {"ok": True, "turo": "duplicate"}
    if turo_is_suppressed(ev, *suppress_fields):
        turo_append_inbox(ev, True, now)
        log.info("turo suppressed (not our fleet) event=%s", ev["event_id"])
        return 200, {"ok": True, "turo": "suppressed"}
    turo_append_inbox(ev, False, now)
    log.info("turo event=%s urgent=%s class=%s len=%d trip=%s vehicle=%s",
             ev["event_id"], ev["urgent"], ev["urgent_class"] or "-", len(ev["text"]),
             bool(ev["reservation_id"]), bool(ev["vehicle"]))
    if ev["urgent"]:
        _spawn(_turo_safe, (owner_alert, ev, now))
        _spawn(_turo_safe, (alexandra_turo_push, ev))
    _spawn(_turo_safe, (helm_push, ev))
    return 200, {"ok": True, "turo": "accepted", "urgent": ev["urgent"]}


def turo_handle(parsed, now=None):
    """-> (http status, response body). Never texts for non-urgent events."""
    now = time.time() if now is None else now
    if turo_disabled():
        log.info("turo notification ignored (TURO_HOOK_DISABLED=1)")
        return 200, {"ok": True, "ignored": "turo_disabled"}
    if parsed.get("summary"):
        return 200, {"ok": True, "ignored": "empty"}
    ev = normalize_turo(parsed, now)
    if ev is None:
        log.info("turo notification ignored (empty text)")
        return 200, {"ok": True, "ignored": "empty"}
    raw = parsed.get("raw") if isinstance(parsed.get("raw"), dict) else {}
    title = _turo_field(raw, ("title",))
    return turo_ingest(ev, now, (title, ev.get("sub_text"), ev.get("vehicle"), ev.get("text")))


# ---------------------------------------------------------------------------
# Turo guest-message emails (#1142)
#
# gmail_turo_watch.py (Gmail users.watch -> Pub/Sub streaming pull) parses
# Turo guest-message emails with turo_email.py and POSTs the fields here over
# a loopback-only listener. The event goes through turo_event() and
# turo_ingest(), the same code as the notification path, so suppress rules,
# urgent classes, owner alert, Helm and dedupe are shared, and turo_state.json
# keeps a single writer (this process).
def turo_email_disabled():
    return turo_disabled() or env("TURO_EMAIL_DISABLED") == "1"


def turo_email_event(obj, now=None):
    """Parsed email fields -> Turo event (src "gmail"), or None without text."""
    now = time.time() if now is None else now
    if not isinstance(obj, dict):
        return None
    text = str(obj.get("text") or "").strip()
    guest = " ".join(str(obj.get("guest") or "").split())[:TURO_GUEST_MAX] or None
    if not text or not guest:
        return None
    res = str(obj.get("reservation_id") or "").strip()
    res = res if re.fullmatch(r"\d{7,9}", res) else None
    mid = re.sub(r"[^A-Za-z0-9]", "", str(obj.get("message_id") or ""))[:32]
    ev = turo_event(text, title=guest, category="email", nkey=f"gmail|{res or mid}",
                    posted_at=obj.get("internal_ms"), now=now)
    ev["src"] = TURO_EMAIL_SRC
    ev["line"] = None
    vehicle, plate = parse_turo_vehicle(obj.get("vehicle"))
    if vehicle:
        ev["vehicle"], ev["plate"] = vehicle, plate
    if res:
        ev["reservation_id"] = res
    return ev


def turo_email_handle(obj, now=None):
    """-> (http status, response body). Same ingest path as turo_handle()."""
    now = time.time() if now is None else now
    if turo_email_disabled():
        log.info("turo email ignored (disabled)")
        return 200, {"ok": True, "ignored": "turo_disabled"}
    ev = turo_email_event(obj, now)
    if ev is None:
        log.info("turo email ignored (empty)")
        return 200, {"ok": True, "ignored": "empty"}
    fields = (obj.get("subject"), obj.get("vehicle"), ev.get("vehicle"), ev.get("text"))
    return turo_ingest(ev, now, fields, shadow=env("TURO_EMAIL_SHADOW") == "1")


