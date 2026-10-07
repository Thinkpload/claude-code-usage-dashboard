#!/usr/bin/env python3
"""Build demo/index.html from invented data.

Nothing here reads a real ~/.claude. The numbers come from a seeded generator, so the page is
reproducible for a given day, and it ends on today so the demo never looks stale.

    python demo/make_demo.py            # writes demo/index.html
    python demo/make_demo.py --lang ru
"""
import argparse
import json
import os
import random
import shutil
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from claude_usage_dashboard import usage_report as ur  # noqa: E402

DAYS = 90
PROJECTS = [
    # name, group, share of activity, commits per active hour
    ("orbit-api", "core", 0.40, 0.9),
    ("orbit-web", "work", 0.22, 0.7),
    ("shared-infra", "work", 0.13, 0.5),
    ("stargazer", "personal", 0.17, 1.1),
    ("dotfiles", "personal", 0.08, 1.4),
]
# hour of day -> relative weight; a working day with an evening tail
HOUR_WEIGHT = [0, 0, 0, 0, 0, .1, .4, .9, 1.6, 2.2, 2.4, 2.1,
               1.2, 1.8, 2.3, 2.2, 1.9, 1.4, .9, .8, 1.1, 1.3, .7, .2]
MODELS = [("claude-opus-5", .74), ("claude-fable-5-1", .08),
          ("claude-sonnet-5", .15), ("claude-haiku-4-5-20251001", .03)]
SKILLS = ["brainstorming", "systematic-debugging", "writing-plans", "dataviz", "test-driven-development"]

CONFIG = {
    "tz_offset_hours": 0,
    "idle_minutes": 10,
    "lang": "en",
    "groups": [
        {"id": "core", "label": "Main work", "match": ["orbit-api"]},
        {"id": "work", "label": "Work misc", "match": ["orbit-*", "shared-*"]},
        {"id": "personal", "label": "Personal", "match": ["*"]},
    ],
    "work_groups": ["core", "work"],
    "plan_history": [{"from": "2026-01-01", "plan": "max5"}],
    "git_roots": [],
    "git_authors": [],
}


def day_profile(rnd, d):
    """Seconds of activity for one day: weekdays heavy, weekends light, some days off."""
    weekday = d.weekday()
    base = {0: 5.6, 1: 6.4, 2: 6.8, 3: 6.1, 4: 4.9, 5: 1.6, 6: 1.1}[weekday]
    if rnd.random() < (0.08 if weekday < 5 else 0.45):
        return 0.0
    return max(0.0, rnd.gauss(base, base * 0.35)) * 3600


def spread_hours(rnd, total_sec):
    hours = [0.0] * 24
    if total_sec <= 0:
        return hours
    weights = [w * rnd.uniform(0.5, 1.5) for w in HOUR_WEIGHT]
    s = sum(weights) or 1
    left = total_sec
    for h in range(24):
        take = min(left, total_sec * weights[h] / s)
        hours[h] = round(take, 1)
        left -= take
    return hours


def make_models(rnd, sec):
    """Token counts roughly proportional to time, with cache reads dominating as they do in life."""
    calls = max(1, int(sec / 40 * rnd.uniform(0.8, 1.2)))
    out = {}
    for name, share in MODELS:
        c = int(calls * share * rnd.uniform(0.7, 1.3))
        if not c:
            continue
        out[name] = {
            "calls": c,
            "input": int(c * rnd.uniform(1, 4)),
            "cache_create": int(c * rnd.uniform(2000, 4500)),
            "cache_read": int(c * rnd.uniform(180_000, 320_000)),
            "output": int(c * rnd.uniform(700, 1400)),
            "thinking": int(c * rnd.uniform(200, 500)),
        }
    return out


def make_ins(rnd, cfg, models):
    ins = ur.new_ins()
    units = ur.units_of(models, cfg)
    calls = sum(m["calls"] for m in models.values())
    ins["units"] = round(units, 3)
    shares = [rnd.uniform(*r) for r in ((0.02, 0.08), (0.08, 0.16), (0.14, 0.24), (0.55, 0.72))]
    s = sum(shares)
    for key, part in zip(("lt50", "50_100", "100_150", "gt150"), shares):
        ins["ctx"][key] = round(units * part / s, 3)
    # effort by exact model and context bucket, [calls, units, output]: share of the calls, then what one call
    # costs and writes against a call at high on the same model; max only on the big models
    levels = (("xhigh", 0.24, 1.12, 1.37), ("high", 0.50, 1.0, 1.0), ("medium", 0.12, 0.87, 0.70), ("low", 0.04, 0.38, 0.26), ("max", 0.10, 1.57, 2.08))
    for name, m in models.items():
        lv = [x for x in levels if x[0] != "max" or "opus" in name or "fable" in name]
        jit = {e: rnd.uniform(0.95, 1.05) for e, *_ in lv}
        price, write = (sum(part * w[i] * jit[e] for e, part, *w in lv) for i in (0, 1))
        mu = ur.units_of({name: m}, cfg)
        for effort, part, pw, ow in lv:
            for key, bpart in zip(("lt50", "50_100", "100_150", "gt150"), shares):
                c = int(m["calls"] * part * bpart / s)
                if not c:
                    continue
                ins["effort"][effort] += c
                ins["effort_cells"].setdefault(effort, {}).setdefault(name, {})[key] = [
                    c, round(mu * part * pw * jit[effort] / price * bpart / s, 4), int(m["output"] * part * ow * jit[effort] / write * bpart / s)]
    ins["side_units"] = round(units * rnd.uniform(0.02, 0.09), 3)
    ins["cache_create_units"] = round(units * rnd.uniform(0.08, 0.18), 3)
    for skill in rnd.sample(SKILLS, rnd.randint(1, 3)):
        ins["skills"][skill] = round(units * rnd.uniform(0.004, 0.03), 3)
    ins["sessions"] = max(1, int(calls / rnd.uniform(40, 90)))
    ins["sessions_short"] = int(ins["sessions"] * rnd.uniform(0.1, 0.4))
    ins["first_call_units"] = round(ins["sessions"] * rnd.uniform(0.4, 0.9), 3)
    return ins


def build_history(cfg, today, seed=20260101):
    rnd = random.Random(seed)
    days, commits = {}, {}
    for i in range(DAYS):
        d = today - timedelta(days=DAYS - 1 - i)
        key = d.strftime("%Y-%m-%d")
        total = day_profile(rnd, d)
        if (today - d).days < 4:   # a crunch before a release, every day of it worked: the gauge has something to warn about
            total = max(total, 4.5 * 3600) * 1.3
        if total <= 0:
            continue
        day, day_commits = {}, {}
        for name, _group, share, cph in PROJECTS:
            sec = total * share * rnd.uniform(0.4, 1.8)
            if sec < 300:
                continue
            hours = spread_hours(rnd, sec)
            models = make_models(rnd, sec)
            day[name] = {
                "sec": round(sec, 1),
                "hours": hours,
                "msgs": max(1, int(sec / 320 * rnd.uniform(0.7, 1.3))),
                "sessions": [f"{key}-{name}-{n}" for n in range(max(1, int(sec / 7200)))],
                "models": models,
                "ins": make_ins(rnd, cfg, models),
            }
            per_hour = [0] * 24
            for h, sec_h in enumerate(hours):
                if sec_h > 600 and rnd.random() < 0.45:
                    per_hour[h] = max(1, int(sec_h / 3600 * cph * rnd.uniform(0.5, 2.0)))
            if any(per_hour):
                day_commits[name] = per_hour
        if day:
            days[key] = day
        if day_commits:
            commits[key] = day_commits
    return {"days": days, "updated": today.isoformat()}, commits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--out", default=os.path.join(HERE, "index.html"))
    ap.add_argument("--strict", action="store_true", help="fail if the translation table has drifted from the template")
    args = ap.parse_args()

    cfg = dict(ur.DEFAULT_CONFIG)
    cfg.update(CONFIG)
    cfg["lang"] = args.lang
    now = datetime.now(ur.tz(cfg)).replace(hour=16, minute=20, second=0, microsecond=0)
    history, commits = build_history(cfg, now.date())

    # Put the reset far enough ahead that "now" sits two thirds into the week, so the gauge shows
    # a week in flight rather than one that just started.
    cfg["week_reset"] = (now + timedelta(days=2, hours=8)).strftime("%Y-%m-%d 20:00")
    # The paid month began 25 days ago, so the plan view has a month of limit weeks to show.
    cfg["plan_history"] = [{"from": (now - timedelta(days=25)).strftime("%Y-%m-%d"), "plan": "max5"}]

    # /usage readings twice a week against one weekly budget, rounded to whole percent like the real
    # ones. The budget is set so the heaviest finished week lands near the limit without crossing it, and the
    # week in flight, two thirds in, stays under three quarters of it.
    is_fable = lambda m: m.startswith("claude-fable")
    fmt = lambda t: t.strftime("%Y-%m-%d %H:%M")
    start = ur.window_start(now, cfg)
    weeks = [start - timedelta(days=7 * i) for i in range(6, -1, -1)]
    full = [ur.units_between(history, w, w + timedelta(days=7), cfg) for w in weeks[:-1]]
    full_f = [ur.units_between(history, w, w + timedelta(days=7), cfg, only=is_fable) for w in weeks[:-1]]
    budget = max(max(full) / 0.96, ur.units_between(history, start, now, cfg) / 0.74)
    fable_budget = max(max(full_f) / 0.88, ur.units_between(history, start, now, cfg, only=is_fable) / 0.74)
    reading = lambda w, at: {"at": fmt(at), "pct": round(100 * ur.units_between(history, w, at, cfg) / budget),
                             "fable_pct": round(100 * ur.units_between(history, w, at, cfg, only=is_fable) / fable_budget)}
    obs = [reading(w, at) for w in weeks for at in (w + timedelta(days=3, hours=-4), w + timedelta(days=6))
           if at < now - timedelta(hours=1)]                                 # mid-week, then the evening before the reset
    # the latest reading is minutes old and carries the 5-hour window: opened three hours ago, a sixth of the week
    last, five_reset = reading(start, now - timedelta(minutes=10)), now + timedelta(hours=2)
    last["five_pct"] = round(100 * ur.units_between(history, five_reset - timedelta(hours=5), now, cfg) / (budget / 6))
    last["five_reset"] = fmt(five_reset)
    cfg["observations"] = [o for o in obs + [last] if o["pct"] > 0]
    cfg["cloud_credit"] = {"limit": 250, "used": 61.4, "expires": fmt(now + timedelta(days=19)), "at": fmt(now)}

    out_dir = os.path.join(HERE, "_build")
    ur.set_out_dir(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    ur.save_json(ur.CONFIG_PATH, cfg)

    data = ur.build_data(history, cfg, now)
    data["commits"] = commits
    data["git"] = {"repos": len(PROJECTS), "authors": ["you@example.com"]}
    data["config_path"] = "~/.claude/usage-report/config.json"   # never leak the build machine's path

    paths, missing = ur.render(data, args.lang)
    shutil.copyfile(paths[0], args.out)
    shutil.rmtree(out_dir, ignore_errors=True)
    if missing:
        print(f"{len(missing)} translation entries no longer match the template, e.g. {missing[0][:70]!r}")
        if args.strict:
            raise SystemExit(1)
    print(f"{args.out} ({os.path.getsize(args.out) // 1024} KB), {len(history['days'])} days, lang={args.lang}")


if __name__ == "__main__":
    main()
