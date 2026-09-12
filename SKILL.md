---
name: claude-usage-report
description: Use when the user asks how much time or how many tokens Claude Code has spent (this project, all projects, this month), asks to refresh or publish the usage dashboard, gives a /usage percentage to calibrate the weekly limit, or says "how much have I spent on Claude", "token stats", "refresh the usage dashboard", "work vs personal split", "percent of the limit", "usage report".
---

# Claude Code usage report

Counts active hours, tokens, project split (three groups) and weekly-limit usage from the local
session logs (`~/.claude/projects/**/*.jsonl`); accumulates history so Claude Code's 30-day log
pruning does not erase anything; renders a dashboard and can publish it as an artifact.

| What | Where |
|---|---|
| Script | `usage_report.py` in this folder |
| Page template | `template.html` in this folder |
| Settings (groups, plan, readings) | `~/.claude/usage-report/config.json` |
| Daily history | `~/.claude/usage-report/history.json` |
| Dashboard for a browser / for an artifact | `~/.claude/usage-report/dashboard.html` / `dashboard.artifact.html` |
| The same data without a page | `~/.claude/usage-report/data.json` — fields in `docs/data-contract.md` |

## What to do

1. **Does the message contain a `/usage` reading** (claude.ai → Settings → Usage, or `/usage` in
   Claude Code)? Record it first — it is the only way to make the limit gauge real. You need the
   "All models" percentage, the "Fable" percentage (a separate scale), when the week resets, and
   whether a promo is running:
   ```bash
   python usage_report.py --observe 20 --fable 34 --reset "2026-09-17 20:00"
   ```
   The reading defaults to "now"; for a screenshot taken earlier add `--at "YYYY-MM-DD HH:MM"`.
   Promos ("+50 % weekly limits until …") go into `config.json` → `boosts` with dates; they apply
   both to calibration and to display. Budgets are calibrated **per plan**: a reading's plan is the
   one in force on its date (`plan_history`), and a plan with no reading of its own gets no
   percentage (shown as "?"). Rescaling "Pro = Max ÷ 5" was tested and does not hold.
2. **Rebuild:** `python usage_report.py` (add `--open` to open it in a browser). Scanning 100+
   sessions takes up to a minute.
3. **Publish the artifact** with the Artifact tool, using `~/.claude/usage-report/dashboard.artifact.html`.
   If `config.json` has an `artifact_url`, pass it as `url` to update the same page. If not, publish
   fresh (favicon 📊), then write the returned address into `config.json` → `artifact_url`.
4. **Answer with the script's own numbers:** active hours over 30 days, the three group shares and
   the work total, calls and output, limit usage — labelled "calibrated" or "factory guess". An
   uncalibrated limit is a placeholder; say so rather than presenting it as fact.

## Settings (`config.json`)

| Key | Why |
|---|---|
| `groups` | three groups by project-name pattern, first match wins: `core` = main work, `work` = work misc, `personal` = everything else |
| `plan_history` | plan by date: `[{"from": "2026-09-08", "plan": "max5"}]`; a week's plan is the one in force at its end |
| `observations` | `/usage` readings (`at`, `pct`, `fable_pct`); each plan's budget comes from its own readings |
| `week_reset` | the week's reset moment from `/usage`; windows are measured from it and a partial day is split along the activity profile. Without it, weeks start Monday |
| `boosts` | promos applied to the weekly budget: `[{"from", "to", "factor"}]` |
| `factory_weekly_units_pro` | the factory guess used while there are no readings at all |
| `token_weights` | per-model token weights (= API prices), used only for the limit |
| `git_roots`, `git_authors` | where to look for local repos (`.git` no deeper than `depth`) and whose commits count (patterns for `git --author`); this is the "GitHub activity" on the dashboard |
| `idle_minutes` | the gap after which time stops counting as active (10) |
| `lang` | dashboard language; `ru` needs `i18n/ru.json` |

## A look of your own (Claude Design)

Mechanics and looks are separated: every colour and font is in the first `<style>` block of the
template, `<div class="bg">` is reserved for an animated background, and the markup is semantic.

- **What to hand to Claude Design:** `~/.claude/usage-report/dashboard.design.html` (the same page on
  a small slice — 10 days, 4 projects, so the model does not rewrite tens of kilobytes of digits)
  plus the brief in `docs/design-brief.md`. Do not hand over the full `dashboard.html`.
- **Getting the result back:** compare it in three parts — `<head>` (styles), body, script — because
  a design pass often edits the script too. Read the whole diff, ignoring blank lines. If the sample
  has fallen behind the template, take the head and the changed functions and leave the rest.
- **What to do with it:** `python usage_report.py --adopt path/to/file.html` extracts the look,
  restores the hooks (`<!--BODY-->` and `const DATA = /*__DATA__*/null;`), saves the old template
  beside it as `template.bak.html`, and rebuilds. If the result breaks the charts, put
  `template.bak.html` back.
- A page from scratch goes on top of `data.json`; the fields are in `docs/data-contract.md`.
- Translations live in `i18n/<lang>.json` as a flat map from the template's English strings. After a
  design pass the script reports entries that no longer match, which are the ones to update.

## Running it unattended

Any scheduler works — cron, systemd timers, Windows Task Scheduler. Point it at
`python usage_report.py` daily so the history and the local dashboard stay fresh. The scheduler does
not publish the artifact; that is step 3, from a session.

## Pitfalls

- Claude Code deletes logs older than 30 days (`cleanupPeriodDays`). The history only preserves what
  it managed to see, so run it at least monthly.
- One API call is written as several lines sharing a `requestId`; the script already deduplicates —
  do not count lines another way.
- Anthropic does not publish limits in tokens; everything about the limit is a model on top of
  `/usage` readings. Fable is a separate scale with its own budget and, judging by the numbers, is
  also included in "all models" (inferred from how the figures reconcile, not from documentation).
- `/usage` inside Claude Code can show a stale plan (it reads the login token) — trust
  claude.ai → Settings → Usage, and run `/login` after changing plans.
- Personal projects are visible in the report — do not drop it into a work repository.
- Core checks: `python test_usage_report.py` in this folder.
