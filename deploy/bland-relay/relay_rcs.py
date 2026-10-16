# Loaded by bland_relay.py into the bland_relay module namespace (#1107).
# Do not import this file directly.
if __name__ not in ("bland_relay", "__main__"):
    raise RuntimeError("load relay_rcs.py through bland_relay.py")

def _text_key(text):
    """Whitespace- and case-insensitive body key for cross-path dedupe."""
    return " ".join(str(text or "").split()).casefold()


def _same_sender(a_num, a_name, b_num, b_name):
    if a_num and b_num:
        return a_num == b_num
    if not a_num and not b_num:
        return bool(a_name) and a_name == b_name
    # One side only has an unmapped contact name: the body decides.
    return True


def _recent_find(num, name, tkey, srcs, now=None):
    """Matching entry from srcs within RCS_DEDUPE_S. Caller holds _recent_lock."""
    now = time.time() if now is None else now
    _recent_msgs[:] = [e for e in _recent_msgs if now - e["at"] <= RCS_DEDUPE_S][-RCS_RECENT_MAX:]
    for e in _recent_msgs:
        if e["src"] in srcs and e["tkey"] == tkey and _same_sender(num, name, e["num"], e["name"]):
            return e
    return None


def _recent_claim(src, num, name, tkey, check_srcs, now=None):
    """Atomically: None if a matching entry exists, else add and return a new one."""
    now = time.time() if now is None else now
    with _recent_lock:
        if _recent_find(num, name, tkey, check_srcs, now):
            return None
        entry = {"at": now, "src": src, "num": num, "name": name, "tkey": tkey}
        _recent_msgs.append(entry)
        return entry


def _recent_drop(entry):
    if entry is None:
        return
    with _recent_lock:
        try:
            _recent_msgs.remove(entry)
        except ValueError:
            pass


def _rcs_wait_s():
    raw = env("RCS_DEDUPE_WAIT_S")
    if not raw:
        return RCS_WAIT_DEFAULT_S
    try:
        return max(0.0, min(float(raw), 20.0))
    except ValueError:
        return RCS_WAIT_DEFAULT_S


def rcs_name_map():
    """RCS_NAME_MAP: contact name -> number. Keys match case- and space-insensitively.

    Preferred form is "Name=+1NXXNXXXXXX;Other Name=+1NXXNXXXXXX" because a
    systemd EnvironmentFile strips double quotes from values. A JSON object
    also works when it reaches the process intact.
    """
    raw = env("RCS_NAME_MAP")
    if not raw:
        return {}
    data = None
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        data = {}
        for part in raw.split(";"):
            name, sep, num = part.rpartition("=")
            if sep and name.strip():
                data[name.strip()] = num.strip()
    if not data:
        log.warning("RCS_NAME_MAP has no usable entries; ignoring")
        return {}
    out = {}
    for k, v in data.items():
        num = normalize_nanp(v)
        if num and str(k).strip():
            out[" ".join(str(k).split()).casefold()] = num
    return out


def _lenient_fields(body):
    """Pull "key": "value" pairs out of a template that broke JSON (unescaped quotes)."""
    text = body.decode("utf-8", "replace")
    out = {}
    keys = re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*"', text)
    for i, k in enumerate(keys):
        m = re.search(r'"%s"\s*:\s*"' % re.escape(k), text)
        if not m:
            continue
        rest = text[m.end():]
        nxt = None
        for k2 in keys[i + 1:]:
            m2 = re.search(r'"\s*,\s*"%s"\s*:' % re.escape(k2), rest)
            if m2:
                nxt = m2.start()
                break
        if nxt is None:
            m3 = re.search(r'"\s*}\s*$', rest)
            nxt = m3.start() if m3 else len(rest)
        out.setdefault(k, rest[:nxt])
    return out


def parse_rcs_notification(body):
    """Notification-forwarder POST -> dict(package, title, text, number, posted_at, summary).

    Accepts JSON from a template (package/title/text, plus aliases) and falls
    back to lenient key extraction when the app did not escape quotes.
    Returns None when nothing usable is present.
    """
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        data = None
    if not isinstance(data, dict):
        data = _lenient_fields(body) if body else {}
    if not data:
        return None
    pkg = _first(data, ("package", "packagename", "pkg", "app_package"))
    title = _first(data, ("title", "sender", "conversation", "name", "contact"))
    text = _first(data, ("text", "content", "message", "body", "big_text", "bigtext"))
    number = _first(data, ("number", "phone", "from_number", "address", "from"))
    posted = _first(data, ("postedat", "post_time", "posted_at", "sentstamp", "timestamp", "time"))
    summary = _first(data, ("group_summary", "groupsummary", "is_group_summary"))
    return {
        "package": None if pkg is None else str(pkg).strip(),
        "title": None if title is None else " ".join(str(title).split()),
        "text": None if text is None else str(text).strip(),
        "number": None if number is None else str(number).strip(),
        "posted_at": posted,
        "summary": summary in (True, "true", "True", "1", 1),
        "raw": data,
    }


def resolve_rcs_sender(parsed, name_map=None):
    """-> (number or None, display name or None, how). how: number|title|name_map|unknown."""
    name_map = rcs_name_map() if name_map is None else name_map
    num = normalize_nanp(parsed.get("number"))
    title = " ".join(str(parsed.get("title") or "").split())
    if num:
        return num, (title if title and not normalize_nanp(title) else None), "number"
    num = normalize_nanp(title)
    if num:
        return num, None, "title"
    name = title[:RCS_NAME_MAX] if title else None
    if name:
        mapped = name_map.get(name.casefold())
        if mapped:
            return mapped, name, "name_map"
    return None, name, "unknown"


def _placeholder(v):
    """True for an unfilled template variable like %title% or {text}."""
    s = str(v or "").strip()
    return bool(re.fullmatch(r"%[A-Za-z_]+%|\{[A-Za-z_]+\}", s))


def _spawn(target, args=()):
    """Background alert. Tests replace this so a failure does not race the response."""
    threading.Thread(target=target, args=args, daemon=True).start()


