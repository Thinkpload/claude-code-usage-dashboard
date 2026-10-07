"""Core checks: python test_usage_report.py"""
import json
import os
import tempfile
from datetime import date

from claude_usage_dashboard import usage_report as ur

CFG = dict(ur.DEFAULT_CONFIG)
CFG["observations"] = []
CFG["tz_offset_hours"] = 3
CFG["strip_prefixes"] = ["c--Users-ann-code-", "c--Users-ann"]
CFG["groups"] = [
    {"id": "core", "label": "Main work", "match": ["acme-api"]},
    {"id": "work", "label": "Work misc", "match": ["acme-*", "*-infra*"]},
    {"id": "personal", "label": "Personal", "match": ["*"]},
]

SLUG = "c--Users-ann-code-acme-api"


def write_session(root, slug, sid, records):
    d = os.path.join(root, slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, sid + ".jsonl"), "w", encoding="utf8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def asst(ts, req, model="claude-opus-5", out=100):
    return {"type": "assistant", "timestamp": ts, "requestId": req,
            "message": {"model": model, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 10,
                                                  "cache_read_input_tokens": 1000, "output_tokens": out,
                                                  "output_tokens_details": {"thinking_tokens": 40}}}}


def user(ts, tool=False):
    content = [{"type": "tool_result"}] if tool else "hello"
    return {"type": "user", "timestamp": ts, "message": {"content": content}}


def test_scan_dedupes_and_applies_idle_rule():
    with tempfile.TemporaryDirectory() as root:
        # 12:00 UTC = 15:00 local; three lines of one call (one requestId) + a call after 30 min idle
        write_session(root, SLUG, "s1", [
            user("2026-09-01T12:00:00Z"),
            asst("2026-09-01T12:00:05Z", "r1"), asst("2026-09-01T12:00:05Z", "r1"), asst("2026-09-01T12:00:05Z", "r1"),
            user("2026-09-01T12:04:05Z", tool=True),
            asst("2026-09-01T12:34:05Z", "r2"),
        ])
        days = ur.scan(CFG, root)
    v = days["2026-09-01"]["acme-api"]
    assert v["models"]["claude-opus-5"]["calls"] == 2, "duplicate requestIds must collapse into one call"
    assert v["msgs"] == 1, "a tool_result is not a user message"
    assert v["sec"] == 245, f"5s + 240s, the 30 min gap does not count; got {v['sec']}"
    assert v["hours"][15] == 245, "activity lands in local hour 15"


def test_skill_attribution_and_context_buckets():
    with tempfile.TemporaryDirectory() as root:
        skill_call = {"type": "assistant", "timestamp": "2026-09-01T12:00:05Z", "requestId": "r1",
                      "message": {"model": "claude-opus-5", "usage": {"input_tokens": 1, "cache_read_input_tokens": 200_000, "output_tokens": 10},
                                  "content": [{"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "demo"}}]}}
        skill_body = {"type": "user", "timestamp": "2026-09-01T12:00:06Z", "isMeta": True, "sourceToolUseID": "t1",
                      "message": {"content": [{"type": "text", "text": "x" * 40_000}]}}  # ~10k tokens
        write_session(root, SLUG, "s1",
                      [user("2026-09-01T12:00:00Z"), skill_call, skill_body, asst("2026-09-01T12:00:30Z", "r2"), asst("2026-09-01T12:00:40Z", "r3")])
        days = ur.scan(CFG, root)
    ins = days["2026-09-01"]["acme-api"]["ins"]
    assert ins["ctx"]["gt150"] > 0 and ins["ctx"]["lt50"] > 0, "calls are bucketed by context size"
    # two calls after the skill loaded x 10k tokens x 0.5/1M
    assert abs(ins["skills"]["demo"] - 2 * 10_000 * 0.5 / 1e6) < 1e-9, ins["skills"]
    assert ins["sessions"] == 1 and ins["sessions_short"] == 0
    assert days["2026-09-01"]["acme-api"]["msgs"] == 1, "the meta record carrying the skill text is not a user message"


def test_effort_splits_spend():
    """Calls and their load units by effort level, so the page can set a call's share against its spend."""
    with tempfile.TemporaryDirectory() as root:
        hi, mx = asst("2026-09-01T12:00:05Z", "r1", out=100), asst("2026-09-01T12:00:30Z", "r2", out=900)
        hi["effort"], mx["effort"] = "high", "max"
        write_session(root, SLUG, "s1", [user("2026-09-01T12:00:00Z"), hi, mx])
        days = ur.scan(CFG, root)
    ins = days["2026-09-01"]["acme-api"]["ins"]
    assert dict(ins["effort"]) == {"high": 1, "max": 1}
    cells = ins["effort_cells"]                    # effort -> model -> context bucket -> [calls, units, output]
    hi_c, mx_c = cells["high"]["claude-opus-5"]["lt50"], cells["max"]["claude-opus-5"]["lt50"]   # 1 011 tokens of context
    assert hi_c[0] == 1 and mx_c[0] == 1 and hi_c[2] == 100 and mx_c[2] == 900, cells
    assert abs(hi_c[1] + mx_c[1] - ins["units"]) < 1e-9 and mx_c[1] > hi_c[1], cells
    hist = ur.merge({"days": {}}, days)
    assert hist["days"]["2026-09-01"]["acme-api"]["ins"]["effort_cells"]["max"]["claude-opus-5"]["lt50"][2] == 900


def test_groups_and_names():
    assert ur.project_name("c--Users-ann-code-acme-billing", CFG) == "acme-billing"
    assert ur.project_name("c--Users-ann", CFG) == "~"
    assert ur.group_of("acme-api", CFG) == "core"
    assert ur.group_of("acme-billing", CFG) == "work"
    assert ur.group_of("shared-infra-2", CFG) == "work"
    assert ur.group_of("stargazing-app", CFG) == "personal"


def test_home_slug_matches_claude_code_encoding():
    """The default strip_prefixes must peel the home path off a real log folder name."""
    cfg = dict(CFG)
    cfg["strip_prefixes"] = [ur.home_slug(r"C:\Users\ann") + "-", ur.home_slug(r"C:\Users\ann")]
    assert ur.project_name("c--Users-ann-code-acme-api", cfg) == "code-acme-api"
    assert ur.project_name("c--Users-ann--dev-notes", cfg) == "dev-notes", "leading dashes are trimmed"
    assert ur.home_slug("/home/ann") == "-home-ann"


def test_merge_keeps_fuller_snapshot():
    hist = {"days": {"2026-09-01": {"P": {"sec": 100, "hours": [0] * 24, "msgs": 3, "sessions": ["a", "b"], "ins": ur.new_ins(),
                                          "models": {"claude-opus-5": {"calls": 5, "input": 0, "cache_create": 0, "cache_read": 0, "output": 0, "thinking": 0}}}}}}
    fresh = {"2026-09-01": {"P": {"sec": 40, "hours": [0] * 24, "msgs": 1, "sessions": {"b"}, "ins": ur.new_ins(),
                                  "models": {"claude-opus-5": {"calls": 2, "input": 0, "cache_create": 0, "cache_read": 0, "output": 0, "thinking": 0}}}}}
    ur.merge(hist, fresh)
    assert hist["days"]["2026-09-01"]["P"]["sec"] == 100, "once the logs are pruned the older, fuller snapshot stays"


def test_calibration_per_plan_with_fable_and_windows():
    from datetime import datetime, timedelta, timezone
    cfg = dict(CFG)
    cfg["plan_history"] = [{"from": "2026-01-01", "plan": "pro"}, {"from": "2026-09-08", "plan": "max5"}]
    cfg["week_reset"] = "2026-09-17 20:00"        # windows: Thu 20:00 -> Thu 20:00
    cfg["boosts"] = [{"from": "2026-09-01", "to": "2026-09-13", "factor": 1.5}]
    cfg["observations"] = [{"at": "2026-09-12 06:00", "pct": 20, "fable_pct": 50}]
    opus = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 1_000_000, "thinking": 0}   # 25 units
    fable = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 1_000_000, "thinking": 0}  # 50 units
    hours = [0] * 24; hours[10] = 3600; hours[22] = 3600                                          # activity split evenly over 10:00 and 22:00
    day = lambda models: {"P": {"sec": 7200, "hours": hours, "msgs": 0, "sessions": [], "models": models}}
    hist = {"days": {"2026-09-10": day({"claude-opus-5": opus}),                     # half falls before 20:00 -> 12.5 units in the window
                     "2026-09-11": day({"claude-opus-5": opus, "claude-fable-5-1": fable})}}  # 75 units, 50 of them Fable
    assert ur.window_start(ur.parse_local("2026-09-12 06:00", cfg), cfg) == datetime(2026, 9, 10, 20, 0, tzinfo=timezone(timedelta(hours=3)))
    used = ur.units_between(hist, ur.parse_local("2026-09-10 20:00", cfg), ur.parse_local("2026-09-12 06:00", cfg), cfg)
    assert abs(used - 87.5) < 1e-6, used
    b = ur.calibrate(hist, cfg)
    assert not b["max5"]["estimated"] and b["pro"]["estimated"]
    assert abs(b["max5"]["all"] - 87.5 / 0.2 / 1.5) < 1e-6, b["max5"]        # the +50% promo is divided out
    assert abs(b["max5"]["fable"] - 50 / 0.5 / 1.5) < 1e-6, b["max5"]
    weeks, _ = ur.build_weeks(hist, cfg, ur.parse_local("2026-09-12 06:00", cfg))
    cur = weeks[-1]
    assert cur["plan"] == "max5" and cur["partial"] and abs(cur["pct"] - 20) < 1e-6 and abs(cur["fable_pct"] - 50) < 1e-6, cur


def test_parse_usage_from_endpoint_payload():
    cfg = dict(CFG)
    payload = {"limits": [                                             # the shape api/oauth/usage returned on 2026-09-14
        {"kind": "session", "group": "session", "percent": 49, "resets_at": "2026-09-14T15:30:00.891907+00:00", "scope": None},
        {"kind": "weekly_all", "group": "weekly", "percent": 48, "resets_at": "2026-09-17T17:00:00.891939+00:00", "scope": None},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 49, "resets_at": "2026-09-17T16:59:59.892328+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}}]}
    obs, reset = ur.parse_usage(payload, cfg)
    assert obs == {"pct": 48, "fable_pct": 49, "five_pct": 49, "five_reset": "2026-09-14 18:30"} and reset == "2026-09-17 20:00", (obs, reset)   # UTC -> +3
    # reset times wobble around the minute from one fetch to the next: a second short of it still lands on the minute
    wobble = {"limits": [
        {"kind": "session", "group": "session", "percent": 49, "resets_at": "2026-09-14T15:29:59.991907+00:00", "scope": None},
        {"kind": "weekly_all", "group": "weekly", "percent": 48, "resets_at": "2026-09-17T16:59:59.892328+00:00", "scope": None}]}
    assert ur.parse_usage(wobble, cfg) == ({"pct": 48, "five_pct": 49, "five_reset": "2026-09-14 18:30"}, "2026-09-17 20:00")
    assert ur.parse_usage({"limits": []}, cfg) == (None, None)
    # the cloud session credit sits under a codename; it is the entry that carries a dollar limit
    credit = {"five_hour": {"utilization": 20.0, "limit_dollars": None, "used_dollars": None},
              "iguana_necktie": {"utilization": 0.55, "resets_at": "2026-11-05T07:59:00+00:00",
                                 "limit_dollars": 250, "used_dollars": 1.378266, "remaining_dollars": 248.621734}}
    assert ur.parse_credit(credit, cfg) == {"limit": 250, "used": 1.38, "expires": "2026-11-05 10:59"}
    assert ur.parse_credit(payload, cfg) is None
    # a rerun within the hour replaces the last reading; later on it appends
    cfg["observations"] = [{"at": "2026-09-14 16:00", "pct": 48}]
    ur.record_observation(cfg, {"at": "2026-09-14 16:40", "pct": 49}, ur.parse_local("2026-09-14 16:40", cfg))
    assert [o["pct"] for o in cfg["observations"]] == [49]
    ur.record_observation(cfg, {"at": "2026-09-14 18:00", "pct": 51}, ur.parse_local("2026-09-14 18:00", cfg))
    assert [o["pct"] for o in cfg["observations"]] == [49, 51]
    # a new 5-hour window within the hour is news, not a rerun: the reading of the window that closed stays
    cfg["observations"] = [{"at": "2026-09-14 16:00", "pct": 48, "five_pct": 79, "five_reset": "2026-09-14 16:10"}]
    ur.record_observation(cfg, {"at": "2026-09-14 16:20", "pct": 48, "five_pct": 2, "five_reset": "2026-09-14 21:10"}, ur.parse_local("2026-09-14 16:20", cfg))
    assert [o["five_pct"] for o in cfg["observations"]] == [79, 2]


def test_calibration_pools_observations_by_size():
    cfg = dict(CFG)
    cfg["plan_history"] = [{"from": "2026-01-01", "plan": "max5"}]
    cfg["week_reset"] = "2026-09-17 20:00"
    cfg["boosts"] = []
    opus = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 1_000_000, "thinking": 0}   # 25 units a day
    hours = [0] * 24; hours[10] = 3600
    day = lambda: {"P": {"sec": 3600, "hours": hours, "msgs": 0, "sessions": [], "models": {"claude-opus-5": opus}}}
    hist = {"days": {f"2026-09-{d}": day() for d in ("11", "12", "13")}}                        # window opens Thu 10.09 20:00
    # an early reading of 1 % (rounded; really 25/1000 = 2.5 %) and a late one of 8 % (75/1000 = 7.5 %) at a budget of 1000
    cfg["observations"] = [{"at": "2026-09-11 12:00", "pct": 1}, {"at": "2026-09-13 12:00", "pct": 8}]
    b = ur.calibrate(hist, cfg)["max5"]["all"]
    assert abs(b - (25 + 75) / (0.01 + 0.08)) < 1e-6, b        # ~1111: the pooled ratio, not the mean of 2500 and 937
    assert 1000 < b < 1200


def test_boost_change_applies_mid_week():
    """A limit change lands at once, not at the next reset: each reading sees the factor of its own moment,
    and the window is drawn with the factor in effect at its end (now, for the current week)."""
    cfg = dict(CFG)
    cfg["plan_history"] = [{"from": "2026-01-01", "plan": "max5"}]
    cfg["week_reset"] = "2026-09-17 20:00"
    cfg["boosts"] = [{"from": "2026-09-01", "to": "2026-09-13", "factor": 1.5},      # 1000 -> 1500 ...
                     {"from": "2026-09-14", "to": "2099-12-31", "factor": 1.25}]     # ... then 1250 from the 14th
    opus = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 1_000_000, "thinking": 0}   # 25 units a day
    hours = [0] * 24; hours[10] = 3600
    day = lambda: {"P": {"sec": 3600, "hours": hours, "msgs": 0, "sessions": [], "models": {"claude-opus-5": opus}}}
    hist = {"days": {f"2026-09-{d}": day() for d in ("11", "12", "13", "14")}}
    # 50 units against 1500 on the 12th, 100 units against 1250 on the 14th: both point at a base of 1000
    cfg["observations"] = [{"at": "2026-09-12 12:00", "pct": 100 * 50 / 1500}, {"at": "2026-09-14 12:00", "pct": 8}]
    assert abs(ur.calibrate(hist, cfg)["max5"]["all"] - 1000) < 1e-6
    weeks, _ = ur.build_weeks(hist, cfg, ur.parse_local("2026-09-14 12:00", cfg))
    assert weeks[-1]["boost"] == 1.25 and abs(weeks[-1]["pct"] - 8) < 1e-6, weeks[-1]   # what /usage shows now


def test_week_with_its_own_reading_follows_it():
    """A limit change nobody put into boosts (a promo, a new model) must not drag a week away from its own
    /usage reading: such a week takes its budget from its latest reading, the pooled budget covers the rest."""
    cfg = dict(CFG)
    cfg["plan_history"] = [{"from": "2026-01-01", "plan": "max5"}]
    cfg["week_reset"] = "2026-09-17 20:00"
    cfg["boosts"] = []
    opus = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 1_000_000, "thinking": 0}   # 25 units a day
    hours = [0] * 24; hours[10] = 3600
    day = lambda: {"P": {"sec": 3600, "hours": hours, "msgs": 0, "sessions": [], "models": {"claude-opus-5": opus}}}
    hist = {"days": {f"2026-09-{d}": day() for d in ("11", "12", "13", "18", "19", "20")}}
    # last week 75 units = 7.5 % of 1000; this week the limit doubled unannounced: 50 units = 2.5 % of 2000
    cfg["observations"] = [{"at": "2026-09-13 12:00", "pct": 7.5}, {"at": "2026-09-19 12:00", "pct": 2.5}]
    weeks, _ = ur.build_weeks(hist, cfg, ur.parse_local("2026-09-20 12:00", cfg))
    last, cur = weeks[-2], weeks[-1]
    assert abs(last["budget"] - 1000) < 1e-6 and abs(cur["budget"] - 2000) < 1e-6, (last, cur)
    assert cur["pct"] == round(75 / 2000 * 100, 1), cur                                       # 2.5 % at the reading + one more day


def test_fable_load_is_fitted_to_the_readings():
    """The weights are API prices, and the weekly limit prices Fable steeper than they do: per unit Fable loads it
    k times as hard as the other models. k comes from the readings, and a week counts in load units, so a week that
    went from Fable to Opus after its last reading does not carry the Fable-heavy rate into its Opus-only days."""
    cfg = dict(CFG)
    cfg["plan_history"] = [{"from": "2026-01-01", "plan": "max5"}]
    cfg["week_reset"] = "2026-09-17 20:00"
    cfg["boosts"] = []
    opus = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 1_000_000, "thinking": 0}   # 25 units
    fable = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 1_000_000, "thinking": 0}  # 50 units, a load of 100 at k = 2
    hours = [0] * 24; hours[10] = 3600
    day = lambda models: {"P": {"sec": 3600, "hours": hours, "msgs": 0, "sessions": [], "models": models}}
    mixed = {"claude-opus-5": opus, "claude-fable-5-1": fable}                                    # 75 units, a load of 125
    hist = {"days": {**{f"2026-09-{d}": day({"claude-opus-5": opus}) for d in ("04", "05", "06", "11", "12", "13", "21", "22")},
                     "2026-09-18": day(mixed), "2026-09-19": day(mixed)}}
    # a limit of 1000 load units: two Opus weeks read 2.5, 5 and 7.5 % each, the mixed one 12.5 and 25 %
    cfg["observations"] = [{"at": f"2026-09-{d} 12:00", "pct": p} for d, p in (("04", 2.5), ("05", 5), ("06", 7.5),
                           ("11", 2.5), ("12", 5), ("13", 7.5), ("18", 12.5), ("19", 25))]
    b = ur.calibrate(hist, cfg)["max5"]
    assert b["fable_load"] == 2 and abs(b["all"] - 1000) < 1e-6, b
    weeks, _ = ur.build_weeks(hist, cfg, ur.parse_local("2026-09-25 12:00", cfg))
    opus_week, mixed_week = weeks[-3], weeks[-2]
    assert opus_week["pct"] == 7.5 and opus_week["budget"] == 1000, opus_week
    # 250 load units at 25 %, then 50 of Opus: 30 %, not the 200 / 600 = 33.3 % of the week's Fable-heavy start
    assert mixed_week["pct"] == 30 and mixed_week["units"] == 200, mixed_week
    assert abs(mixed_week["budget"] - 200 / 0.3) < 0.01, mixed_week                             # in units at the week's mix: units / budget = pct
    # the paid month (the 1st to the 30th) counts in load units too: 75 + 75 + 300 of them against three full weeks and
    # the running one's 148 h in September at 1000, not the units against budgets taken at each week's own mix
    sep = ur.build_billing_months(hist, weeks, cfg, ur.parse_local("2026-09-25 12:00", cfg))[0]
    assert sep["pct"] == round(100 * 450 / (3000 + 1000 * 148 / 168), 1) and sep["budget_units"] == 350, sep
    # seven readings are too few to tell Fable's weight from noise: k stays at the API prices
    cfg["observations"] = cfg["observations"][1:]
    assert ur.calibrate(hist, cfg)["max5"]["fable_load"] == 1
    # readings that all carry the same Fable share cannot tell k apart either, whole-percent rounding or not
    same = {"days": {f"2026-09-{d}": day(mixed) for d in range(18, 26)}}                           # 75 units a day
    cfg["observations"] = [{"at": f"2026-09-{d} 12:00", "pct": p} for d, p in
                           zip(range(18, 26), (8, 15, 22, 30, 38, 45, 52, 8))]                        # 7.5 % a day, a new week on the 25th
    assert ur.calibrate(same, cfg)["max5"]["fable_load"] == 1


def test_billing_months_split_weeks_and_run_to_the_end():
    """A paid month starts on the day the current plan began. It takes each limit week's budget pro rata to the
    time the week spends in it, and the share counts that time only: days with no budget neither pad nor dilute
    it. The running month is measured against its whole length, the days ahead at this week's budget."""
    cfg = dict(CFG)
    cfg["plan_history"] = [{"from": "2026-01-01", "plan": "pro"}, {"from": "2026-09-08", "plan": "max5"}]
    cfg["week_reset"] = "2026-09-17 20:00"        # the window Thu 01.10 20:00 -> Thu 08.10 20:00 straddles two paid months
    cfg["boosts"] = []
    opus = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 1_000_000, "thinking": 0}   # 25 units a day
    hours = [0] * 24; hours[10] = 3600
    day = lambda: {"P": {"sec": 3600, "hours": hours, "msgs": 0, "sessions": [], "models": {"claude-opus-5": opus}}}
    hist = {"days": {d: day() for d in ("2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09")}}
    cfg["observations"] = [{"at": "2026-10-07 12:00", "pct": 5}]   # 50 units = 5 % -> a budget of 1000
    now = ur.parse_local("2026-10-09 12:00", cfg)
    weeks, _ = ur.build_weeks(hist, cfg, now)
    assert ur.billing_day(cfg) == 8
    sep, oct_ = ur.build_billing_months(hist, weeks, cfg, now)
    assert (sep["start"], sep["end"], oct_["start"], oct_["end"]) == ("2026-09-08", "2026-10-07", "2026-10-08", "2026-11-07")
    # 08.09-07.10 holds 148 h of the first week (01.10 20:00 -> 08.10 00:00); before it there is no week, so no budget
    assert sep["budget"] == round(1000 * 148 / 168, 1) and sep["pct"] == round(100 * 50 / (1000 * 148 / 168), 1), sep
    assert sep["covered_days"] == round(148 / 24, 1) and sep["days"] == 30 and not sep["partial"], sep
    # 08.10-07.11 is running: all 31 days at 1000 a week, of which 50 units are spent so far
    assert oct_["budget"] == round(1000 * 31 / 7, 1) and oct_["budget_units"] == 50, oct_
    assert oct_["pct"] == round(100 * 50 / (1000 * 31 / 7), 1) and oct_["partial"] and oct_["days"] == 31, oct_
    # a billing day past the end of a short month falls on its last day
    assert ur.billing_start(date(2026, 3, 1), 31) == date(2026, 2, 28)


def test_five_hour_window_in_units():
    """The 5-hour window is sized like the week: the units spent since it opened (its reset minus 5 h) over the
    share /usage shows, pooled over the readings of the current plan, promos divided out and the current one applied."""
    cfg = dict(CFG)
    cfg["plan_history"] = [{"from": "2026-01-01", "plan": "pro"}, {"from": "2026-09-08", "plan": "max5"}]
    cfg["boosts"] = [{"from": "2026-09-01", "to": "2026-09-13", "factor": 1.5}]
    opus = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 1_000_000, "thinking": 0}   # 25 units a day
    hours = [0] * 24; hours[10] = 3600; hours[14] = 3600                                          # half at 10:00, half at 14:00
    day = lambda: {"P": {"sec": 7200, "hours": hours, "msgs": 0, "sessions": [], "models": {"claude-opus-5": opus}}}
    hist = {"days": {"2026-09-05": day(), "2026-09-12": day(), "2026-09-15": day()}}
    cfg["observations"] = [
        {"at": "2026-09-05 15:00", "pct": 5, "five_pct": 90, "five_reset": "2026-09-05 18:00"},    # Pro: not this plan
        {"at": "2026-09-12 15:00", "pct": 5, "five_pct": 30, "five_reset": "2026-09-12 16:00"},    # 11:00-15:00: 12.5 units, x1.5 promo
        {"at": "2026-09-15 15:00", "pct": 5, "five_pct": 10, "five_reset": "2026-09-15 19:00"},    # 14:00-15:00: 12.5 units
        {"at": "2026-09-15 20:00", "pct": 6}]                                                       # no 5-hour reading
    f = ur.calibrate_five(hist, cfg, ur.parse_local("2026-09-15 20:00", cfg))
    assert abs(f["budget"] - (12.5 + 12.5) / (0.30 * 1.5 + 0.10)) < 0.05, f   # ~45.5 units, today's factor is 1
    assert f["pct"] == 10 and f["reset"] == "2026-09-15 19:00" and f["at"] == "2026-09-15 15:00" and f["readings"] == 2, f
    cfg["observations"] = [{"at": "2026-09-15 15:00", "pct": 5}]
    assert ur.calibrate_five(hist, cfg, ur.parse_local("2026-09-15 20:00", cfg)) is None


def test_refresh_button_only_on_local_page():
    """The refresh link reaches this machine, so only dashboard.html carries it; the published artifact
    must not, and the command it runs points at this script and this data folder."""
    data = {"refresh_url": ur.REFRESH_URL, "today": "2026-09-20", "days": [], "commits": {}, "projects": {}, "weeks": [], "billing_months": []}
    old = ur.OUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ur.set_out_dir(tmp)
            paths = ur.render(data)[0]
            local, shared = (open(p, encoding="utf8").read() for p in paths)
            cmd = ur.refresh_command()
        finally:
            ur.set_out_dir(old)
    assert '"refresh_url": "claude-usage://refresh"' in local
    assert '"refresh_url": null' in shared and "claude-usage://refresh\"" not in shared
    assert os.path.abspath(ur.__file__) in cmd and f'--out-dir "{tmp}"' in cmd, cmd


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
