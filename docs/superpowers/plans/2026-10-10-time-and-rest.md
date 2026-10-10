# Your Time, Rest and Manual Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard shows how long you yourself spent at Claude today and this week, warns about overwork, a `UserPromptSubmit` hook reminds you to take a break, and the advice block points at manual work worth automating.

**Architecture:** `scan()` collects two new interval streams in the same pass over the logs (every record → "Claude busy", your own messages → "your time") and per project-day counters (`mine`, `nudges`, `prompts`); a pure `presence_days()` merges the intervals into `history["presence"][day]`. The template renders a new "Your time and rest" card and two manual-work tips; published copies replace prompt text with a hash. `--break-check` is a tiny stateful hook mode that exits before any scanning.

**Tech Stack:** Python 3 stdlib only; vanilla JS inside `template.html`; tests are plain functions run by `python test_usage_report.py`.

**Spec:** `docs/superpowers/specs/2026-10-10-time-and-rest-design.md`

## Global Constraints

- Stdlib only. Run tests with `PYTHONUTF8=1 python test_usage_report.py` from the repo root.
- Existing `sec`, `hours`, `msgs` and project shares keep their meaning — do not change how they are counted.
- "Your message" = `type == "user"`, not `isMeta`, not a `tool_result`, not `isSidechain`, `entrypoint != "sdk-cli"`, stripped text non-empty and not starting with `<` or `[Request interrupted by user`.
- Nudge: your message, stripped length ≤ 15, not starting with `/`. Prompt key: your message, stripped length ≥ 20, not starting with `/`, `re.sub(r"\s+", " ", text.lower())[:80]`.
- Config defaults: `"break_minutes": 15`, `"remind_after_minutes": 90`. Reminder at most once per 30 min.
- Card thresholds: today > 8 h; week > 45 h; stretch > 2 h; night ≥ 30 min in the week; streak ≥ 7 days (day off = under 30 min of your time). Repeats: ≥ 3 times on ≥ 2 days, top 5. Nudges: share ≥ 10 % with `mine` ≥ 20.
- Prompt text only in local `dashboard.html` / `data.json`; artifact and design copies carry `#` + first 8 hex of sha1 of the key.
- No commits — the user commits himself. Do not touch `~/.claude/settings.json` without the user's explicit yes (Task 7).
- Twin rule (CLAUDE.md): every change is ported to `~/.claude/skills/claude-usage-report/` in this session; skill hook text is Russian; `localize(repo_template, "ru")` must equal the skill template byte for byte.

---

### Task 1: Your time, Claude busy, and the per-project counters

**Files:**
- Modify: `claude_usage_dashboard/usage_report.py` — `DEFAULT_CONFIG` (~line 61), `new_project_day` (~161), `scan` (~175-265), `merge` (~268), `trim_for_design` (~669), `build_data` (~606), `main` (~912); new helpers after `text_size`.
- Test: `test_usage_report.py`

**Interfaces:**
- Produces: `prompt_text(content) -> str`; `is_yours(record, text) -> bool`; `merge_intervals(pairs) -> list[[start, end]]`; `split_by_day(a, b)` yields `(day_str, start, end)`; `presence_days(busy, yours, cfg) -> {day: {"you", "busy", "longest", "night"}}` (float seconds); `scan(cfg, projects_dir=PROJECTS_DIR, presence=None)` fills `presence` dict if given; `merge(history, fresh, presence=None)`; project-day snapshot gains `"mine": int, "nudges": int, "prompts": {key: count}`; `build_data()` gains `"presence"` and `"break_minutes"`.

- [ ] **Step 1: Write the failing tests** — append to `test_usage_report.py` above `if __name__ == "__main__":`

```python
def say(ts, text, **extra):
    return dict({"type": "user", "timestamp": ts, "message": {"content": text}}, **extra)


def test_presence_merges_parallel_sessions_and_counts_your_time():
    """Two sessions over the same minutes: Claude busy counts them once; your time is the gaps between your own
    messages across both sessions, up to break_minutes."""
    with tempfile.TemporaryDirectory() as root:
        write_session(root, SLUG, "s1", [say("2026-09-01T12:00:00Z", "first request to the model"), asst("2026-09-01T12:05:00Z", "r1"),
                                         say("2026-09-01T12:10:00Z", "go"), asst("2026-09-01T12:10:30Z", "r2")])
        write_session(root, "c--Users-ann-code-acme-web", "s2",
                      [say("2026-09-01T12:02:00Z", "a parallel request elsewhere"), asst("2026-09-01T12:08:00Z", "r3")])
        pres = {}
        days = ur.scan(CFG, root, presence=pres)
    p = pres["2026-09-01"]
    assert p["busy"] == 630, f"12:00-12:10:30 once, not 630 + 360; got {p['busy']}"
    assert p["you"] == 600 and p["longest"] == 600 and p["night"] == 0, p
    v = days["2026-09-01"]["acme-api"]
    assert v["mine"] == 2 and v["nudges"] == 1, v
    assert v["prompts"] == {"first request to the model": 1}, v["prompts"]


def test_presence_skips_what_is_not_you_and_splits_at_midnight():
    with tempfile.TemporaryDirectory() as root:
        write_session(root, SLUG, "s1", [
            say("2026-09-01T20:50:00Z", "late request number one"),                       # 23:50 local
            say("2026-09-01T20:55:00Z", "[Request interrupted by user]"),
            say("2026-09-01T20:56:00Z", "<command-name>/clear</command-name>"),
            say("2026-09-01T20:57:00Z", "subagent prompt text here", isSidechain=True),
            say("2026-09-01T20:58:00Z", "scripted prompt from claude -p", entrypoint="sdk-cli"),
            say("2026-09-01T21:05:00Z", "  Late   REQUEST number one  "),                 # 00:05, 15 min on: one stretch
            say("2026-09-01T21:40:00Z", "after a long break"),                            # 35 min on: a break
        ])
        pres = {}
        days = ur.scan(CFG, root, presence=pres)
    assert pres["2026-09-01"] == {"you": 600, "busy": 600, "longest": 900, "night": 0}, pres["2026-09-01"]
    assert pres["2026-09-02"] == {"you": 300, "busy": 300, "longest": 0, "night": 300}, pres["2026-09-02"]
    a, b = days["2026-09-01"]["acme-api"], days["2026-09-02"]["acme-api"]
    assert (a["mine"], b["mine"]) == (1, 2), "interruptions, command tags, subagents and claude -p are not yours"
    assert a["prompts"] == b["prompts"] == {"late request number one": 1}, (a["prompts"], b["prompts"])
    assert a["nudges"] == b["nudges"] == 0


def test_merge_keeps_fuller_presence():
    h = ur.merge({"days": {}}, {}, {"2026-09-01": {"you": 100.4, "busy": 500, "longest": 100, "night": 0}})
    h = ur.merge(h, {}, {"2026-09-01": {"you": 50, "busy": 200, "longest": 50, "night": 0}})
    assert h["presence"]["2026-09-01"] == {"you": 100, "busy": 500, "longest": 100, "night": 0}, h["presence"]
    assert "presence" not in ur.merge({"days": {}}, {}), "no presence passed, none written"
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONUTF8=1 python test_usage_report.py`
Expected: FAIL in `test_presence_merges_parallel_sessions_and_counts_your_time` with `TypeError: scan() got an unexpected keyword argument 'presence'`.

- [ ] **Step 3: Implement**

In `DEFAULT_CONFIG`, right after `"idle_minutes": 10,`:

```python
    # Your time: a pause between your own messages longer than this is a break; the --break-check hook
    # reminds you after remind_after_minutes without one.
    "break_minutes": 15,
    "remind_after_minutes": 90,
```

Add `import hashlib` to the imports (alphabetical, after `import glob`) — used in Task 2; adding it now keeps the import block edited once.

`new_project_day`:

```python
def new_project_day():
    return {"sec": 0.0, "hours": [0.0] * 24, "msgs": 0, "sessions": set(), "ins": new_ins(),
            "mine": 0, "nudges": 0, "prompts": defaultdict(int),
            "models": defaultdict(lambda: dict.fromkeys(("calls",) + TOKEN_FIELDS, 0))}
```

New helpers after `text_size`:

```python
def prompt_text(content):
    """Text of a user message: the string itself, or its text blocks joined."""
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return content if isinstance(content, str) else ""


def is_yours(r, text):
    """A message you typed: not a subagent's prompt, not a claude -p run, not an interruption or a command tag."""
    return bool(text) and not r.get("isSidechain") and r.get("entrypoint") != "sdk-cli" \
        and not text.startswith(("<", "[Request interrupted by user"))


def merge_intervals(pairs):
    """(start, end) pairs -> disjoint [start, end] runs, sorted; touching runs join."""
    out = []
    for a, b in sorted(pairs):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def split_by_day(a, b):
    """(start, end) -> (day, start, end) pieces cut at local midnight."""
    while a.date() < b.date():
        mid = datetime.combine(a.date() + timedelta(days=1), datetime.min.time(), tzinfo=a.tzinfo)
        yield a.strftime("%Y-%m-%d"), a, mid
        a = mid
    yield a.strftime("%Y-%m-%d"), a, b


def presence_days(busy, yours, cfg):
    """Per day: you = gaps between your own messages up to break_minutes, all sessions merged; busy = any session
    running, parallel ones counted once; longest = your longest stretch without a break (to the day it started);
    night = your time at 00-06."""
    out = defaultdict(lambda: {"you": 0.0, "busy": 0.0, "longest": 0.0, "night": 0.0})
    for a, b in merge_intervals(busy):
        for d, x, y in split_by_day(a, b):
            out[d]["busy"] += (y - x).total_seconds()
    yours = sorted(yours)
    brk = cfg["break_minutes"] * 60
    for a, b in merge_intervals([(a, b) for a, b in zip(yours, yours[1:]) if (b - a).total_seconds() <= brk]):
        start = out[a.strftime("%Y-%m-%d")]
        start["longest"] = max(start["longest"], (b - a).total_seconds())
        for d, x, y in split_by_day(a, b):
            out[d]["you"] += (y - x).total_seconds()
            dawn = x.replace(hour=6, minute=0, second=0, microsecond=0)
            out[d]["night"] += max(0.0, (min(y, dawn) - x).total_seconds())
    return dict(out)
```

`scan` — signature and docstring:

```python
def scan(cfg, projects_dir=PROJECTS_DIR, presence=None):
    """day -> project -> {sec, hours[24], msgs, sessions, models}. One API call = one requestId.
    A dict passed as presence is filled with presence_days() over every project."""
    days = defaultdict(lambda: defaultdict(new_project_day))
    idle = timedelta(minutes=cfg["idle_minutes"])
    busy, yours = [], []        # every session's active gaps; the moments of your own messages
```

In the user branch, replace the `days[d][name]["msgs"] += 1` line's block:

```python
                        elif not r.get("isMeta") and not (isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)):
                            days[d][name]["msgs"] += 1
                            t = prompt_text(c).strip()
                            if is_yours(r, t):
                                yours.append(dt)
                                pdv = days[d][name]
                                pdv["mine"] += 1
                                if not t.startswith("/") and len(t) <= 15:
                                    pdv["nudges"] += 1
                                elif not t.startswith("/") and len(t) >= 20:
                                    pdv["prompts"][re.sub(r"\s+", " ", t.lower())[:80]] += 1
```

In the per-session gap loop, record the gap:

```python
                if g <= idle.total_seconds():
                    pdv = days[a.strftime("%Y-%m-%d")][name]
                    pdv["sec"] += g
                    pdv["hours"][a.hour] += g
                    busy.append((a, b))
```

Before `return days`:

```python
    if presence is not None:
        presence.update(presence_days(busy, yours, cfg))
    return days
```

`merge` — signature, docstring line, snapshot fields, presence block:

```python
def merge(history, fresh, presence=None):
    """Merge a scan into the history: for each (day, project) keep the fuller snapshot. Logs only
    grow while they live and vanish whole when pruned, so "more calls" means "more complete".
    Presence per day follows the same rule by its busy seconds."""
```

```python
            snap = {"sec": int(v["sec"]), "hours": [int(h) for h in v["hours"]], "msgs": v["msgs"],
                    "mine": v["mine"], "nudges": v["nudges"], "prompts": dict(v["prompts"]),
                    "sessions": sorted(v["sessions"]), "models": models, "ins": ins}
```

```python
    if presence:
        pres = history.setdefault("presence", {})
        for d, p in presence.items():
            snap = {k: int(x) for k, x in p.items()}
            if d not in pres or snap["busy"] >= pres[d]["busy"]:
                pres[d] = snap
    return history
```

`build_data` — add next to `"idle_minutes"`:

```python
        "presence": history.get("presence", {}),
        "idle_minutes": cfg["idle_minutes"],
        "break_minutes": cfg["break_minutes"],
```

`trim_for_design` — before `return d`:

```python
    d["presence"] = {k: v for k, v in data.get("presence", {}).items() if k >= first}
```

`main` — replace the history line:

```python
    pres = {}
    history = merge(load_json(HISTORY_PATH, {"days": {}}), scan(cfg, presence=pres), pres)
```

- [ ] **Step 4: Run tests**

Run: `PYTHONUTF8=1 python test_usage_report.py`
Expected: every line `ok test_…`, including the three new ones.

---

### Task 2: Prompt text stays on the local page

**Files:**
- Modify: `claude_usage_dashboard/usage_report.py` — new `hide_prompts` before `render`; `render` (~740)
- Test: `test_usage_report.py`

**Interfaces:**
- Consumes: project-day `"prompts"` from Task 1.
- Produces: `hide_prompts(data) -> data` (prompt keys → `"#" + sha1(key)[:8]`); the template recognises hidden keys by `/^#[0-9a-f]{8}$/`.

- [ ] **Step 1: Write the failing test**

Add `import hashlib` to the test file imports, then:

```python
def test_prompt_text_stays_on_the_local_page():
    """Your words go to dashboard.html only; the artifact and the design sample get a hash per request, so the
    repeat counts still add up."""
    text = "deploy the staging build please"
    data = {"refresh_url": None, "today": "2026-09-20", "commits": {}, "projects": {"acme-api": "core"}, "weeks": [],
            "billing_months": [], "presence": {},
            "days": [{"d": "2026-09-20", "p": {"acme-api": {"sec": 1, "prompts": {text: 4}}}}]}
    old = ur.OUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ur.set_out_dir(tmp)
            local, shared = (open(p, encoding="utf8").read() for p in ur.render(data)[0])
            design = open(os.path.join(tmp, "dashboard.design.html"), encoding="utf8").read()
        finally:
            ur.set_out_dir(old)
    key = "#" + hashlib.sha1(text.encode("utf8")).hexdigest()[:8]
    assert text in local
    for page in (shared, design):
        assert text not in page and f'"{key}": 4' in page
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONUTF8=1 python test_usage_report.py`
Expected: FAIL in `test_prompt_text_stays_on_the_local_page` (AssertionError: the text is in the shared page).

- [ ] **Step 3: Implement**

Before `render`:

```python
def hide_prompts(data):
    """The published copies keep the repeat counts but not your words: each request becomes #<8 hex of its sha1>."""
    key = lambda s: "#" + hashlib.sha1(s.encode("utf8")).hexdigest()[:8]
    return dict(data, days=[{"d": day["d"], "p": {n: dict(v, prompts={key(k): c for k, c in (v.get("prompts") or {}).items()})
                                                  for n, v in day["p"].items()}} for day in data["days"]])
```

In `render`, the shared and design copies:

```python
    shared = body.replace("/*__DATA__*/null", json.dumps(dict(hide_prompts(data), refresh_url=None), ensure_ascii=False).replace("</", "<\\/"))  # a published page cannot reach this machine, nor carry your words
```

```python
        f.write(full.replace(payload, json.dumps(trim_for_design(hide_prompts(data)), ensure_ascii=False).replace("</", "<\\/")))
```

- [ ] **Step 4: Run tests**

Run: `PYTHONUTF8=1 python test_usage_report.py`
Expected: all `ok`.

---

### Task 3: `--break-check` hook mode

**Files:**
- Modify: `claude_usage_dashboard/usage_report.py` — new `break_check` before `main`; `main` argparse + early exit
- Test: `test_usage_report.py`

**Interfaces:**
- Consumes: `cfg["break_minutes"]`, `cfg["remind_after_minutes"]` (Task 1), `load_json`/`save_json`, `OUT_DIR`.
- Produces: `break_check(cfg, now) -> str | None`; CLI `--break-check` prints `{"systemMessage": "..."}` or nothing, exit 0, no scanning.

- [ ] **Step 1: Write the failing tests**

Add to test imports: `import contextlib`, `import io`, and change `from datetime import date` to `from datetime import date, datetime, timedelta, timezone`. Then:

```python
def test_break_check_reminds_after_a_long_stretch():
    """Prompts every 10 min: a reminder at 90 min, again no sooner than 30 min later; a 20-min pause starts afresh;
    a broken state file is not an error."""
    cfg = dict(CFG, break_minutes=15, remind_after_minutes=90)
    old = ur.OUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ur.set_out_dir(tmp)
            seen = {hm: ur.break_check(cfg, ur.parse_local("2026-09-01 " + hm, cfg)) for hm in
                    ["09:00", "09:10", "09:20", "09:30", "09:40", "09:50", "10:00", "10:10", "10:20", "10:30", "10:40", "10:50", "11:00", "11:20"]}
            with open(os.path.join(tmp, "break.json"), "w") as f:
                f.write("{")
            broken = ur.break_check(cfg, ur.parse_local("2026-09-01 12:00", cfg))
        finally:
            ur.set_out_dir(old)
    assert [hm for hm, m in seen.items() if m] == ["10:30", "11:00"], seen
    assert "1 h 30 min" in seen["10:30"] and "2 h 00 min" in seen["11:00"], seen
    assert broken is None


def test_break_check_flag_prints_a_hook_message():
    old = ur.OUT_DIR
    buf = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ur.set_out_dir(tmp)
            t = datetime.now(timezone.utc)
            ur.save_json(os.path.join(tmp, "break.json"), {"start": (t - timedelta(hours=2)).isoformat(), "last": (t - timedelta(minutes=5)).isoformat()})
            with contextlib.redirect_stdout(buf):
                ur.main(["--break-check", "--out-dir", tmp])
        finally:
            ur.set_out_dir(old)
    out = json.loads(buf.getvalue())
    assert "without a break" in out["systemMessage"], out
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONUTF8=1 python test_usage_report.py`
Expected: FAIL with `AttributeError: module ... has no attribute 'break_check'`.

- [ ] **Step 3: Implement**

Before `main`:

```python
def break_check(cfg, now):
    """UserPromptSubmit hook: the stretch of your prompts without a break, across all sessions (break.json in the
    data folder). Past remind_after_minutes returns a reminder, at most once every 30 min; a broken state file
    simply starts a new stretch."""
    path = os.path.join(OUT_DIR, "break.json")
    try:
        st = {k: datetime.fromisoformat(v) for k, v in load_json(path, {}).items() if v}
    except (ValueError, TypeError, AttributeError):
        st = {}
    if "start" not in st or "last" not in st or (now - st["last"]).total_seconds() > cfg["break_minutes"] * 60:
        st["start"] = now
    st["last"] = now
    run = int((now - st["start"]).total_seconds()) // 60
    msg = None
    if run >= cfg["remind_after_minutes"] and ("nudged" not in st or (now - st["nudged"]).total_seconds() >= 30 * 60):
        msg = f"You've been at it for {run // 60} h {run % 60:02d} min without a break - stand up for 10 minutes."
        st["nudged"] = now
    save_json(path, {k: v.isoformat() for k, v in st.items()})
    return msg
```

In `main`, add the argument after `--lang`:

```python
    ap.add_argument("--break-check", action="store_true", help="UserPromptSubmit hook: remind to take a break after remind_after_minutes without one; nothing else runs")
```

and right after `now = datetime.now(tz(cfg))`:

```python
    if args.break_check:
        msg = break_check(cfg, now)
        if msg:
            print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
        return
```

- [ ] **Step 4: Run tests**

Run: `PYTHONUTF8=1 python test_usage_report.py`
Expected: all `ok`.

---

### Task 4: The card, the manual-work tips, the method note, Russian strings

**Files:**
- Modify: `claude_usage_dashboard/template.html` — CSS after `.advice li .how{…}` (~329); card HTML first in `#board` (~357); formatting helpers (~426-432); new `rest()` before `// ---------- readout row ----------`; manual-work block in `insights()` before `const right = document.createElement('ol');` (~1219); `method()` (~1285); `render()` (~1320)
- Modify: `claude_usage_dashboard/i18n/ru.json`

**Interfaces:**
- Consumes: `DATA.presence`, `DATA.break_minutes`, project-day `mine`/`nudges`/`prompts` (Tasks 1-2).

- [ ] **Step 1: CSS** — after the `.advice li .how{color:var(--ink-2)}` line:

```css
.rest-figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:16px 22px;align-content:start}
.rest-figs div{display:flex;flex-direction:column;gap:4px}
.rest-figs b{font-family:var(--mono);font-size:22px;font-weight:600;letter-spacing:-.02em;color:var(--ink)}
.rest-figs span{font-size:12px;color:var(--muted)}
```

- [ ] **Step 2: Card HTML** — first child of `<section class="board" id="board" …>`:

```html
    <div class="card" id="k-rest" data-span="12">
      <header><h2>Your time and rest</h2><span class="note">today and this week, whatever the period above</span></header>
      <div class="advice" id="c-rest"></div>
    </div>

```

- [ ] **Step 3: Helpers** — after the `parseAt` line in `// ---------- formatting ----------`:

```js
  const fmtHM = (sec) => { const m = Math.round(sec / 60); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`; };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
```

- [ ] **Step 4: `rest()`** — before `// ---------- readout row ----------`:

```js
  // ---------- your time and rest ----------
  // DATA.presence[day]: you = gaps between your own messages up to break_minutes, all sessions merged; busy = any
  // session running, parallel ones counted once; longest = your longest stretch without a break; night = you at 00-06
  function rest() {
    const day = (d) => (DATA.presence || {})[d] || { you: 0, busy: 0, longest: 0, night: 0 };
    const fold = (from, to, k, f) => { let s = 0; for (let d = from; d <= to; d = addDays(d, 1)) s = f(s, day(d)[k]); return s; };
    const sum = (from, to, k) => fold(from, to, k, (a, b) => a + b), most = (from, to, k) => fold(from, to, k, Math.max);
    const today = DATA.today, mon = addDays(today, -((new Date(today + 'T00:00:00').getDay() + 6) % 7)), t = day(today);
    const week = sum(mon, today, 'you'), long = most(mon, today, 'longest'), night = sum(mon, today, 'night');
    const past = [1, 2, 3, 4].map((i) => sum(addDays(mon, -7 * i), addDays(mon, 6 - 7 * i), 'you')).filter((s) => s > 0);
    const avg = past.length ? past.reduce((a, b) => a + b, 0) / past.length : null;
    let streak = 0;   // a day off is a day with under 30 min of you; a young today does not break the run
    for (let d = t.you >= 1800 ? today : addDays(today, -1); day(d).you >= 1800; d = addDays(d, -1)) streak++;
    const figs = [
      [fmtH(t.you), `you today · Claude busy ${fmtH(t.busy)}`],
      [fmtH(week), 'you this week' + (avg != null ? ` · ${fmtH(avg)} a week on average before` : '')],
      [fmtHM(t.longest), `longest stretch without a break today; this week: ${fmtHM(long)}`],
      [fmtH(night), 'at night (00–06) this week'],
      [ru(streak), 'days in a row without a day off'],
    ];
    const tips = [];
    if (t.you > 8 * 3600) tips.push(['warn', `${fmtH(t.you)} at Claude today`, 'A long day. Wrap up: what is left will keep until tomorrow, and tired prompts cost more rework than they save.']);
    if (week > 45 * 3600) tips.push(['warn', `Week at Claude: ${fmtH(week)}`, 'Heavier than a full working week. Plan a lighter day or a day without Claude.']);
    if (long > 2 * 3600) tips.push(['warn', `${fmtHM(long)} in one go without a break`, `Stand up every 60–90 minutes: a pause of ${DATA.break_minutes} min or more counts as a break here.`]);
    if (night >= 1800) tips.push(['warn', `${fmtH(night)} at night this week`, 'Night work shifts sleep and the next day with it. Move it into the day where you can.']);
    if (streak >= 7) tips.push(['warn', `${streak} days in a row without a day off`, 'Take a day without Claude: a day off counts here as under 30 minutes at it.']);
    if (!tips.length) tips.push(['ok', 'Your pace is fine', 'No long days, no marathons without a break, no night work this week.']);
    $('c-rest').innerHTML = `<div class="rest-figs">${figs.map(([v, k]) => `<div><b>${v}</b><span>${k}</span></div>`).join('')}</div>` +
      `<ol>${tips.map(([kind, head, how]) => `<li><span class="tag ${kind}">${kind === 'warn' ? 'watch' : kind === 'ok' ? 'fine' : 'fact'}</span><div><b>${head}</b><br><span class="how">${how}</span></div></li>`).join('')}</ol>`;
  }

```

- [ ] **Step 5: Manual-work tips** — in `insights()`, immediately before `const right = document.createElement('ol');`:

```js
    // manual work: requests you keep typing (3+ times on 2+ days) and short nudges that walk Claude by the hand
    const rep = {}; let mine = 0, nudges = 0;
    for (const day of A.days) for (const [name, v] of Object.entries(day.p)) {
      mine += v.mine || 0; nudges += v.nudges || 0;
      for (const [k, n] of Object.entries(v.prompts || {})) {
        const R = rep[k] || (rep[k] = { n: 0, days: new Set(), projects: new Set() });
        R.n += n; R.days.add(day.d); R.projects.add(name);
      }
    }
    const repeats = Object.entries(rep).filter(([, R]) => R.n >= 3 && R.days.size >= 2).sort((a, b) => b[1].n - a[1].n);
    if (repeats.length) tips.push(['warn', `Requests you keep typing by hand: ${ru(repeats.length)}`,
      repeats.slice(0, 5).map(([k, R]) => `<span class="mono">${/^#[0-9a-f]{8}$/.test(k) ? `${k} (text hidden in the published copy)` : esc(k)}</span> · ${ru(R.n)}× · days with it: ${R.days.size} · ${[...R.projects].map(esc).join(', ')}`).join('<br>') +
      '<br>Turn a request you repeat into a skill or a slash command: one word instead of a paragraph, and the same result every time.']);
    if (mine >= 20 && pct(nudges, mine) >= 10) tips.push(['warn', `${P(nudges, mine)} of your messages are short nudges (${ru(nudges)} / ${ru(mine)})`,
      '“yes”, “go”, “continue”: Claude stops and waits for you. Hand it the whole task with a plan up front, switch on auto mode, allow the commands it keeps asking about (<b>/fewer-permission-prompts</b>), and use <b>/loop</b> for checks you repeat.']);
```

- [ ] **Step 6: Method note** — in `method()`, a new paragraph right after the `How this is counted.` paragraph:

```
<p><b>Your time.</b> Counted from your own messages across all sessions at once: the gaps between them no longer than ${DATA.break_minutes} min; a longer pause is a break. Reading the last answer before a break is not counted, so the figure runs a little low. “Claude busy” is the time at least one session was running, parallel sessions counted once; the active time above sums sessions and runs higher. Subagents, <span class="mono">claude -p</span> runs and interruptions are not your messages.</p>
```

- [ ] **Step 7: Call it** — in `render()`: `header(A); weekInstrument(); stats(A); rest(); portrait(A); …`

- [ ] **Step 8: Russian strings** — add to `i18n/ru.json` (keep its formatting: one entry per line, 1-space indent). Each key is an exact substring of the template; longest-first replacement means these whole phrases win over shorter generic entries:

```json
 "<h2>Your time and rest</h2>": "<h2>Твоё время и отдых</h2>",
 "today and this week, whatever the period above": "сегодня и эта неделя, независимо от периода выше",
 "you today · Claude busy ": "ты сегодня · Claude в работе ",
 "you this week": "ты на этой неделе",
 " a week on average before": " в неделю в среднем раньше",
 "longest stretch without a break today; this week: ": "самый долгий отрезок без перерыва сегодня; за неделю: ",
 "at night (00–06) this week": "ночью (00–06) на этой неделе",
 "days in a row without a day off": "дней подряд без выходного",
 " at Claude today": " за Claude сегодня",
 "A long day. Wrap up: what is left will keep until tomorrow, and tired prompts cost more rework than they save.": "Длинный день. Закругляйся: остальное дождётся завтра, а запросы на усталую голову дают больше переделок, чем экономят.",
 "Week at Claude: ": "Неделя за Claude: ",
 "Heavier than a full working week. Plan a lighter day or a day without Claude.": "Тяжелее полной рабочей недели. Запланируй лёгкий день или день без Claude.",
 " in one go without a break": " подряд без перерыва",
 "Stand up every 60–90 minutes: a pause of ": "Вставай каждые 60–90 минут: перерывом здесь считается пауза от ",
 " min or more counts as a break here.": " мин.",
 " at night this week": " ночью на этой неделе",
 "Night work shifts sleep and the next day with it. Move it into the day where you can.": "Ночная работа сдвигает сон, а с ним и следующий день. Где можно, переноси её на день.",
 "Take a day without Claude: a day off counts here as under 30 minutes at it.": "Возьми день без Claude: выходным здесь считается день, где за ним меньше 30 минут.",
 "Your pace is fine": "Режим в норме",
 "No long days, no marathons without a break, no night work this week.": "На этой неделе нет длинных дней, марафонов без перерыва и ночной работы.",
 "Requests you keep typing by hand: ": "Запросы, которые ты набираешь вручную снова и снова: ",
 " (text hidden in the published copy)": " (текст скрыт в опубликованной копии)",
 "× · days with it: ": "× · дней с ним: ",
 "<br>Turn a request you repeat into a skill or a slash command: one word instead of a paragraph, and the same result every time.": "<br>Сделай из повторяющегося запроса скилл или слэш-команду: одно слово вместо абзаца и каждый раз одинаковый результат.",
 " of your messages are short nudges (": " твоих сообщений — короткие подталкивания (",
 "“yes”, “go”, “continue”: Claude stops and waits for you. Hand it the whole task with a plan up front, switch on auto mode, allow the commands it keeps asking about (<b>/fewer-permission-prompts</b>), and use <b>/loop</b> for checks you repeat.": "«да», «go», «продолжай»: Claude останавливается и ждёт тебя. Отдай ему задачу целиком с планом заранее, включи режим auto, разреши команды, о которых он спрашивает снова и снова (<b>/fewer-permission-prompts</b>), а для повторяющихся проверок — <b>/loop</b>.",
 "<p><b>Your time.</b> Counted from your own messages across all sessions at once: the gaps between them no longer than ": "<p><b>Твоё время.</b> Считается по твоим собственным сообщениям во всех сессиях сразу: промежутки между ними не длиннее ",
 " min; a longer pause is a break. Reading the last answer before a break is not counted, so the figure runs a little low. “Claude busy” is the time at least one session was running, parallel sessions counted once; the active time above sums sessions and runs higher. Subagents, <span class=\"mono\">claude -p</span> runs and interruptions are not your messages.</p>": " мин; пауза длиннее — перерыв. Чтение последнего ответа перед перерывом не считается, так что цифра немного занижена. «Claude в работе» — время, когда работала хотя бы одна сессия, параллельные считаются один раз; активное время выше суммирует сессии и выходит больше. Субагенты, запуски <span class=\"mono\">claude -p</span> и прерывания — не твои сообщения.</p>",
```

- [ ] **Step 9: Verify the page renders in both languages** (real logs, scratch data folder — never the shared `~/.claude/usage-report/`)

```bash
S="$TEMP/claude/c--Users-gllex--DEV-PROJECTS-2026-claude-code-usage-dashboard/7a8bb6a0-9efc-4d1f-b497-0b917a8b9cf7/scratchpad"
mkdir -p "$S/out" && cp ~/.claude/usage-report/config.json ~/.claude/usage-report/history.json "$S/out/"
PYTHONUTF8=1 python -m claude_usage_dashboard --out-dir "$S/out" --no-fetch --lang en   # run in background: the scan takes minutes
```

Expected: no `Untranslated:` line beyond what HEAD already prints. Then:

```bash
CH="/c/Program Files/Google/Chrome/Application/chrome.exe"
"$CH" --headless=new --disable-gpu --dump-dom "file:///$S/out/dashboard.html" > "$S/dom.html"
grep -c 'class="rest-figs"' "$S/dom.html"; grep -c '<tbody>' "$S/dom.html"; grep -o 'Requests you keep typing by hand: [0-9]*' "$S/dom.html"
"$CH" --headless=new --disable-gpu --window-size=1400,1600 --screenshot="$S/rest.png" "file:///$S/out/dashboard.html"
```

Expected: `rest-figs` 1 and the projects table rendered (an exception in `rest()` would leave the later cards empty). Look at `rest.png`. Then rebuild with `--lang ru` and grep the dumped DOM's `c-rest` and the advice block for leftover English phrases from Step 8; there must be none.

- [ ] **Step 10: Template sync check is expected to fail here** (the skill template is updated in Task 6) — do not "fix" it now.

---

### Task 5: Docs

**Files:**
- Modify: `docs/data-contract.md`, `README.md`, `README.ru.md`, `SKILL.md`

- [ ] **Step 1: `docs/data-contract.md`** — read it; in the same style add: top-level `"presence": {"2026-09-20": {"you": 25200, "busy": 41000, "longest": 6300, "night": 1800}}` with one comment per field (definitions from the spec), `"break_minutes": 15` next to `idle_minutes`, and in the project-day object `"mine"`, `"nudges"`, `"prompts": {"normalized start of a request": 4}` — noting that the artifact and design copies carry `#<8 hex of sha1>` keys instead of text. In the "Deriving your own numbers" part add one sentence: for time at Claude use `presence.you`, not the sum of `sec` (parallel sessions).

- [ ] **Step 2: READMEs** — read both; in the feature list add one bullet each (EN / RU) about the "Your time and rest" card, the manual-work tips and the `--break-check` hook; if there is a CLI flags list, add `--break-check`.

- [ ] **Step 3: `SKILL.md`** — in "Settings (`config.json`)" add `break_minutes` / `remind_after_minutes`. Add a section after "Running it unattended":

````markdown
## Break reminder (hook)

`--break-check` is a `UserPromptSubmit` hook: it notes each prompt in `break.json` in the data folder, shared by all
sessions, and once you have gone `remind_after_minutes` (90) without a pause longer than `break_minutes` (15) it
shows a reminder, at most every 30 min. It never blocks a prompt and does not scan the logs. In `~/.claude/settings.json`:

```json
{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "claude-usage-dashboard --break-check"}]}]}}
```
````

---

### Task 6: Port to the skill twin

**Files:**
- Modify: `~/.claude/skills/claude-usage-report/usage_report.py`, `test_usage_report.py`, `template.html`, `SKILL.md`, `data-contract.md`

- [ ] **Step 1: Re-read before editing** — `git -C ~/.claude/skills status --short claude-usage-report` and `git -C ~/.claude/skills log -3 --oneline -- claude-usage-report`; read the current files. Other sessions edit them; never write from a stale copy.

- [ ] **Step 2: `usage_report.py`** — apply Tasks 1-3 code changes verbatim in logic, in the skill's own language for comments/docstrings/help strings (match its existing Russian style), personal defaults untouched. The reminder text in Russian: `f"Ты работаешь уже {run // 60} ч {run % 60:02d} мин без перерыва - встань минут на 10."`. No `localize`/`--lang` (the skill has none); its `render()` returns `paths`, so `hide_prompts` wiring is the same but no tuple.

- [ ] **Step 3: `test_usage_report.py`** — add the six new tests adapted to the skill's fixtures (its own `CFG`, `SLUG` and second slug in place of `acme-web`; `ur.render(data)` returns paths directly, so drop the `[0]`; the break-check assertions check the Russian text: `"1 ч 30 мин"`, `"2 ч 00 мин"`, `"без перерыва"`).

- [ ] **Step 4: `template.html`** — write `localize(repo_template, "ru")[0]` to the skill's `template.html` (UTF-8, `\n` newlines, matching the current file's line endings — check with `file` first).

- [ ] **Step 5: `SKILL.md` and `data-contract.md`** — the same facts as Task 5 in Russian. In the skill's SKILL.md the snippet uses this machine's command: `python C:/Users/gllex/.claude/skills/claude-usage-report/usage_report.py --break-check` (confirm `python` resolves in Git Bash with `which python`).

- [ ] **Step 6: Verify both sides**

```bash
cd /c/Users/gllex/_DEV_PROJECTS_2026/claude-code-usage-dashboard && PYTHONUTF8=1 python test_usage_report.py
cd ~/.claude/skills/claude-usage-report && PYTHONUTF8=1 python test_usage_report.py
cd /c/Users/gllex/_DEV_PROJECTS_2026/claude-code-usage-dashboard && PYTHONUTF8=1 python -c "from claude_usage_dashboard import usage_report as ur; import os; r=open('claude_usage_dashboard/template.html',encoding='utf8').read(); s=open(os.path.expanduser('~/.claude/skills/claude-usage-report/template.html'),encoding='utf8',newline='').read().replace('\r\n','\n'); print('template in sync:', ur.localize(r,'ru')[0]==s)"
```

Expected: all `ok` on both sides, `template in sync: True`.

---

### Task 7: Install the hook (only after the user says yes)

- [ ] **Step 1:** Ask the user. On yes: read `~/.claude/settings.json`, add to `hooks.UserPromptSubmit` (create the key if absent, keep every existing hook) `{"hooks": [{"type": "command", "command": "python C:/Users/gllex/.claude/skills/claude-usage-report/usage_report.py --break-check"}]}`; validate the file with `python -m json.tool`.
- [ ] **Step 2:** Smoke test: `echo '{}' | python C:/Users/gllex/.claude/skills/claude-usage-report/usage_report.py --break-check; echo "exit $?"` → prints nothing (or a reminder), `exit 0`, returns in well under a second; `~/.claude/usage-report/break.json` exists.
