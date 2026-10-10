# data.json — the fields

`~/.claude/usage-report/data.json` is rebuilt on every run, and the same object is injected into the
template as `const DATA = …`. Times are local (`tz_offset_hours` from the settings), dates are
`YYYY-MM-DD` strings, seconds and tokens are integers. `data.json` holds your raw prompt text (the
`prompts` keys): before publishing a page built on it, hash or drop those keys, as `hide_prompts()` does for the artifact.

```jsonc
{
  "generated": "2026-09-12 04:45",      // when it was built
  "today": "2026-09-12",
  "groups": [{"id": "core", "label": "Main work"}, …],   // order = colour order
  "work_groups": ["core", "work"],       // which groups count as "work"
  "projects": {"orbit-api": "core", "stargazer": "personal", …},   // project -> group
  "model_labels": {"claude-opus-5": "Opus 5", …},
  "months": ["2026-09", "2026-08", …],   // months with data, newest first
  "billing_months": [                    // paid months, oldest first: from the day the current plan began (plan_history)
    {"start": "2026-09-08", "end": "2026-10-07",
     "units": 2926.4,                    // load units in the month
     "budget": 4254.7,                   // the most the limit allowed in it: each week's budget pro rata to its time there,
                                         // the running month to its end at this week's budget, counted in load units and
                                         // shown in units at the month's model mix (like a week's); null without one
     "budget_units": 2925.4,             // load units over the time that has a budget, the base of pct
     "pct": 68.8,                        // budget_units / budget x 100; null without a budget (Fable is weekly only)
     "covered_days": 30.0, "days": 30,   // days with a budget / days in the month
     "partial": true}                    // the month is still running
  ],

  "days": [                              // ascending by date; only days with activity
    {"d": "2026-09-11",
     "p": {                              // project ->
       "orbit-api": {
         "sec": 9720,                    // active seconds (gaps <= idle_minutes)
         "hours": [0, 0, …, 1800, …],    // the same seconds by hour of day, 0-23
         "msgs": 42,                     // user messages (tool results excluded)
         "sessions": ["b622af0b-…", …],  // ids of sessions alive that day
         "mine": 30,                     // of msgs, the ones you typed (not subagent prompts, `claude -p` runs, interruptions or command tags)
         "nudges": 6,                    // of those, short pokes: up to 15 characters, not a slash command ("continue", "ok")
         "prompts": {"normalized start of a request": 4},   // requests of 20+ characters, not slash commands: lower-cased, spaces
                                         // collapsed, cut at 80 characters -> how often it came up that day. In
                                         // dashboard.artifact.html and dashboard.design.html the keys are "#" + 8 hex of the
                                         // text's sha1 instead of the text: the counts travel, your words do not
         "ins": {                        // where the spend goes, in load units (tokens x weights)
           "units": 12.4, "ctx": {"lt50": 0.1, "50_100": 2.0, "100_150": 3.1, "gt150": 7.2},   // by the call's context size
           "effort": {"high": 200, "xhigh": 60},          // calls by effort level
           "effort_cells": {"high": {"claude-opus-5": {"gt150": [180, 3.9, 210000]}}},   // effort -> exact model -> context bucket -> [calls, load units, output tokens]
           "side_units": 0.2,                             // of which subagents
           "cache_create_units": 2.6,                     // cache writes
           "skills": {"claude-api": 0.4},                 // re-reading a skill's text from cache
           "sessions": 3, "sessions_short": 1, "first_call_units": 0.8   // sessions started that day; shorter than 3 calls; cost of their first calls
         },
         "models": {                     // model ->
           "claude-opus-5": {"calls": 286, "input": 6000, "cache_create": 1600000,
                             "cache_read": 83853000, "output": 440000, "thinking": 102000}
         }}}}
  ],

  "weeks": [                             // weekly limit windows (from week_reset), ascending
    {"start": "2026-09-10", "end": "2026-09-16", "start_at": "2026-09-10 20:00",
     "plan": "max5", "plan_label": "Max 5x",
     "units": 105.5,                     // load units = tokens x weights, all models
     "budget": 527.6,                    // the window's budget including any promo, in units at this window's model mix
                                         // (the limit counts Fable at fable_load); null if the plan is not calibrated
     "pct": 20.0,                        // units / budget x 100 (= load / the budget in load units); null without a budget
     "fable_units": 83.9, "fable_budget": 246.6, "fable_pct": 34.0,   // Fable's separate scale
     "fable_load": 2.0,                  // the plan's fable_load (see limits.budgets) the window's pct is counted with
     "boost": 1.5,                       // promo multiplier for this window
     "estimated": false,                 // true = this plan has no reading of its own (so no percentage)
     "partial": true}                    // the window is still running
  ],

  "presence": {                        // day -> your time at Claude, all projects and sessions together
    "2026-09-20": {"you": 25200,         // seconds: time between your own messages, all sessions merged, waiting for Claude included, unless a break: over break_minutes of quiet after Claude's last move, or a gap over an hour
                   "busy": 41000,        // seconds with any session running (gaps <= idle_minutes), parallel ones counted once
                   "longest": 6300,      // your longest stretch without a break, on the day it started
                   "night": 1800}        // of you, the seconds between 00:00 and 06:00
  },

  "commits": {                         // day -> project -> [24] commits by hour (local git, authors from the settings)
    "2026-09-11": {"orbit-api": [0, 0, …, 3, …]}
  },
  "git": {"repos": 66, "authors": ["you@example.com", …]},

  "limits": {
    "calibrated": true,                  // is there a reading for the current plan
    "plan_now": "Max 5x",
    "budgets": {"Max 5x": {"all": 351.6, "fable": 164.6, "fable_load": 2.0, "estimated": false}, "Pro": {…, "estimated": true}},
                                         // all = load units = units + (fable_load - 1) x Fable units; fable_load is fitted
                                         // to the readings (how much harder Fable loads the limit than its API price says), 1 without enough of them
    "observations": [{"at": "2026-09-12 05:50", "pct": 20, "fable_pct": 34,
                      "five_pct": 74, "five_reset": "2026-09-12 07:40"}],   // the 5-hour window, when fetched
    "week_reset": "2026-09-17 20:00",    // or null (weeks start Monday)
    "cloud_credit": {"limit": 250, "used": 1.38, "expires": "2026-11-05 10:59", "at": "2026-09-28 01:20"},  // or null
    "five_hour": {"budget": 130.3,       // one 5-hour window in load units (current plan, today's promo), pooled over readings
                  "pct": 74, "reset": "2026-09-12 07:40", "at": "2026-09-12 05:50",   // the latest reading of it
                  "readings": 3}         // or null: no 5-hour reading on the current plan yet
  },
  "idle_minutes": 10,
  "break_minutes": 15,                   // quiet after Claude's last move longer than this, before your next message, is a break (presence)
  "config_path": "~/.claude/usage-report/config.json",
  "refresh_url": "claude-usage://refresh"   // or null: not registered (--register-refresh), and always null in the artifact
}
```

Deriving your own numbers from `days`: hours for a project or group over a period is the sum of
`sec`; calls and tokens are the sum over `models`; sessions in a period is the union of `sessions`
(one session can span several days); the weekday × hour heatmap is the sum of `hours` bucketed by
the weekday of `d`. For your own time at Claude use `presence[d].you`, not the sum of `sec`: parallel
sessions overlap there. Note that `thinking` is already part of `output` — do not add them together.
