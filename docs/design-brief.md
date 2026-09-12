# Brief for a design pass — "Claude Code usage"

**What this is.** One person's private dashboard: how many hours and tokens went into Claude Code,
the split between work and personal projects, weekly limit usage, when the work happens (weekday ×
hour), commits, token mix, and suggestions for spending less. Opened once a week and read for two or
three minutes: the tiles and the gauge first, then the charts, then the table at the end. Screen is
a 1280+ laptop, occasionally a phone.

**The attached file** is the real page with its real markup and a small slice of data. Everything
you see is drawn by its own script (SVG, no libraries), so:

## What to change — the look

- **The `:root` block at the top of `<style>`** is the only place colours and fonts live. The dark
  theme is the primary one; the light theme can stay secondary or be left as is.
- **The `<div class="bg">` layer** (empty, `position:fixed`, behind the page) is where a background
  belongs: a slow drift of two or three blurred lights, or a fine grid. Nothing abrupt, and switch
  the animation off under `prefers-reduced-motion: reduce`.
- **The gauge** (`figure.dial`) — arc, thickness, glow, the label under the number; tick marks are
  welcome.
- **Tiles** (`.tile`) and **cards** (`.card`) — radii, borders, shadows, padding. A card's heading is
  its drag handle (it carries a ⠿ mark); keep that.
- Data colours: the three project groups (`--s1`, `--s2`, `--s3`) must stay distinguishable under
  colour blindness; the ordinal ramp `--r1…--r4` is one hue from dark to light; the heatmap `--heat`
  is a single hue.

## What not to touch — the mechanics

- Everything inside `<script>…</script>`, including the line `const DATA = …` (that is the data; the
  build script substitutes it).
- Element `id`s and `class`es, the `data-span` attributes (card width in a six-column grid),
  `draggable`, and the `main > header + section.tiles + section.board + footer` structure.
- No external scripts: the page has to work as a local file and as a claude.ai artifact, where only
  Google Fonts are allowed.
- Do not translate the visible strings. `template.html` is the English original and `i18n/*.json`
  maps it to other languages by exact string match — reworded text silently drops out of every
  translation.

**Deliverable** — one whole HTML file, with the script and data left as they are. It is picked up
from there by `python -m claude_usage_dashboard --adopt file.html`.
