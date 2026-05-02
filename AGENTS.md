# AGENTS.md

This file orients any AI agent (Claude Cowork, OpenClaw, Codex GPT, or future) joining the Solray team. **Read this first, then `.agents/` for depth.** The same file lives at the root of `solray-app/`, `solray-ai/`, and `solray-landing/` so it is reachable wherever you start.

---

## What Solray is

Solray is a subscription astrology / Human Design / Gene Keys app at $23/month. Two-week-old product, eleven paying subscribers, no ad spend, organic growth from one founder. Iceland-incorporated. Users get a daily personalised reading grounded in their exact birth chart, ongoing chat with their Higher Self (an AI Oracle), and compatibility readings between users (Souls feature).

The differentiation is the framework underneath. Bob, the founder, has authored six interlocking philosophy books synthesizing astrology, Human Design, Gene Keys, and the biological / photonic / material / movement layers of human experience. The Oracle is grounded in this framework, not in generic astrology training data. This is the moat. Protect it.

---

## The team

- **Bob** (kristjangilbert@gmail.com) — founder, author of the six books, editorial conscience, final design judgment, every strategic decision.
- **Claude (Cowork mode)** — long-context reasoning, multi-file refactors, Oracle prompt work, strategic conversations, web stack (Next.js + FastAPI). Operates in a sandbox; cannot run shell on Bob's Mac directly.
- **OpenClaw** — runs on Bob's Mac with full machine access. Executes deploys (`railway up`, `vercel deploy --prod`), iOS native build pipeline, Xcode operations, physical-device testing. Shares auto-memory with Claude through `/sessions/.../mnt/.auto-memory/`.
- **Codex GPT** — iOS native Swift work (WidgetKit widgets, ActivityKit Live Activities), code review on web-stack PRs from Claude. Different model family means different blind spots, useful for review.

---

## Editorial rules (HARD, no exceptions)

These are non-negotiable across every line of code, copy, prompt, commit message, comment, UI string, and response to Bob.

1. **No em dashes.** Anywhere. Use commas, periods, colons, or semicolons. The em dash is the single most common reason a piece of writing reads as AI-generated. There is a regex sanitiser in `solray-ai/ai/chat.py` (`_strip_em_dashes`) that strips them from Oracle output as a backstop, do not rely on it; never write one in the first place.
2. **No emojis.** Anywhere. UI, copy, code comments, commit messages, responses to Bob, push notification text. Zero exceptions.
3. **Never frame Solray as a correction of traditional astrology.** Don't say "not Mercury, but Ceres." Just use Solray rulerships naturally (Earth rules Taurus, Ceres rules Virgo).
4. **Never surface the six books in product or marketing.** They are internal framework context, never named in app UI, landing copy, chatbot output, App Store description, social media. Internal use only.
5. **No corporate or sycophantic register.** No "Great question," "Certainly," "Of course," "As your guide." No filler openings.
6. **No mock data in user-facing flows.** If something fails to load, surface that honestly. Never fall back to placeholder text that pretends to be real.

See `.agents/voice.md` for examples, including the dual-language pattern (traditional astrological term plus seasonal nature-based description).

---

## Voice and design

- **Living By Design.** Japanese-bones design philosophy: function or beauty as the only two reasons for anything to exist. If something is neither functional nor beautiful, remove it. Empty space is not missing content; it is breathing room (`ma`).
- **Wabi-sabi.** Imperfection is permitted, polish is required. Never let a sharp pixel-level decision go unmade.
- **Color system** lives in CSS variables in `solray-app/app/globals.css`, theme-aware via `data-theme="light"` override. Use Tailwind tokens (`forest-deep`, `amber-sun`, `ember`, `moss`, `mist`, `indigo`, `wisteria`, `pearl`, `text-primary`, `text-secondary`). Never hex codes outside the variables.
- **Type scale** bumped May 2026 for readability. Body and small text were too cramped on dark forest. Don't shrink below the current floor; specifically, the eyebrow tracked-uppercase style is now 12px, not 10px.
- **Oracle voice** is precise, warm, witness-not-prescriber. Translates astrological language into seasonal, nature-based description (Scorpio is the forest floor in October; Virgo is the discerning eye of late harvest). Never lectures. Never performs spirituality. Always grounded in the user's specific chart.

See `.agents/design.md` for the full design system reference.

---

## Architecture

Three repos under the GitHub org `solraybob`, all on `main` branch:

- **`solray-app/`** — Next.js 15 App Router, TypeScript, Tailwind. Capacitor wrapper for iOS / Android in `ios/` and `android/`. Deploys to Vercel via `vercel deploy --prod`. Service worker in `public/sw.js`; bump `CACHE_NAME` whenever frontend ships visible changes that need to invalidate installed PWA caches.
- **`solray-ai/`** — FastAPI + SQLAlchemy async + Postgres (SQLite in dev). Oracle prompt construction in `ai/chat.py`. Calculation engines in `astrology.py`, `human_design.py`, `gene_keys.py`. Deploys to Railway via `railway up`.
- **`solray-landing/`** — separate Next.js marketing site at solray.ai. Deploys to Vercel.

Live URLs: `https://app.solray.ai` (frontend), `https://solray.ai` (landing), `https://solray-backend-production.up.railway.app` (backend).

See `.agents/architecture.md` for code-path level overview.

---

## Deploy

Backend changes:
```
cd solray-ai && railway up
```

Frontend changes:
```
cd solray-app && vercel deploy --prod
```

Landing changes:
```
cd solray-landing && vercel deploy --prod
```

Service worker bump in `solray-app/public/sw.js`: increment `CACHE_NAME` whenever frontend ships visible changes (typography, layouts, assets).

GitHub auto-deploy is NOT confirmed for any of the three. Always run the explicit deploy command after pushing to main. Do not assume.

See `.agents/deployment.md` for the full runbook including smoke tests.

---

## Coordination

All three agents commit through GitHub PRs to `main` rather than direct push when feasible. OpenClaw on the Mac serializes merges where possible. Each agent has a primary surface and ventures into another's only when the work is bounded and clear.

Primary surfaces:
- **Claude**: web stack TypeScript and Python, Oracle prompts, strategic docs, refactors
- **OpenClaw**: deploy execution, iOS Capacitor build, native testing on physical device, Xcode workspace operations
- **Codex GPT**: iOS Swift (WidgetKit, ActivityKit), web-stack code review

Before starting work: pull latest from `main`. After committing: push immediately so other agents see your work. Don't sit on uncommitted changes for long stretches; that's where merge conflicts breed.

---

## Standing principles

- **Boil the ocean.** Ship the finished product, not a plan or steps. Do the whole thing. Never hand back work that can be done now.
- **Live app, live users, move.** Eleven paying subscribers can't wait. Read fast, commit to one fix, ship it. Don't stall to contemplate.
- **Look inward first.** When a problem, error, or pushback arrives, default posture is that the thing to fix is ours. Self-respect, not self-punishment.
- **God is in the details.** Polish is the work, not a finish step. Never hand-wave a micro-decision; pick the specific number and say why.

---

## Deeper reference

- `.agents/voice.md` — extended voice guide with positive and negative examples
- `.agents/design.md` — full design system, type scale, color tokens, layout patterns
- `.agents/deployment.md` — full deploy runbook with smoke tests
- `solray-ai/ai/chat.py` — Oracle system prompt construction (the framework lives here, do not move)
- `solray-app/CAPACITOR_RUNBOOK.md` — iOS native build runbook
- `HIVE_MIND_ARCHITECTURE.md` (in `solray-complete/` parent) — collective intelligence vision
