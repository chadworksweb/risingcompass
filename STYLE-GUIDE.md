# The Rising Compass — Style Guide

Developer reference. Every value is extracted from the live codebase.
Change any `--rc-*` variable in `:root` and it propagates everywhere.

---

## Color System

### Tier Colors (Chakra Rainbow)

| Tier | Variable | Hex | JS Key | Degree Range |
|------|----------|-----|--------|-------------|
| Ascended | `--rc-bright-green` | `#9933ff` (violet) | `bright_green` | 0°–22.5° |
| Elevated | `--rc-green` | `#3388ff` (blue) | `green` | 22.5°–67.5° |
| Decent | `--rc-yellow` | `#33cc55` (green) | `yellow` | 67.5°–112.5° |
| Degraded | `--rc-orange` | `#ffbb33` (yellow) | `orange` | 112.5°–157.5° |
| Corrupted | `--rc-red` | `#ff3333` (red) | `red` | 157.5°–180° |

These appear in: compass arcs, song dots, charge gradient, ghost trail, decade bars, trajectory chart, What If calculator, all score displays.

**JS mirroring**: `COLOR_HEX` maps exist in `app.js`, `compass.js`, and `charge.js`. If you change tier colors, update all three plus `:root`.

### Background Layers (darkest → lightest)

| Variable | Hex | Used For |
|----------|-----|----------|
| `--rc-bg-dark` | `#0a0a14` | Body, deepest elements (needle cap, charge point border) |
| `--rc-bg-panel` | `#12121e` | Cards, calc toggle, panels |
| `--rc-bg-card` | `#181828` | Nested cards, inputs, selects, tooltips, album cards, decade cards |
| `--rc-border` | `#2a2a3e` | All borders, dividers between sections |

### Text Colors

| Variable | Hex | Used For |
|----------|-----|----------|
| `--rc-text` | `#c8c8d8` | Body text, descriptions, default copy |
| `--rc-text-dim` | `#6a6a7e` | Secondary labels, metadata, positions, muted copy |
| `--rc-text-bright` | `#eeeef4` | Headings, card headers, emphasized text, song titles |
| `--rc-accent` | `#00d4aa` | Primary accent (teal). Links, active states, accented labels, buttons |

### Derived / Transparent Colors

| Variable | Value | Used For |
|----------|-------|----------|
| `--rc-divider` | `rgba(255,255,255,0.04)` | Subtle list dividers (song items, track items, archive items) |
| `--rc-white` | `#fff` | SVG needle fill, needle cap stroke |
| `--rc-accent-subtle` | `rgba(0,212,170,0.08)` | Accent button backgrounds, zero-contam badge bg |
| `--rc-accent-hover` | `rgba(0,212,170,0.15)` | Button hover backgrounds |
| `--rc-accent-active` | `rgba(0,212,170,0.1)` | Active play button background |
| `--rc-contam-bg` | `rgba(226,138,108,0.12)` | Contamination badge background |
| `--rc-contam-border` | `rgba(226,138,108,0.25)` | Contamination badge border |

---

## Typography

### Font Stacks

| Variable | Value | Used For |
|----------|-------|----------|
| `--rc-font` | `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif` | Body, headings, labels, UI text |
| `--rc-font-mono` | `'JetBrains Mono', 'Fira Code', 'Consolas', monospace` | Scores, dates, data values, positions, selects, textarea |

Google Fonts import: `Inter` (300, 400, 600, 700) + `JetBrains Mono` (400, 700)

### Font Sizes

| Size | Where Used |
|------|-----------|
| `0.5rem` | Compass tier labels (curved text) |
| `0.55rem` | Trajectory x-axis labels |
| `0.6rem` | Charge spectrum labels, speed button |
| `0.62rem` | Tooltip subtitle |
| `0.65rem` | Song contamination tag, calc tabs, site subtitle (mobile) |
| `0.68rem` | Decade meta, charge legend description, song count |
| `0.7rem` | Era calc header, nav tab (mobile) |
| `0.72rem` | Contam badge number, calc toggle button, line count, reset button |
| `0.75rem` | Card description, song position, track number, decade tier, archive charge, calc button, charge legend labels, footer |
| `0.78rem` | Song artist, decade breakdown, track assessment, What If label |
| `0.8rem` | Site subtitle, nav tab, song item default, charge label, era calc select, calc textarea, album back |
| `0.82rem` | Era calc labels/results, calc context, playlist result |
| `0.85rem` | Reading date, archive date/degree, album artist, error message, calc decade, What If stepper, What If count |
| `0.88rem` | Track item |
| `0.9rem` | Song title, methodology paragraphs |
| `0.95rem` | Reading editorial |
| `1rem` | Card header, compass label text, methodology h3, album card title |
| `1.1rem` | Decade name/score, calc score |
| `1.3rem` | Site title (tablet) |
| `1.6rem` | Compass score text (SVG) |
| `1.8rem` | Site title (desktop) |
| `2rem` | Contam number (mobile) |

### Font Weights

| Weight | Where Used |
|--------|-----------|
| `300` | Site title base |
| `400` | Body (default), charge legend descriptions |
| `600` | Site title accent span, card headers, charge legend labels |
| `700` | Compass score, compass label, decade name/score, calc score/tier, stepper buttons, track numbers, archive degrees |

### Letter Spacing

| Value | Where Used |
|-------|-----------|
| `0.01em` | Charge legend description |
| `0.04em` | Charge legend labels |
| `0.08em` | Card header, calc tab, decade tier, era calc label, What If group label, charge spectrum labels |
| `0.1em` | Nav tab, song contamination, archive charge, speed button |
| `0.12em` | Compass label text, compass tier labels, calc toggle button |
| `0.15em` | Site title, era calc header, methodology h3 |
| `0.3em` | Site subtitle |

### Line Heights

| Value | Where Used |
|-------|-----------|
| `1` | Contam badge number, What If count |
| `1.35` | Charge legend description |
| `1.4` | Card description, tooltips |
| `1.5` | Era calc result, reading editorial, decade breakdown, playlist result |
| `1.6` | Body (default), calc textarea |
| `1.7` | Methodology content |

---

## Border Radii

| Variable | Value | Used For |
|----------|-------|----------|
| `--rc-radius-xs` | `3px` | Speed button, progress bars, charge gradient |
| `--rc-radius-sm` | `4px` | Selects, archive items, stepper buttons, reset button |
| `--rc-radius-md` | `6px` | Tooltips, album cards, decade cards, mix result, textarea, calc button |
| `--rc-radius-lg` | `8px` | Main cards, calc toggle |
| `50%` | Circles: song dots, charge point, play buttons, slider thumbs (not variablized) |
| `10px` | Contam badge pill (not variablized) |

---

## Transitions & Animation

| Variable | Value | Used For |
|----------|-------|----------|
| `--rc-ease-fast` | `0.15s` | Stepper hover, archive hover, decade bar segments |
| `--rc-ease` | `0.2s` | Standard hover: tabs, buttons, tooltips, focus states |
| `--rc-ease-medium` | `0.35s` | Calc toggle body (close), chevron rotation |
| `--rc-ease-slow` | `0.5s` | Charge point color/shadow, decade bar segments |
| `--rc-needle-ease` | `1.5s cubic-bezier(0.34, 1.56, 0.64, 1)` | Compass needle (elastic overshoot) |

### Special Animations
- **Charge point slide**: `left 1s ease-out` (horizontal movement)
- **Progress bar fill**: `width 0.1s linear` (real-time scrub)
- **Calc toggle open**: `max-height 0.4s ease-in` (expand)
- **Calc toggle close**: `max-height 0.35s ease-out` (collapse)
- **Compass `.no-transition`**: Disables needle CSS transition during JS playback animation

### Ghost Trail (compass.js)
- Blur filter: `2px` on `.compass-ghost-trail`
- Opacity range: `0.04` (oldest) → `0.22` (newest)
- Glow halo at tip: `r=4`, `opacity * 0.6`
- Base spread angle: `0.03 radians`

---

## Layout

| Variable | Value | Used For |
|----------|-------|----------|
| `--rc-max-width` | `1200px` | Dashboard + secondary nav max-width |
| `--rc-card-padding` | `1.5rem` | Standard card padding |
| `--rc-gap-panel` | `1.5rem` | Panel column gaps |
| `--rc-gap-dashboard` | `2rem` | Dashboard flex gap |

### Dashboard Grid
- Left panel: `flex: 0 0 42%` (compass + charge + toggle)
- Right panel: `flex: 1` (tabs + content)
- Tablet (≤768px): stacks to single column

### Responsive Breakpoints
- **768px** — Tablet: columns stack, compass shrinks to 280px, nav tabs scroll horizontally, cards reduce padding
- **480px** — Phone: header tightens, site title shrinks to 1.1rem

---

## Shadows & Glows

| Variable/Value | Used For |
|----------------|----------|
| `--rc-shadow-needle` | `drop-shadow(0 0 4px rgba(255,255,255,0.5))` — Needle SVG glow |
| `--rc-glow-sm` | `0 0 6px` — Song dot glow (bright_green, red), slider thumb glow |
| Charge point glow | `0 0 10px ${hex}, 0 0 20px ${hex}` — set via JS |

---

## SVG Compass Geometry (compass.js)

| Constant | Value | Purpose |
|----------|-------|---------|
| `CX` | `180` | Center X |
| `CY` | `170` | Center Y |
| `R` | `130` | Arc radius |
| `ARC_WIDTH` | `18` | Stroke width of color bands |
| ViewBox | `0 -10 360 270` | SVG coordinate space |
| Arc span | `36°` each (180° / 5 tiers) | |
| Tick interval | `18°` (minor), `36°` (major) | |
| Needle width | `8px` (CX ± 4) | |
| Needle cap | `r=8` | |
| Score box | `96 × 38`, rx=4 | |
| Label box | `124 × 29`, rx=3 | |
| Label radius | `R + ARC_WIDTH/2 + 11` | Curved text path |
| Transform origin | `180px 170px` (= CX, CY) | Needle rotation pivot |

### Score Formula
```
score = Math.round((90 - degree) * 100 / 90)
```
- 0° → +100 (Ascended)
- 90° → 0 (Decent)
- 180° → -100 (Corrupted)

---

## Trajectory Chart (app.js)

| Value | Purpose |
|-------|---------|
| `W=320, H=120` | SVG dimensions |
| `padL=30, padR=10, padT=10, padB=22` | Chart padding |
| Line stroke: `2px`, round cap/join | Data line |
| Area fill: `opacity 0.15` | Under-line gradient |
| Dot: `r=3.5`, `stroke-width=2` | Year markers |
| Grid: `rgba(255,255,255,0.06)`, `stroke-width=1` | Horizontal grid lines |
| Gradient: `#9933ff → #3388ff → #ffbb33 → #ff3333` | Line + area gradient |
| X labels: decade marks + last year | '60, '70, ... '20, '23 |
| Hover line: `rgba(255,255,255,0.25)`, dashed `3 2` | Vertical crosshair |

---

## Contamination Badge

| State | Background | Border | Text Color |
|-------|------------|--------|------------|
| Active (>0) | `--rc-contam-bg` | `--rc-contam-border` | `--rc-red` |
| Zero | `--rc-accent-subtle` | `rgba(0,212,170,0.2)` | `--rc-accent` |

---

## Component Patterns

### Cards
```css
background: var(--rc-bg-panel);
border: 1px solid var(--rc-border);
border-radius: var(--rc-radius-lg);   /* 8px */
padding: var(--rc-card-padding);       /* 1.5rem */
```

### Nested Cards (decade cards, album cards, tooltips)
```css
background: var(--rc-bg-card);
border: 1px solid var(--rc-border);
border-radius: var(--rc-radius-md);   /* 6px */
padding: 1rem 1.2rem;
```

### Tab Navigation
```css
font-size: 0.8rem;          /* main nav: 0.8rem, calc tabs: 0.65rem */
letter-spacing: 0.1em;
text-transform: uppercase;
color: var(--rc-text-dim);   /* default */
color: var(--rc-accent);     /* active + hover target */
border-bottom: 2px solid transparent / var(--rc-accent);
transition: color var(--rc-ease), border-color var(--rc-ease);
```

### List Items (songs, tracks, archive)
```css
border-bottom: 1px solid var(--rc-divider);
padding: 0.7rem 0;          /* songs: 0.7rem, tracks: 0.5rem, archive: 0.8rem 1rem */
```

### Buttons (accent)
```css
border: 1px solid var(--rc-accent);
border-radius: var(--rc-radius-sm);
background: var(--rc-accent-subtle);
color: var(--rc-accent);
transition: background var(--rc-ease);
/* hover: */ background: var(--rc-accent-hover);
```

### Score Display (mono, bold, color-coded)
```css
font-family: var(--rc-font-mono);
font-weight: 700;
color: ${COLOR_HEX[tier]};  /* matches tier color */
```

---

## Z-Index Stack

| Value | Element |
|-------|---------|
| `1` | Contam badge |
| `5` | Trajectory tooltip |
| `10` | Contam tooltip |

---

## Playback Engine (app.js)

| Constant | Value |
|----------|-------|
| `BASE_SPEED` | `3` years/second at 1× |
| Speed options | `[0.5, 1, 2, 4]` |
| Year range | 1960–2023 (64 data points) |
| Position range | `0` → `data.length - 1` |
| Interpolation | Linear between adjacent year data points |
| Animation | `requestAnimationFrame` with delta-time accumulation |

---

## File Map

| File | Purpose |
|------|---------|
| `frontend/css/main.css` | All variables in `:root`, core component styles |
| `frontend/css/compass.css` | SVG compass gauge styles |
| `frontend/css/responsive.css` | Mobile breakpoints (768px, 480px) |
| `frontend/js/app.js` | Main app logic, calculator suite, trajectory chart, decade cards |
| `frontend/js/compass.js` | SVG compass rendering + needle animation |
| `frontend/js/charge.js` | Charge spectrum gradient bar |
| `frontend/js/contamination.js` | Contamination counter badge |
| `frontend/js/api.js` | API client (`/api/*` endpoints) |
| `frontend/index.html` | Single page: panels, nav tabs, section containers |
