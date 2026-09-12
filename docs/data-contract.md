# data.json — the fields

`~/.claude/usage-report/data.json` is rebuilt on every run, and the same object is injected into the
template as `const DATA = …`. Times are local (`tz_offset_hours` from the settings), dates are
`YYYY-MM-DD` strings, seconds and tokens are integers.

```jsonc
{
  "generated": "2026-09-12 04:45",      // when it was built
  "today": "2026-09-12",
  "groups": [{"id": "core", "label": "Main work"}, …],   // order = colour order
  "work_groups": ["core", "work"],       // which groups count as "work"
  "projects": {"orbit-api": "core", "stargazer": "personal", …},   // project -> group
  "model_labels": {"claude-opus-5": "Opus 5", …},
  "months": ["2026-09", "2026-08", …],   // months with data, newest first

  "days": [                              // ascending by date; only days with activity
    {"d": "2026-09-11",
     "p": {                              // project ->
       "orbit-api": {
         "sec": 9720,                    // active seconds (gaps <= idle_minutes)
         "hours": [0, 0, …, 1800, …],    // the same seconds by hour of day, 0-23
         "msgs": 42,                     // user messages (tool results excluded)
         "sessions": ["b622af0b-…", …],  // ids of sessions alive that day
         "ins": {                        // where the spend goes, in load units (tokens x weights)
           "units": 12.4, "ctx": {"lt50": 0.1, "50_100": 2.0, "100_150": 3.1, "gt150": 7.2},   // by the call's context size
           "effort": {"high": 200, "xhigh": 60},          // calls by effort level
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
     "budget": 527.6,                    // the window's budget including any promo; null if the plan is not calibrated
     "pct": 20.0,                        // units / budget x 100; null without a budget
     "fable_units": 83.9, "fable_budget": 246.6, "fable_pct": 34.0,   // Fable's separate scale
     "boost": 1.5,                       // promo multiplier for this window
     "estimated": false,                 // true = this plan has no reading of its own (so no percentage)
     "partial": true}                    // the window is still running
  ],

  "commits": {                         // day -> project -> [24] commits by hour (local git, authors from the settings)
    "2026-09-11": {"orbit-api": [0, 0, …, 3, …]}
  },
  "git": {"repos": 66, "authors": ["you@example.com", …]},

  "limits": {
    "calibrated": true,                  // is there a reading for the current plan
    "plan_now": "Max 5x",
    "budgets": {"Max 5x": {"all": 351.6, "fable": 164.6, "estimated": false}, "Pro": {…, "estimated": true}},
    "observations": [{"at": "2026-09-12 05:50", "pct": 20, "fable_pct": 34}],
    "week_reset": "2026-09-17 20:00"     // or null (weeks start Monday)
  },
  "idle_minutes": 10,
  "config_path": "~/.claude/usage-report/config.json"
}
```

Deriving your own numbers from `days`: hours for a project or group over a period is the sum of
`sec`; calls and tokens are the sum over `models`; sessions in a period is the union of `sessions`
(one session can span several days); the weekday × hour heatmap is the sum of `hours` bucketed by
the weekday of `d`. Note that `thinking` is already part of `output` — do not add them together.
