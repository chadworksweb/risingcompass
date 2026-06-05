# The Rising Compass — Style Guide

Developer reference. Every value is extracted from the live codebase.
Change any `--rc-*` variable in `:root` and it propagates everywhere.

---

## Aesthetic Regions

The site has three visually distinct rooms. Each has its own token
namespace and its own job; the visual separation is the point.

| Region | Tokens | Fonts | Mood | Where |
|--------|--------|-------|------|-------|
| **Dashboard / Compass** | `--rc-*` | Inter + JetBrains Mono | Dark instrument | `/`, `/library/`, `/songs/<slug>`, `/artists/<slug>` |
| **Tenets** | `--tn-*` (in `tenets/tenets.css`) | Cardo + Cormorant SC | Dark literary (the constitution) | `/tenets/` |
| **Deliberation Venue** | `--md-*` (motion-desk), `--am-*` (amendments) | Cardo + Cormorant SC | Cream literary (the chamber) | `/motion-desk/`, `/amendments/`, future `/chamber/` |
| **Utility / Form surface** | `--rc-*` remapped via `.rc-elevated` | Inter + JetBrains Mono | Dark instrument, lifted for contrast | `/dev/*` (Dev Ledger), form/utility pages (account onboarding, inquiry, misread, artist-claim) |

The deliberation venue and tenets are paired on purpose: same serifs,
same gravitas, opposite palettes. The framework stays dark; the room
where the framework gets argued over is cream. Walking from `/tenets/`
to `/motion-desk/` should feel like walking from the vault into the
hall, not changing websites.

The **Utility / Form surface** is not a separate room -- it is the same
dark dashboard palette with the surface, border, and text tokens lifted
for contrast, so forms and dev/utility info read clearly. It mirrors the
status page (`status.risingcompass.net`). Content/showcase pages (home,
`/songs/<slug>`, `/artists/<slug>`, `/library/`, `/tenets/`) deliberately
keep the standard (lower-contrast) dashboard palette -- the lift is only
for input- and data-dense utility surfaces. See **Utility / Form Surface
Palette** below.

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
| `--rc-text-dim` | `#c0c0ca` | Secondary labels, metadata, positions, muted copy |
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

## Deliberation Venue Palette (cream/brown)

Shared between `/motion-desk/` and `/amendments/` (and the future
Chamber). Two namespaces, identical values — `--md-*` lives in
`frontend/motion-desk/motion-desk.css`, `--am-*` lives in
`frontend/amendments/amendments.css`. Keep them in sync.

### Background + Ink

| `--md-*` / `--am-*` | Hex | Used For |
|---------------------|-----|----------|
| `bg` | `#f0e6cc` | Page background (warm cream) |
| `bg-soft` | `#e6dcbf` | Cards, panels, modal surface, motion items |
| `bg-input` *(--md only)* | `#f7efd8` | Form inputs, select, search results |
| `rule` | `#b09269` | Borders, tab underline, table dividers |
| `rule-soft` *(--md only)* | `#d4c39b` | Subtle row dividers inside results dropdowns |
| `ink` | `#3d2a18` | Body text |
| `ink-bright` | `#2a1c0e` | Headings, label uppercase chrome, input text |
| `ink-dim` | `#7a5a3a` | Italic copy, counters, meta, "optional" hints |
| `gold` | `#a07a2a` | Accent (deepened from `#c9a960` for cream contrast) |
| `accent` *(--md only)* | `#00a888` | Teal accent for verified badges and links |

The form section on motion-desk uses a slightly deeper cream
(`#e8ddc0`) plus a tan border to mark it as a discrete room. The
`.md-form-help` callout inside that goes one more shade deeper
(`#ddcfaa`) so it punches above the panel surface.

### Status Colors (deeper-than-dashboard, tuned for cream)

| Status | `--md-status-*` / `--am-*` | Hex | Used For |
|--------|----------------------------|-----|----------|
| filed / proposed | `filed` / `proposed` | `#966619` | New motions awaiting deliberation |
| in deliberation | `in-deliberation` / `deliberating` | `#1e5fb0` | Active chamber motions |
| ratified | `ratified` | `#1f8a3a` | Approved into the framework |
| covered | `covered` | `#6f5635` | Already addressed by an existing tenet |
| rejected | `rejected` | `#b02824` | Failed deliberation |

Used for: card left-stripes (`border-left: 3px solid var(--status-color)`)
and small uppercase status badges. Body text inside cards stays brown
(`--ink`) for legibility — the status color is decoration, not content.

### Hover Pattern (gold-outline → brown-fill)

CTAs and submit buttons in this region default to a gold-outlined
ghost button and fill to deep brown on hover (not gold-fill — gold
text on gold bg loses contrast on cream). Pattern:

```css
.md-submit, .md-gate-cta {
  background: transparent;
  border: 1px solid var(--md-gold);
  color: var(--md-gold);
}
.md-submit:hover, .md-gate-cta:hover {
  background: var(--md-ink-bright);
  color: var(--md-bg);
  border-color: var(--md-ink-bright);
}
```

---

## Utility / Form Surface Palette (high-contrast dark)

The dark dashboard palette, lifted for contrast on form- and data-dense
utility surfaces. Same `--rc-*` tokens, remapped within a `.rc-elevated`
scope so existing components inherit the lift automatically -- no
per-component restyling. Mirrors the status page so the dev/utility
surfaces read as one family.

**Where it applies:** `/dev/*` (Dev Ledger), and form/utility pages
(account onboarding, `/inquiry`, misread submission, artist-claim).
**Where it does NOT:** content/showcase pages (home, `/songs/<slug>`,
`/artists/<slug>`, `/library/`, `/tenets/`, methodology, calibration log)
keep the standard palette; the deliberation venue keeps its cream palette.

### Token remap (`.rc-elevated` scope)

| Token | Standard | Elevated | Why |
|-------|----------|----------|-----|
| `--rc-bg-dark` | `#0a0a14` | `#08080f` | deeper page floor |
| `--rc-bg-panel` | `#12121e` | `#191930` | lifted |
| `--rc-bg-card` | `#181828` | `#1f1f38` | clear card/page separation |
| `--rc-border` | `#2a2a3e` | `#3d3d5c` | brighter, defined edges |
| `--rc-text` | `#c8c8d8` | `#d2d2e0` | brighter body copy |
| `--rc-text-dim` | `#c0c0ca` | `#9c9cb6` | a true dim (standard was ~= body) |
| `--rc-text-bright` | `#eeeef4` | `#f4f4fa` | brighter headings |
| `--rc-accent` | `#00d4aa` | `#00e6b8` | more vivid teal |

Tier colors (green / blue / red) are unchanged -- they are semantic, not
surface chrome. One extra token ships with the scope:
`--rc-elevated-shadow: 0 1px 6px rgba(0,0,0,.35)` for lifting cards off
the page.

### Mechanism

Defined in `frontend/css/main.css`. Apply by adding `rc-elevated` to the
page `<body>`:

```html
<body class="rc-elevated">
```

Every descendant resolving `var(--rc-bg-card)`, `var(--rc-border)`, etc.
adopts the lifted value -- so the Dev Ledger and other utility pages get
the contrast without touching their component CSS. To pull a page out of
the treatment, drop the class; nothing else changes.

---

## Typography

### Font Stacks

| Variable | Value | Used For |
|----------|-------|----------|
| `--rc-font` | `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif` | Dashboard body, headings, labels, UI text |
| `--rc-font-mono` | `'JetBrains Mono', 'Fira Code', 'Consolas', monospace` | Scores, dates, data values, positions, selects, textarea |
| (not variablized) | `'Cardo', Georgia, 'Times New Roman', serif` | Tenets + deliberation venue body, italic prose |
| (not variablized) | `'Cormorant SC', serif` | Tenets + deliberation venue headings, labels, uppercase chrome |

Google Fonts imports:
- Dashboard: `Inter` (300, 400, 600, 700) + `JetBrains Mono` (400, 700)
- Tenets + Venue: `Cardo` (400 + italic + 700) + `Cormorant SC` (400, 500, 600, 700)

The serif pair is reserved for the constitutional / deliberation rooms.
Dashboard surfaces stay on Inter so the instrument feels mechanical;
the chamber stays on Cardo so the proceedings feel deliberate.

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

## Deliberation Venue Patterns

### Hero (page entry)
Centered, 720px max, three blocks stacked: title (Cormorant SC,
clamped 2.4-3.8rem, letter-spacing 0.16em), italic kicker (Cardo,
~1.3rem, ink-dim), preamble (Cardo, 1.05rem, left-aligned inside the
centered column). Used identically on motion-desk and amendments.

### Section Label
```css
font-family: 'Cormorant SC', serif;
font-weight: 600;
font-size: clamp(1.3rem, 2.2vw, 1.7rem);
letter-spacing: 0.18em;
text-transform: uppercase;
color: var(--md-ink-bright);
```

### Motion / Amendment Card
```css
background: var(--md-bg-soft);              /* cream-soft panel */
border: 1px solid var(--md-rule);
border-left: 3px solid var(--status-color); /* set inline per-row */
border-radius: 2px;
padding: 20px 22px;
```
Title in Cormorant SC at 1.2rem; meta in Cormorant SC at 0.78rem with
0.12em letter-spacing, uppercase, status-colored. Body in Cardo.

### Status Badge (inside card head)
```css
padding: 2px 8px;
background: var(--md-bg-input);
border: 1px solid var(--status-color);
color: var(--status-color);
border-radius: 2px;
font-family: 'Cormorant SC', serif;
font-size: 0.78rem;
letter-spacing: 0.12em;
text-transform: uppercase;
```

### Filing Form (cream-on-cream layered hierarchy)
- Panel: `#e8ddc0` with `1px solid var(--rule-soft)`.
- Help banner inside panel: `#ddcfaa` with `2px solid var(--gold)` left
  border.
- Form fields: `var(--bg-input)` (`#f7efd8`), border `var(--rule)`,
  focus border `var(--gold)`.
- Labels: Cormorant SC, 0.78rem, `letter-spacing: 0.16em`, uppercase,
  `color: var(--ink-bright)`.
- Live char counter (Cardo italic, 0.78rem, ink-dim) right-aligned in
  the label row; turns red (`var(--status-rejected)`) when under
  minimum.

### Gate Panel (auth state)
```css
max-width: 720px;
background: var(--md-bg-soft);
border: 1px solid var(--md-rule);
padding: 28px 26px;
```
Title (Cormorant SC, uppercase, 1rem, 0.16em letter-spacing) +
body (Cardo, 1rem) + ghost CTA (gold outline, brown fill on hover).
Renders different content for anonymous / handle-only / Tier 2 /
verified states.

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

### Dashboard region
| File | Purpose |
|------|---------|
| `frontend/css/main.css` | All `--rc-*` variables in `:root`, core component styles |
| `frontend/css/compass.css` | SVG compass gauge styles |
| `frontend/css/responsive.css` | Mobile breakpoints (768px, 480px) |
| `frontend/js/app.js` | Main app logic, calculator suite, trajectory chart, decade cards |
| `frontend/js/compass.js` | SVG compass rendering + needle animation |
| `frontend/js/charge.js` | Charge spectrum gradient bar |
| `frontend/js/contamination.js` | Contamination counter badge |
| `frontend/js/api.js` | API client (`/api/*` endpoints) |
| `frontend/index.html` | Single page: panels, nav tabs, section containers |

### Tenets region (dark literary)
| File | Purpose |
|------|---------|
| `frontend/tenets/index.html` | Constitution page markup (organ SVG + tenet chambers) |
| `frontend/tenets/tenets.css` | Dark theme, Cardo + Cormorant SC |
| `frontend/tenets/tenets.js` | Organ rendering, tenet population from `/api/tenets` |

### Deliberation venue region (cream literary)
| File | Purpose |
|------|---------|
| `frontend/motion-desk/index.html` | Motion Desk page markup |
| `frontend/motion-desk/motion-desk.css` | `--md-*` tokens (cream), filing form, record list, gate panel |
| `frontend/motion-desk/motion-desk.js` | Auth-aware filing UX, record fetch/render |
| `frontend/amendments/index.html` | Public amendment record page |
| `frontend/amendments/amendments.css` | `--am-*` tokens (cream), card styling, modal, status colors |
| `frontend/amendments/amendments.js` | Amendment log fetch + grouped status rendering |

### Shared infrastructure
| File | Purpose |
|------|---------|
| `frontend/js/auth.js` | Clerk wrapper + post-sign-in `returnTo` redirect, header-state sync |
| `frontend/js/comments.js` | Lobby comment widget (used inside song/artist pages) |
| `frontend/partials/header.html` | Brand mark + Sign in/Account link with bfcache + storage listeners |
| `frontend/account/index.html` | Sign-in, handle picker, profile, Verify Identity panel |
| `frontend/account/account.js` | Account state machine + `returnTo` plumbing |
