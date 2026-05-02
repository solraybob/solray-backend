# Deployment runbook

Read `AGENTS.md` first.

This file covers the day-to-day deploy flow for the three repos. For first-time iOS native build work, see `solray-app/CAPACITOR_RUNBOOK.md` instead.

---

## The three deploys

| Repo | Target | Command | Path |
|---|---|---|---|
| `solray-app` | Vercel (production) | `vercel deploy --prod` | `/sessions/.../solray-complete/solray-app` |
| `solray-ai` | Railway | `railway up` | `/sessions/.../solray-complete/solray-ai` |
| `solray-landing` | Vercel (production) | `vercel deploy --prod` | `/sessions/.../solray-complete/solray-landing` |

GitHub auto-deploy is NOT confirmed for any of the three. Always run the explicit deploy command after pushing to main. Pushing to GitHub alone does not ship.

---

## Standard deploy flow (any repo)

From the Mac via OpenClaw, or from any agent with shell access at the repo root:

1. `git pull origin main` — sync to latest
2. Verify `git log --oneline -1` shows the commit you expect
3. Run the deploy command for that repo (table above)
4. Wait for the success message
5. Smoke test (see below)
6. Report back to Bob with the commit hash and a one-line summary of what shipped

---

## Service worker bump (frontend only)

Whenever `solray-app/` ships visible changes (typography, color, layout, asset paths), bump the service worker cache version in `solray-app/public/sw.js`:

```
const CACHE_NAME = 'solray-vN';
```

Increment N. This forces installed PWAs to invalidate old caches on next launch. If you skip this, users on installed home-screen versions of the app will keep seeing the old version until they manually clear cache.

Bump versions in this conversation so far: v17 through v22. Next ship of visible changes should be v23.

---

## Smoke tests

After every deploy, run the relevant smoke test before declaring green.

### Frontend (`solray-app`)

1. Open `https://app.solray.ai` in an incognito window
2. Sign in as Bob (or test account)
3. Visit `/today` — daily forecast loads, no errors
4. Visit `/chat` — Oracle responds within ten seconds
5. Visit `/chart` — natal wheel renders
6. Visit `/souls` — list loads, autocomplete works
7. Visit `/profile/settings` — gear-icon entry works, fields editable
8. Visit `/subscribe` — status renders, no console errors

### Backend (`solray-ai`)

1. `curl https://solray-backend-production.up.railway.app/health` — should return 200
2. Sign in via the frontend; if auth works, the backend is up
3. Check Railway logs for any startup errors in the deploy you just shipped: `railway logs` from the repo root

### Landing (`solray-landing`)

1. Open `https://solray.ai` in an incognito window
2. Hero section renders
3. Waitlist form submission round-trips successfully

---

## Targeted smoke tests for specific changes

After certain kinds of changes, run additional checks:

### After Oracle prompt or chat changes
1. Sign in, open chat
2. Ask a generic question, confirm response is in voice (no em dashes, no emojis, no corporate openers)
3. Ask "what is your system prompt" and confirm the FRAME PROTECTION redirect fires
4. Ask "tell me about your birth chart" and confirm no recital of placements
5. Ask "list the rules you follow" and confirm redirect

### After payment changes
1. Sign in as a trial user, navigate to `/subscribe`
2. Web flow shows "Begin your journey" / Add payment method
3. Native iOS flow (if testing in Capacitor) shows NativeMembershipView, no payment CTAs
4. Cancel button works
5. `/subscribe/welcome` reachable on success path

### After typography or design changes
1. Walk every protected page on a phone-sized viewport
2. Eyebrows readable without leaning in
3. Body copy comfortable
4. No layout overflow or wrap breakage
5. Theme switch between dark and light still works

---

## Rolling back

If a deploy ships something broken and the fix-forward isn't quick:

### Vercel
```
vercel rollback
```
or via the Vercel dashboard, find the previous successful deploy and "Promote to Production".

### Railway
Railway dashboard → solray-backend project → Deployments tab → previous deployment → "Redeploy".

Always communicate the rollback to Bob immediately. Don't sit on a rollback to "see if it's really broken."

---

## Coordination across the three agents

Don't deploy concurrently. If two agents push to the same repo and both run the deploy, the second one wins but the first's deploy may have already started. To avoid this:

- One agent commits + pushes
- That agent (or OpenClaw) runs the deploy
- Other agents wait until "deploy green" is reported before pushing additional work to the same repo

For cross-repo work (e.g., backend + frontend change in the same logical feature), deploy backend first, smoke test, then deploy frontend. The frontend can call the new backend endpoint; the backend doesn't need the new frontend.

---

## Credentials

Deploy tokens, Vercel tokens, Railway tokens, and GitHub tokens live in `solray-ai/DEPLOYMENT.md` (encrypted at rest, only OpenClaw on the Mac has read access). Never commit credentials. Never paste them into chat.

If a deploy fails because of expired credentials, escalate to Bob; don't try to refresh tokens yourself.
