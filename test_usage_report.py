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
    assert obs == {"pct": 48, "fable_pct": 49} and reset == "2026-09-17 20:00", (obs, reset)   # UTC -> +3
    assert ur.parse_usage({"limits": []}, cfg) == (None, None)
    # a rerun within the hour replaces the last reading; later on it appends
    cfg["observations"] = [{"at": "2026-09-14 16:00", "pct": 48}]
    ur.record_observation(cfg, {"at": "2026-09-14 16:40", "pct": 49}, ur.parse_local("2026-09-14 16:40", cfg))
    assert [o["pct"] for o in cfg["observations"]] == [49]
    ur.record_observation(cfg, {"at": "2026-09-14 18:00", "pct": 51}, ur.parse_local("2026-09-14 18:00", cfg))
    assert [o["pct"] for o in cfg["observations"]] == [49, 51]


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
