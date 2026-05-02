# Design system

Read `AGENTS.md` first.

---

## Philosophy

Living By Design. Japanese-bones design. Function or beauty as the only two reasons anything exists in the product. If a UI element is neither functional nor beautiful, remove it.

`ma`: the space between elements is the design. Don't fill it.
`wabi-sabi`: imperfection is permitted; polish is required.
Never let a sharp pixel-level decision go unmade.

---

## Color tokens

All colors live as RGB-triplet CSS variables in `app/globals.css`, theme-aware via `[data-theme="light"]` override. Tailwind tokens consume them via `rgb(var(--rgb-x) / <alpha-value>)`.

Never write a hex code in a component. Use the token. Theme switching breaks otherwise.

Available tokens (from `tailwind.config.ts`):

- `forest-deep`, `forest-dark`, `forest-card`, `forest-border` — base surfaces
- `amber-sun` — single hero accent for primary CTAs and active states
- `ember`, `moss`, `mist`, `indigo`, `wisteria`, `pearl` — aged-pigment categorical palette, all desaturated to harmonize with forest + amber
- `text-primary`, `text-secondary`, `text-muted` — copy hierarchy

Opacity composes via Tailwind `/N` syntax: `bg-forest-deep/40`, `text-mist/60`, `border-indigo/30`. Use this rather than separate `opacity` classes when adjusting alpha on a single color.

---

## Typography

Two families:

- `font-heading`: Cormorant Garamond, Georgia, serif. Display copy and italic emphasis. Almost always weight 300, occasionally weight 400 for headers.
- `font-body`: Inter, system-ui, sans-serif. UI copy, eyebrows, buttons.

Type scale (post-May 2026 readability bump):

Tailwind preset overrides in `tailwind.config.ts`:
- `text-xs` = 14px (was 12px)
- `text-sm` = 15px (was 14px)
- `text-base` = 17px (was 16px)
- `text-lg` and up: unchanged

Bracketed pixel sizes still in use across components, in current units:
- `text-[11px]` — micro labels (rare)
- `text-[12px]` — eyebrows, tracked uppercase (was 10px before bump)
- `text-[13px]` — meta, status text
- `text-[14px]` to `text-[15px]` — small body, detail rows
- `text-[16px]` and `text-[17px]` — generous body
- `text-2xl`, `text-3xl`, `text-5xl` — section and page headers (Cormorant)

Tracking on uppercase eyebrows: `tracking-[0.3em]` for the most refined feel; `tracking-[0.18em]` to `tracking-[0.22em]` for slightly tighter use.

Don't shrink text below the current floor. The May 2026 bump was driven by older users, outdoor reading, and accessibility concerns. Reverting any of it requires explicit Bob sign-off.

---

## Layout primitives

- Page padding: `px-6 pt-20 pb-32` is the standard for a content page (top space lets the page header breathe; bottom space sits above the fixed BottomNav).
- Cards: `rounded-sm` (not full rounded), `bg-forest-card` or `rgba(10, 31, 18, 0.6)` over the deep background, with a thin `border-forest-border` or amber-tinted accent border.
- Buttons: `rounded-full` for primary CTAs, `py-4 px-8`, `text-[11px] tracking-[0.3em] uppercase`. Amber-sun background with deep-forest text for primary; transparent with secondary-color border for cancel/dismiss.
- Animations: `animate-fade-in` (0.5s), `animate-slide-up` (0.4s), `animate-pulse-slow` (3s). All defined in `tailwind.config.ts`. Don't add new ones unless something is genuinely missing.

---

## Spacing rhythm

The app uses generous vertical rhythm. Sections breathe. `mb-10`, `mb-14`, `mt-8` are common. Don't compress.

Inside a card, content stack uses `space-y-3` to `space-y-4` between detail rows. Eyebrow-to-content spacing is `mb-5` to `mb-6`.

If a layout feels cramped, the answer is almost always more space, not smaller text.

---

## Iconography

- Bottom navigation: lucide-react icons, weight `strokeWidth={1.5}`, color `text-text-secondary` for inactive, `text-amber-sun` for active.
- Inline icons in buttons: same library, smaller size, `w-4 h-4`.
- Custom SVGs in `components/`: `NatalWheel.tsx`, `BodyGraph.tsx`, `AstroGeography.tsx`, `LunarPhaseCard.tsx`, etc. Match the aged-pigment palette; never use Tailwind defaults like `text-blue-500`.

---

## Motion

Sparing. The app shouldn't feel "animated"; it should feel calm. Use motion only where it earns its weight:

- Page entry: `animate-fade-in` on top-level page wrappers.
- Card entry: occasional `animate-slide-up` on stagger-loaded content.
- State changes: 200ms transitions on color, opacity, transform.
- Loading: `animate-spin` on small accent-colored ring.

Never bouncy. Never elastic. Never longer than 600ms unless you can defend it specifically.

---

## Theme switching

Light mode is supported via `[data-theme="light"]` on the root element. The CSS variables in `globals.css` flip; Tailwind tokens automatically use the new values. Components should use tokens (not hex) so they switch correctly.

If you write a new component with hex colors, light mode will not flip them and the component will look broken. There has been a hex sweep before; don't reintroduce hard-coded hex.

---

## What good looks like

The reference points for the aesthetic:

- Aesop product packaging — generous space, restrained typography, single accent
- Kinfolk magazine layout — Cormorant-style serif headings, generous gutter, calm imagery
- Apple HIG fundamentals — clear hierarchy, restrained chrome, type-driven information density

Not Co-Star (which is intentionally aggressive and design-led-as-statement). Not generic spiritual app (which is busy, soft-edged, and overly chromatic). Solray is closer to a quiet luxury brand than to a horoscope app.
