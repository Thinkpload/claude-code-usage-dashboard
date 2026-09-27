#!/usr/bin/env python3
"""Claude Code usage report: active hours, tokens, project split, weekly limit usage.

Reads the session logs in ~/.claude/projects/**/*.jsonl, accumulates daily snapshots in
~/.claude/usage-report/history.json (so days Claude Code has already pruned survive) and
renders dashboard.html + dashboard.artifact.html.

    claude-usage-dashboard                 # rescan and build; the /usage reading is fetched with your login token
    claude-usage-dashboard --open          # ...and open it in a browser
    claude-usage-dashboard --no-fetch      # ...without touching the login token or the network
    claude-usage-dashboard --observe 20 --fable 34          # /usage reading by hand: all-models week and Fable
    claude-usage-dashboard --observe 20 --fable 34 --reset "2026-09-17 20:00" --factor 1.5 --at "2026-09-12 05:50"

From a checkout, with nothing installed: python -m claude_usage_dashboard
"""
import argparse
import fnmatch
import glob
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
OUT_DIR = os.path.join(HOME, ".claude", "usage-report")
CONFIG_PATH = os.path.join(OUT_DIR, "config.json")
HISTORY_PATH = os.path.join(OUT_DIR, "history.json")
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")
CREDENTIALS_PATH = os.path.join(HOME, ".claude", ".credentials.json")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"   # the endpoint behind Claude Code's own /usage panel

TOKEN_FIELDS = ("input", "cache_create", "cache_read", "output", "thinking")


def home_slug(home=None):
    """Claude Code names a project's log folder after its full path, with separators turned
    into dashes: C:\\Users\\ann\\code\\api -> c--Users-ann-code-api. Stripping the encoded home
    prefix turns that back into a readable project name."""
    return re.sub(r"[\\/:_]", "-", home if home is not None else HOME)


def set_out_dir(path):
    """Point the tool at a different data directory (config, history, rendered pages)."""
    global OUT_DIR, CONFIG_PATH, HISTORY_PATH
    OUT_DIR = os.path.abspath(os.path.expanduser(path))
    CONFIG_PATH = os.path.join(OUT_DIR, "config.json")
    HISTORY_PATH = os.path.join(OUT_DIR, "history.json")
    return OUT_DIR


DEFAULT_CONFIG = {
    "tz_offset_hours": 0,
    "idle_minutes": 10,
    # Dashboard language: "en" is the template itself, anything else needs i18n/<lang>.json.
    "lang": "en",
    "strip_prefixes": [home_slug() + "-", home_slug()],
    "groups": [
        {"id": "core", "label": "Main work", "match": []},
        {"id": "work", "label": "Work misc", "match": []},
        {"id": "personal", "label": "Personal", "match": ["*"]},
    ],
    "work_groups": ["core", "work"],
    "model_labels": {"claude-opus-5-5": "Opus 5.5", "claude-opus-5": "Opus 5", "claude-fable-5-1": "Fable 5.1", "claude-sonnet-5": "Sonnet 5",
                     "claude-haiku-4-5-20251001": "Haiku 4.5", "claude-opus-4-8": "Opus 4.8", "claude-opus-4-7": "Opus 4.7",
                     "claude-sonnet-4-6": "Sonnet 4.6"},
    # Token weights for the limit's "load units": API prices per 1M tokens, used as weights only.
    "token_weights": {
        "claude-fable": {"input": 10, "cache_create": 12.5, "cache_read": 0.25, "output": 50},
        "claude-opus-5-5": {"input": 4, "cache_create": 5, "cache_read": 0.2, "output": 20},
        "claude-opus": {"input": 5, "cache_create": 6.25, "cache_read": 0.5, "output": 25},
        "claude-sonnet-5": {"input": 2, "cache_create": 2.5, "cache_read": 0.2, "output": 10},
        "claude-sonnet": {"input": 3, "cache_create": 3.75, "cache_read": 0.3, "output": 15},
        "claude-haiku": {"input": 1, "cache_create": 1.25, "cache_read": 0.1, "output": 5},
        "default": {"input": 5, "cache_create": 6.25, "cache_read": 0.5, "output": 25},
    },
    "plans": {"pro": {"label": "Pro", "ratio": 1}, "max5": {"label": "Max 5x", "ratio": 5}, "max20": {"label": "Max 20x", "ratio": 20}},
    "plan_history": [{"from": "2026-01-01", "plan": "pro"}],
    # Factory guess for the Pro weekly budget in load units, replaced as soon as /usage readings
    # calibrate it. Derived from observed Pro weeks that ran into the ceiling; treat it as a
    # placeholder, not a published figure.
    "factory_weekly_units_pro": 350,
    # Weekly reset moment from /usage (local time); null = weeks start Monday 00:00.
    "week_reset": None,
    # /usage readings: {"at": "2026-09-12 05:50", "pct": 20, "fable_pct": 34, "budget_factor": 1.5}.
    # Budgets are calibrated per plan (a reading's plan is the one in force on its date).
    "observations": [],
    # Temporary boosts to the weekly budget: [{"from": "2026-09-01", "to": "2026-09-13", "factor": 1.5}]
    "boosts": [],
    "artifact_url": None,
    # Local git repos: where to look (.git no deeper than depth) and whose commits to count
    # (patterns passed to git --author).
    "git_roots": [{"path": "~/code", "depth": 2}],
    "git_authors": [],
}


def tz(cfg):
    return timezone(timedelta(hours=cfg["tz_offset_hours"]))


def parse_ts(s, cfg):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(tz(cfg))


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_PATH, {}))
    if not os.path.exists(CONFIG_PATH):
        save_json(CONFIG_PATH, cfg)
    return cfg


def project_name(slug, cfg):
    name = slug
    for p in cfg["strip_prefixes"]:
        if name.lower().startswith(p.lower()):
            name = name[len(p):]
            break
    return name.lstrip("-") or "~"


def group_of(name, cfg):
    for g in cfg["groups"]:
        if any(fnmatch.fnmatch(name.lower(), m.lower()) for m in g["match"]):
            return g["id"]
    return cfg["groups"][-1]["id"]


def new_ins():
    """Where the spend goes, in load units: context size, effort, subagents, skills, cold starts."""
    return {"units": 0.0, "ctx": {"lt50": 0.0, "50_100": 0.0, "100_150": 0.0, "gt150": 0.0},
            "effort": defaultdict(int), "side_units": 0.0, "cache_create_units": 0.0,
            "skills": defaultdict(float), "sessions": 0, "sessions_short": 0, "first_call_units": 0.0}


def new_project_day():
    return {"sec": 0.0, "hours": [0.0] * 24, "msgs": 0, "sessions": set(), "ins": new_ins(),
            "models": defaultdict(lambda: dict.fromkeys(("calls",) + TOKEN_FIELDS, 0))}


def text_size(content):
    """Rough token estimate for a tool result: characters / 4."""
    if isinstance(content, str):
        return len(content) // 4
    if isinstance(content, list):
        return sum(len(b.get("text", "")) // 4 for b in content if isinstance(b, dict))
    return 0


def scan(cfg, projects_dir=PROJECTS_DIR):
    """day -> project -> {sec, hours[24], msgs, sessions, models}. One API call = one requestId."""
    days = defaultdict(lambda: defaultdict(new_project_day))
    idle = timedelta(minutes=cfg["idle_minutes"])
    for pd in sorted(glob.glob(os.path.join(projects_dir, "*"))):
        if not os.path.isdir(pd):
            continue
        name = project_name(os.path.basename(pd), cfg)
        seen = set()
        stamps = defaultdict(list)  # session -> timestamps of every record (subagents included)
        sess = {}                   # session -> {calls, first_day, first_units}, for cold starts
        for f in glob.glob(os.path.join(pd, "**", "*.jsonl"), recursive=True):
            rel = os.path.relpath(f, pd).replace("\\", "/")
            sid = rel.split("/")[0].replace(".jsonl", "")
            pending = {}            # Skill tool-call id -> skill name
            loaded = {}             # skill -> size of its text in tokens (stays in context for the rest of the file)
            with open(f, encoding="utf8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    t = r.get("timestamp")
                    if not t:
                        continue
                    dt = parse_ts(t, cfg)
                    stamps[sid].append(dt)
                    d = dt.strftime("%Y-%m-%d")
                    typ = r.get("type")
                    if typ == "user":
                        c = r.get("message", {}).get("content")
                        if r.get("sourceToolUseID") in pending:  # the skill's text arrives as a separate meta record
                            loaded[pending.pop(r["sourceToolUseID"])] = text_size(c)
                        elif not r.get("isMeta") and not (isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)):
                            days[d][name]["msgs"] += 1
                    elif typ == "assistant":
                        m = r.get("message", {})
                        for b in m.get("content") or []:
                            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
                                pending[b.get("id")] = (b.get("input") or {}).get("skill") or "?"
                        u = m.get("usage")
                        key = r.get("requestId") or m.get("id")
                        if not u or key in seen:
                            continue
                        seen.add(key)
                        model = m.get("model", "?")
                        mm = days[d][name]["models"][model]
                        tok = {"input": u.get("input_tokens") or 0, "cache_create": u.get("cache_creation_input_tokens") or 0,
                               "cache_read": u.get("cache_read_input_tokens") or 0, "output": u.get("output_tokens") or 0,
                               "thinking": (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0}
                        mm["calls"] += 1
                        for k, v in tok.items():
                            mm[k] += v
                        # where the spend goes
                        w = weights_for(model, cfg)
                        units = units_of({model: tok}, cfg)
                        ins = days[d][name]["ins"]
                        ins["units"] += units
                        ctx = tok["input"] + tok["cache_read"] + tok["cache_create"]
                        ins["ctx"]["lt50" if ctx < 50e3 else "50_100" if ctx < 100e3 else "100_150" if ctx < 150e3 else "gt150"] += units
                        ins["effort"][r.get("effort") or "?"] += 1
                        ins["cache_create_units"] += tok["cache_create"] * w["cache_create"] / 1e6
                        if r.get("isSidechain"):
                            ins["side_units"] += units
                        for skill, size in loaded.items():  # every call re-reads the skill text from cache
                            ins["skills"][skill] += size * w["cache_read"] / 1e6
                        si = sess.setdefault(sid, {"calls": 0, "first_day": d, "first_units": units})
                        si["calls"] += 1
        for sid, st in stamps.items():
            st.sort()
            for a, b in zip(st, st[1:]):
                g = (b - a).total_seconds()
                if g <= idle.total_seconds():
                    pdv = days[a.strftime("%Y-%m-%d")][name]
                    pdv["sec"] += g
                    pdv["hours"][a.hour] += g
            for dt in {s.strftime("%Y-%m-%d") for s in st}:
                days[dt][name]["sessions"].add(sid)
        for sid, si in sess.items():
            ins = days[si["first_day"]][name]["ins"]
            ins["sessions"] += 1
            ins["sessions_short"] += si["calls"] < 3
            ins["first_call_units"] += si["first_units"]
    return days


def merge(history, fresh):
    """Merge a scan into the history: for each (day, project) keep the fuller snapshot. Logs only
    grow while they live and vanish whole when pruned, so "more calls" means "more complete"."""
    days = history.setdefault("days", {})
    for d, projects in fresh.items():
        hd = days.setdefault(d, {})
        for name, v in projects.items():
            models = {m: dict(x) for m, x in v["models"].items() if x["calls"]}
            ins = {k: (dict(x) if isinstance(x, dict) else x) for k, x in v["ins"].items()}
            for k in ("units", "side_units", "cache_create_units", "first_call_units"):
                ins[k] = round(ins[k], 3)
            ins["ctx"] = {k: round(x, 3) for k, x in ins["ctx"].items()}
            ins["skills"] = {k: round(x, 3) for k, x in ins["skills"].items() if x >= 0.001}
            snap = {"sec": int(v["sec"]), "hours": [int(h) for h in v["hours"]], "msgs": v["msgs"],
                    "sessions": sorted(v["sessions"]), "models": models, "ins": ins}
            old = hd.get(name)
            if old is None or sum(x["calls"] for x in models.values()) >= sum(x["calls"] for x in old["models"].values()):
                hd[name] = snap
    return history


def weights_for(model, cfg):
    w = cfg["token_weights"]
    for prefix in sorted((k for k in w if k != "default"), key=len, reverse=True):
        if model.startswith(prefix):
            return w[prefix]
    return w["default"]


def units_of(models, cfg):
    """Load units = tokens x weights (per 1M)."""
    total = 0.0
    for m, x in models.items():
        w = weights_for(m, cfg)
        total += sum(x.get(k, 0) * w[k] for k in ("input", "cache_create", "cache_read", "output")) / 1e6
    return total


def day_units(history, d, cfg, only=None):
    """Load units for one day; only(model) -> bool narrows the models (Fable alone, say)."""
    total = 0.0
    for p in history["days"].get(d, {}).values():
        models = {m: x for m, x in p["models"].items() if only is None or only(m)}
        total += units_of(models, cfg)
    return total


def day_hours(history, d):
    hrs = [0] * 24
    for p in history["days"].get(d, {}).values():
        for i, sec in enumerate(p.get("hours") or []):
            hrs[i] += sec
    return hrs


def parse_local(s, cfg):
    """'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' (then end of day) -> datetime in the local zone."""
    if len(s) > 10:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=tz(cfg))
    return datetime.strptime(s, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=tz(cfg))


def plan_on(date_str, cfg):
    plan = cfg["plan_history"][0]["plan"]
    for h in sorted(cfg["plan_history"], key=lambda h: h["from"]):
        if h["from"] <= date_str:
            plan = h["plan"]
    return plan


def window_start(t, cfg):
    """Start of the weekly limit window containing t: anchored on the /usage reset, else Monday 00:00."""
    if cfg.get("week_reset"):
        anchor = parse_local(cfg["week_reset"], cfg)
        k = (t - anchor) // timedelta(days=7)
        return anchor + timedelta(days=7) * k
    d = t.date() - timedelta(days=t.weekday())
    return datetime(d.year, d.month, d.day, tzinfo=tz(cfg))


def units_between(history, a, b, cfg, only=None):
    """Load units in [a, b). A partial day is split along the profile of active hours."""
    total = 0.0
    d = a.date()
    while d <= b.date():
        ds = d.strftime("%Y-%m-%d")
        day0 = datetime(d.year, d.month, d.day, tzinfo=tz(cfg))
        lo, hi = max(a, day0), min(b, day0 + timedelta(days=1))
        if hi > lo:
            du = day_units(history, ds, cfg, only)
            if lo == day0 and hi == day0 + timedelta(days=1):
                total += du
            else:
                hrs = day_hours(history, ds)
                h_lo = lo.hour if lo > day0 else 0
                h_hi = hi.hour if hi < day0 + timedelta(days=1) else 24
                part, whole = sum(hrs[h_lo:h_hi]), sum(hrs)
                total += du * (part / whole if whole else (hi - lo) / timedelta(days=1))
        d += timedelta(days=1)
    return total


FABLE = lambda m: m.startswith("claude-fable")


def boost_factor(t, cfg):
    """Budget multiplier in effect at moment t."""
    d = t.strftime("%Y-%m-%d")
    for bst in cfg.get("boosts", []):
        if bst["from"] <= d <= bst["to"]:
            return bst.get("factor", 1.0)
    return 1.0


def calibrate(history, cfg):
    """Per-plan budgets: {plan: {"all": units, "fable": units|None, "estimated": bool}}.
    A plan's own /usage readings calibrate it; otherwise it is scaled from a calibrated plan by
    ratio (an estimate); with no readings at all, the factory guess."""
    bases = defaultdict(lambda: {"all": [0.0, 0.0], "fable": [0.0, 0.0]})   # [sum of units, sum of budget shares]
    for o in cfg.get("observations", []):
        t = parse_local(o.get("at") or o["date"], cfg)
        ws = window_start(t, cfg)
        plan = plan_on(t.strftime("%Y-%m-%d"), cfg)
        factor = o.get("budget_factor") or boost_factor(t, cfg)
        used = units_between(history, ws, t, cfg)
        if o.get("pct", 0) > 0 and used > 0:
            bases[plan]["all"][0] += used
            bases[plan]["all"][1] += o["pct"] / 100.0 * factor
        used_f = units_between(history, ws, t, cfg, FABLE)
        if o.get("fable_pct", 0) > 0 and used_f > 0:
            bases[plan]["fable"][0] += used_f
            bases[plan]["fable"][1] += o["fable_pct"] / 100.0 * factor
    # a pooled ratio: early-week readings (1-3 %, rounded to whole percent) weigh by their size, not equally
    ratio = lambda acc: acc[0] / acc[1] if acc[1] else None
    out = {}
    for plan, pc in cfg["plans"].items():
        b = bases.get(plan, {"all": [0.0, 0.0], "fable": [0.0, 0.0]})
        out[plan] = {"all": ratio(b["all"]), "fable": ratio(b["fable"]), "estimated": not b["all"][1]}
    ref = next((pl for pl in out if out[pl]["all"]), None)
    for plan, v in out.items():
        if v["all"] is None:
            if ref:
                r = cfg["plans"][plan]["ratio"] / cfg["plans"][ref]["ratio"]
                v["all"] = out[ref]["all"] * r
                v["fable"] = out[ref]["fable"] * r if out[ref]["fable"] else None
            else:
                v["all"] = float(cfg["factory_weekly_units_pro"]) * cfg["plans"][plan]["ratio"]
    return out


def build_weeks(history, cfg, now):
    budgets = calibrate(history, cfg)
    days = sorted(history["days"])
    if not days:
        return [], budgets
    first = parse_local(days[0], cfg).replace(hour=0, minute=0)
    ws = window_start(first, cfg)
    weeks = []
    while ws <= now:
        we = ws + timedelta(days=7)
        upto = min(we, now)
        plan = plan_on(upto.strftime("%Y-%m-%d"), cfg)  # plan at the end of the window: a plan change applies going forward
        b = budgets[plan]
        f = boost_factor(upto, cfg)  # the factor in effect at the window's end: a limit change applies mid-week, like the plan
        used, used_f = units_between(history, ws, upto, cfg), units_between(history, ws, upto, cfg, FABLE)
        known = not b["estimated"]  # without a reading of its own we draw no percentage: scaling by ratio does not hold up
        budget = b["all"] * f if known else None
        fbudget = b["fable"] * f if known and b["fable"] else None
        # a week with a /usage reading of its own follows its latest one: the pooled budget misses limit changes
        # nobody put into boosts (a promo, a new model), and the week would drift away from what /usage shows
        for o in sorted(cfg.get("observations", []), key=lambda o: parse_local(o.get("at") or o["date"], cfg)):
            t = parse_local(o.get("at") or o["date"], cfg)
            if not ws <= t < we or plan_on(t.strftime("%Y-%m-%d"), cfg) != plan:
                continue
            u, uf = units_between(history, ws, t, cfg), units_between(history, ws, t, cfg, FABLE)
            if o.get("pct", 0) > 0 and u > 0:
                budget = u / (o["pct"] / 100.0)
            if o.get("fable_pct", 0) > 0 and uf > 0:
                fbudget = uf / (o["fable_pct"] / 100.0)
        weeks.append({"start": ws.strftime("%Y-%m-%d"), "end": (we - timedelta(minutes=1)).strftime("%Y-%m-%d"),
                      "start_at": ws.strftime("%Y-%m-%d %H:%M"), "plan": plan, "plan_label": cfg["plans"][plan]["label"],
                      "units": round(used, 2), "budget": round(budget, 2) if budget else None,
                      "pct": round(100 * used / budget, 1) if budget else None,
                      "fable_units": round(used_f, 2), "fable_budget": round(fbudget, 2) if fbudget else None,
                      "fable_pct": round(100 * used_f / fbudget, 1) if fbudget else None,
                      "boost": f, "estimated": not known, "partial": we > now})
        ws = we
    peak = max((w["units"] for w in weeks), default=0)  # drop the run of empty weeks at the start
    while weeks and weeks[0]["units"] < 0.05 * peak:
        weeks.pop(0)
    return weeks, budgets


def norm_name(s):
    """'acme-api', 'Acme API' and the slug Acme-API all collapse to one key."""
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def find_repos(cfg):
    repos = []
    for root in cfg.get("git_roots", []):
        base = os.path.expanduser(root["path"])
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, _ in os.walk(base):
            if ".git" in dirnames:
                repos.append(dirpath)
                dirnames[:] = []
                continue
            if dirpath[len(base):].count(os.sep) >= root.get("depth", 1):
                dirnames[:] = []
            else:
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"]
    return repos


def git_commits(cfg, since, project_names):
    """day -> project -> [24] of your own commits in local repos (the part that reaches GitHub)."""
    authors = cfg.get("git_authors") or []
    if not authors:
        return {}, 0
    by_norm = {norm_name(n): n for n in project_names}
    out = defaultdict(lambda: defaultdict(lambda: [0] * 24))
    repos = find_repos(cfg)
    for repo in repos:
        name = by_norm.get(norm_name(os.path.basename(repo)), os.path.basename(repo))
        args = ["git", "-C", repo, "log", "--all", "--format=%aI", f"--since={since}"] + [f"--author={a}" for a in authors]
        try:
            res = subprocess.run(args, capture_output=True, text=True, timeout=60, encoding="utf8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            continue
        for line in res.stdout.splitlines():
            try:
                dt = datetime.fromisoformat(line.strip()).astimezone(tz(cfg))
            except ValueError:
                continue
            out[dt.strftime("%Y-%m-%d")][name][dt.hour] += 1
    return {d: dict(v) for d, v in out.items()}, len(repos)


def build_data(history, cfg, now):
    today = now.date()
    weeks, budgets = build_weeks(history, cfg, now)
    plan_now = plan_on(today.strftime("%Y-%m-%d"), cfg)
    days = [{"d": d, "p": history["days"][d]} for d in sorted(history["days"])]
    names = sorted({n for d in days for n in d["p"]})
    months = sorted({d["d"][:7] for d in days}, reverse=True)
    commits, repo_count = git_commits(cfg, days[0]["d"] if days else today.strftime("%Y-%m-%d"), names)
    names = sorted(set(names) | {n for v in commits.values() for n in v})
    return {
        "generated": now.strftime("%Y-%m-%d %H:%M"),
        "today": today.strftime("%Y-%m-%d"),
        "groups": [{"id": g["id"], "label": g["label"]} for g in cfg["groups"]],
        "work_groups": cfg["work_groups"],
        "projects": {n: group_of(n, cfg) for n in names},
        "model_labels": cfg["model_labels"],
        "token_weights": cfg["token_weights"],
        "days": days,
        "months": months,
        "weeks": weeks,
        "commits": commits,
        "git": {"repos": repo_count, "authors": cfg.get("git_authors", [])},
        "limits": {"calibrated": not budgets[plan_now]["estimated"], "plan_now": cfg["plans"][plan_now]["label"],
                   "budgets": {cfg["plans"][pl]["label"]: {k: (round(v, 1) if isinstance(v, float) else v) for k, v in b.items()} for pl, b in budgets.items()},
                   "observations": cfg.get("observations", []), "week_reset": cfg.get("week_reset"),
                   "cloud_credit": cfg.get("cloud_credit")},
        "idle_minutes": cfg["idle_minutes"],
        "config_path": CONFIG_PATH,
    }


def trim_for_design(data, days=10, projects=4):
    """The same page on small data: the last N days and N projects, so a design model has something
    to look at and nothing to wade through (the full JSON is tens of kilobytes of digits)."""
    d = dict(data)
    d["days"] = data["days"][-days:]
    keep = defaultdict(float)
    for day in d["days"]:
        for name, v in day["p"].items():
            keep[name] += v["sec"]
    top = set(sorted(keep, key=keep.get, reverse=True)[:projects])
    d["days"] = [{"d": day["d"], "p": {n: v for n, v in day["p"].items() if n in top}} for day in d["days"]]
    first = d["days"][0]["d"] if d["days"] else data["today"]
    d["commits"] = {dd: {n: h for n, h in v.items() if n in top} for dd, v in data["commits"].items() if dd >= first}
    d["projects"] = {n: g for n, g in data["projects"].items() if n in top}
    d["weeks"] = [w for w in data["weeks"] if w["end"] >= first]
    d["months"] = sorted({day["d"][:7] for day in d["days"]}, reverse=True)
    return d


def adopt(path):
    """Adopt a look from Claude Design: the returned HTML -> template.html with its hooks restored."""
    with open(path, encoding="utf8") as f:
        s = f.read()
    i = s.find("const DATA = ")
    if i >= 0:  # data is inline, in any formatting: find where the JSON ends with the decoder
        j = i + len("const DATA = ")
        try:
            _, n = json.JSONDecoder().raw_decode(s[j:].lstrip())
            j += len(s[j:]) - len(s[j:].lstrip()) + n
            if s[j:j + 1] == ";":
                j += 1
            s = s[:i] + "const DATA = /*__DATA__*/null;" + s[j:]
        except ValueError:
            pass
    if "/*__DATA__*/null" not in s:
        raise SystemExit("No const DATA = ... in that file - it does not look like a dashboard page")
    m_head = re.search(r"<head[^>]*>(.*?)</head>", s, re.S)
    m_body = re.search(r"<body[^>]*>(.*)</body>", s, re.S)
    if m_head and m_body:
        head = re.sub(r"<meta[^>]*>", "", m_head.group(1)).strip()
        body = m_body.group(1).strip()
    elif "<!--BODY-->" in s:
        head, body = (x.strip() for x in s.split("<!--BODY-->", 1))
    else:
        k = s.rfind("</style>") + len("</style>")
        head, body = s[:k].strip(), s[k:].strip()
    if os.path.exists(TEMPLATE_PATH):
        os.replace(TEMPLATE_PATH, TEMPLATE_PATH.replace(".html", ".bak.html"))
    with open(TEMPLATE_PATH, "w", encoding="utf8") as f:
        f.write(head + "\n<!--BODY-->\n" + body + "\n")
    return TEMPLATE_PATH


def localize(tpl, lang):
    """Swap the template's English strings for another language using i18n/<lang>.json, a flat
    {english: translation} map. Longest strings first, so a short phrase cannot eat a longer one
    it is part of. The template stays the single source of truth: a new string simply shows up
    as untranslated, and the caller reports it."""
    if lang == "en":
        return tpl, []
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n", f"{lang}.json")
    table = load_json(path, None)
    if table is None:
        raise SystemExit(f"No translation table for '{lang}': expected {path}")
    missing = [en for en in table if en not in tpl]
    for en in sorted(table, key=len, reverse=True):
        tpl = tpl.replace(en, table[en])
    return tpl, missing


def render(data, lang="en"):
    with open(TEMPLATE_PATH, encoding="utf8") as f:
        tpl = f.read()
    tpl, missing = localize(tpl, lang)
    head, body = tpl.split("<!--BODY-->", 1)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    body = body.replace("/*__DATA__*/null", payload)
    full = (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">' + head + "</head><body>" + body + "</body></html>")
    paths = (os.path.join(OUT_DIR, "dashboard.html"), os.path.join(OUT_DIR, "dashboard.artifact.html"))
    for path, content in zip(paths, (full, head + body)):
        with open(path, "w", encoding="utf8") as f:
            f.write(content)
    save_json(os.path.join(OUT_DIR, "data.json"), data)  # the same data on its own, for a hand-written template
    with open(os.path.join(OUT_DIR, "dashboard.design.html"), "w", encoding="utf8") as f:  # sample for Claude Design
        f.write(full.replace(payload, json.dumps(trim_for_design(data), ensure_ascii=False).replace("</", "<\\/")))
    return paths, missing


def summary(data, days_back=30):
    cut = (datetime.strptime(data["today"], "%Y-%m-%d") - timedelta(days=days_back)).strftime("%Y-%m-%d")
    sec = defaultdict(float)
    out = calls = 0
    for d in data["days"]:
        if d["d"] <= cut:
            continue
        for name, v in d["p"].items():
            sec[data["projects"][name]] += v["sec"]
            for m in v["models"].values():
                out += m["output"]
                calls += m["calls"]
    total = sum(sec.values()) or 1
    work = sum(sec[g] for g in data["work_groups"])
    lines = [f"Last {days_back} days (through {data['today']}): {total/3600:.1f} active h, {calls} calls, {out/1e6:.2f}M tokens out"]
    for g in data["groups"]:
        lines.append(f"  {g['label']:<20} {sec[g['id']]/3600:6.1f} h  {100*sec[g['id']]/total:4.0f}%")
    lines.append(f"  work (core+work): {100*work/total:.0f}%")
    done = [w for w in data["weeks"] if not w["partial"] and not w["estimated"] and w["pct"] is not None][-4:]
    cur = data["weeks"][-1] if data["weeks"] else None
    if cur:
        tag = "calibrated" if not cur["estimated"] else "estimate; feed it a /usage reading"
        fab = f", Fable {cur['fable_pct']:.0f}%" if cur.get("fable_pct") is not None else ""
        lines.append(f"This week ({cur['plan_label']}): {cur['pct']:.0f}% of the limit{fab} ({tag})" if cur["pct"] is not None
                     else f"This week ({cur['plan_label']}): {cur['units']:.0f} units, this plan's budget is not calibrated - feed it a /usage reading")
    if done:
        lines.append(f"Full calibrated weeks ({len(done)}): {sum(w['pct'] for w in done) / len(done):.0f}% on average")
    return "\n".join(lines)


def parse_usage(payload, cfg):
    """The /usage endpoint's answer -> a reading {"pct", "fable_pct"} plus the week's reset moment in
    local time, or (None, None) when there is no weekly limit in it."""
    obs, reset = {}, None
    for lim in payload.get("limits", []):
        if lim.get("group") != "weekly" or lim.get("percent") is None:
            continue
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        if lim.get("kind") == "weekly_all":
            obs["pct"] = lim["percent"]
            reset = parse_ts(lim["resets_at"], cfg).strftime("%Y-%m-%d %H:%M")
        elif model == "Fable":
            obs["fable_pct"] = lim["percent"]
    return (obs, reset) if "pct" in obs else (None, None)


def parse_credit(payload, cfg):
    """The cloud session credit from the /usage answer -> {"limit", "used", "expires"} or None. It sits under
    a codename that may change, so it is found as the entry that carries a dollar limit."""
    for v in payload.values():
        if isinstance(v, dict) and v.get("limit_dollars") and v.get("used_dollars") is not None:
            return {"limit": v["limit_dollars"], "used": round(v["used_dollars"], 2),
                    "expires": parse_ts(v["resets_at"], cfg).strftime("%Y-%m-%d %H:%M") if v.get("resets_at") else None}
    return None


def fetch_usage(cfg):
    """Fetch the /usage reading with the Claude Code login token from ~/.claude/.credentials.json — the
    same call the /usage panel makes. The token goes to api.anthropic.com and nowhere else, and is
    never printed. Raises RuntimeError with a reason when it cannot. The plan recorded in the token
    (subscriptionType) is ignored: it freezes at login time."""
    tok = (load_json(CREDENTIALS_PATH, {}).get("claudeAiOauth") or {}).get("accessToken")
    if not tok:
        raise RuntimeError(f"no login token in {CREDENTIALS_PATH}")
    req = urllib.request.Request(USAGE_URL, headers={"Authorization": "Bearer " + tok,
                                                     "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}" + (" - the token has expired; open Claude Code and it refreshes" if e.code == 401 else ""))
    except OSError as e:
        raise RuntimeError(f"network: {getattr(e, 'reason', e)}")
    obs, reset = parse_usage(payload, cfg)
    if not obs:
        raise RuntimeError("no weekly limit in the answer")
    return obs, reset, parse_credit(payload, cfg)


def record_observation(cfg, obs, now):
    """Add a reading; a rerun within the hour replaces the last one instead of piling up entries."""
    obs_list = cfg.setdefault("observations", [])
    if obs_list and now - parse_local(obs_list[-1].get("at") or obs_list[-1]["date"], cfg) < timedelta(hours=1):
        obs_list[-1] = obs
    else:
        obs_list.append(obs)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--observe", type=float, metavar="PCT", help="/usage reading by hand: percent of the week (all models); without it the reading is fetched")
    ap.add_argument("--fable", type=float, metavar="PCT", help="/usage reading: percent of the week for Fable (its own scale)")
    ap.add_argument("--at", help="when the reading was taken, 'YYYY-MM-DD HH:MM' (default: now)")
    ap.add_argument("--reset", metavar="'YYYY-MM-DD HH:MM'", help="the week's reset moment, from /usage")
    ap.add_argument("--factor", type=float, default=1.0, help="budget multiplier for the reading's week (1.5 during a +50%% promo)")
    ap.add_argument("--no-fetch", action="store_true", help="do not fetch the /usage reading with the Claude Code login token")
    ap.add_argument("--open", action="store_true", help="open dashboard.html in a browser")
    ap.add_argument("--adopt", metavar="FILE.html", help="adopt a look: HTML from Claude Design -> template.html, then rebuild")
    ap.add_argument("--out-dir", metavar="DIR", help=f"data directory: config, history, rendered pages (default: {OUT_DIR})")
    ap.add_argument("--lang", help="dashboard language; needs i18n/<lang>.json (default: config 'lang', else en)")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.out_dir:
        set_out_dir(args.out_dir)
    cfg = load_config()
    now = datetime.now(tz(cfg))
    if args.adopt:
        print(f"Template updated: {adopt(args.adopt)} (the old one is beside it as template.bak.html)")
    changed = False
    if args.reset:
        cfg["week_reset"] = args.reset
        changed = True
    if args.observe is not None:
        obs = {"at": args.at or now.strftime("%Y-%m-%d %H:%M"), "pct": args.observe, "budget_factor": args.factor}
        if args.fable is not None:
            obs["fable_pct"] = args.fable
        record_observation(cfg, obs, now)
        changed = True
    elif not args.no_fetch:
        try:
            obs, reset, credit = fetch_usage(cfg)
            obs["at"] = now.strftime("%Y-%m-%d %H:%M")        # no budget_factor: a promo comes from config "boosts"
            record_observation(cfg, obs, now)
            cfg["week_reset"] = reset
            if credit:
                cfg["cloud_credit"] = dict(credit, at=obs["at"])
            changed = True
            fable = f"{obs['fable_pct']:.0f} %" if "fable_pct" in obs else "-"
            print(f"/usage reading fetched: week {obs['pct']:.0f} %, Fable {fable}, resets {reset}")
        except RuntimeError as e:
            print(f"/usage reading not fetched ({e}) - calibrating from earlier readings")
    if changed:
        save_json(CONFIG_PATH, cfg)

    history = merge(load_json(HISTORY_PATH, {"days": {}}), scan(cfg))
    history["updated"] = now.isoformat()
    save_json(HISTORY_PATH, history)
    data = build_data(history, cfg, now)
    paths, missing = render(data, args.lang or cfg.get("lang", "en"))
    print(summary(data))
    if missing:
        print(f"Untranslated: {len(missing)} entries in the table no longer match the template, e.g. {missing[0][:60]!r}")
    print(f"Dashboard: {paths[0]}")
    if args.open and hasattr(os, "startfile"):
        os.startfile(paths[0])


if __name__ == "__main__":
    main()
