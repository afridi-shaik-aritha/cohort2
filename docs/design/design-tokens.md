# Design Tokens — Anthropic-style visual system

This is the locked visual language for the whole app (Home + all 6 question pages).
Read this before writing any frontend code. Don't invent parallel styles per page —
extend this file if a genuine gap appears, and say so explicitly rather than silently
drifting.

## Voice of the design

Warm, calm, confident. Generous whitespace. Restrained color — one accent, used
sparingly. Soft, low-contrast borders instead of heavy drop shadows. Typography does
most of the work. Nothing neon, nothing glassy/dark-mode-startup, nothing dense or
cluttered. Content should feel like it's being presented on paper, not in a dashboard.

## Color

Use CSS custom properties (`:root`), with a `[data-theme="dark"]` override block.
Light mode is the default and primary experience.

```css
:root {
  /* Backgrounds */
  --color-bg: #FAF9F5;            /* warm off-white / cream, not pure white */
  --color-bg-raised: #FFFFFF;     /* cards, panels */
  --color-bg-subtle: #F3F1EA;     /* section dividers, hover states */

  /* Text */
  --color-text-primary: #1F1E1C;
  --color-text-secondary: #5D5A52;
  --color-text-muted: #8B8880;

  /* Accent — used sparingly: primary buttons, links, active nav, status "done" */
  --color-accent: #C15F3C;        /* warm terracotta/rust, not saturated red or blue */
  --color-accent-hover: #A94E2E;
  --color-accent-subtle: #F3E2D8; /* accent-tinted backgrounds, badges */

  /* Borders */
  --color-border: #E5E2D9;
  --color-border-strong: #D3CFC2;

  /* Semantic (use sparingly, only where meaning requires it) */
  --color-success: #4E7C59;
  --color-warning: #B8863B;
  --color-error: #B4432F;

  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;

  --shadow-card: 0 1px 2px rgba(31, 30, 28, 0.04), 0 1px 1px rgba(31, 30, 28, 0.03);
  /* Never use shadow beyond this weight. Borders carry separation, not shadow. */
}

[data-theme="dark"] {
  --color-bg: #1B1A17;
  --color-bg-raised: #232220;
  --color-bg-subtle: #29281F;
  --color-text-primary: #F2F0E9;
  --color-text-secondary: #B8B4A8;
  --color-text-muted: #7C7972;
  --color-accent: #E08A64;
  --color-accent-hover: #EBA487;
  --color-accent-subtle: #3A2A22;
  --color-border: #35332C;
  --color-border-strong: #45423A;
}
```

## Typography

- **Headlines**: a clean serif or high-quality humanist sans with some editorial
  character — e.g. `"Tiempos", "Georgia", serif` for a serif direction, or
  `"Styrene", "Inter", sans-serif` for a sans direction. Pick one and stay consistent.
  If neither is available/licensable, fall back to system serif (`Georgia, "Times New
  Roman", serif`) for headlines only — do not use a generic sans for headlines.
- **Body**: highly legible sans — `-apple-system, "Inter", "Helvetica Neue", Arial,
  sans-serif`.
- **Type scale** (rem, 1rem = 16px base):
  - Display (Home hero only): 2.75rem / 1.1 line-height / 500 weight
  - H1 (page title): 2rem / 1.2 / 500
  - H2 (section): 1.375rem / 1.3 / 500
  - Body: 1rem / 1.6 / 400
  - Small / meta: 0.875rem / 1.5 / 400
  - Code/mono (for traces, token counts, IDs): `"SF Mono", "Fira Code", monospace`,
    0.875rem
- Line length: cap body text containers at ~65–75ch for readability.

## Spacing

8px base unit. Use the scale: 4, 8, 12, 16, 24, 32, 48, 64, 96 (px). Page sections
get generous vertical rhythm — don't compress content to fit above the fold.

## Components

- **Cards** (Home question cards, result panels): `--color-bg-raised` background,
  1px `--color-border`, `--radius-lg`, `--shadow-card`, 24–32px internal padding.
  Hover: border shifts to `--color-border-strong`, no lift/scale animation — keep it
  calm.
- **Buttons**:
  - Primary: `--color-accent` bg, white text, `--radius-md`, no shadow, subtle
    background-color transition on hover to `--color-accent-hover`.
  - Secondary: transparent bg, 1px `--color-border-strong`, `--color-text-primary`
    text.
  - Never use pure black/white in flat blocks; everything is warm-toned.
- **Status badges** (Home card status): pill shape, `--radius-sm` full, small text,
  background = subtle tint of the semantic color, text = the semantic color itself.
  "Not built" = muted grey tint, "In progress" = warning tint, "Ready to test" /
  "Done" = accent or success tint (pick one consistently).
- **Nav / shell**: thin top bar, `--color-bg` background (blends with page, not a
  separate raised bar), logo/title left, minimal right-aligned nav (Home + maybe a
  theme toggle). No hamburger unless mobile width requires it.
- **Streaming/chat surfaces (Q1, Q3, Q6)**: message bubbles should be understated —
  user messages right-aligned with `--color-bg-subtle` fill, assistant messages
  left-aligned with no fill (just text), citations rendered as small inline pill
  references pointing to a source panel, not raw footnote numbers.
- **Code/trace panels (Langfuse-adjacent views)**: monospace, `--color-bg-subtle`
  background, generous padding, no syntax-highlighting circus — muted, one or two
  accent colors max for token/cost highlighting.

## Motion

Minimal. 150–200ms ease-out transitions on hover/focus states only. No page-transition
animations, no skeleton shimmer beyond a simple opacity pulse if needed for loading
states. The streaming token effect in Q1 is the one place continuous motion is
expected and desired — everywhere else, stay still.

## Accessibility baseline

- Body text contrast ≥ 4.5:1 against its background in both themes.
- All interactive elements have visible focus states (outline using `--color-accent`
  at 2px offset), not just hover states.
- Don't rely on color alone for status (pair badges with text labels, which this
  system already does).
