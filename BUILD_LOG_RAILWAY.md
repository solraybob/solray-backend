# Solray AI — Railway Deployment Log

**Date:** 2026-03-21  
**Status:** ✅ GitHub push complete. Railway deployment manual steps below.

---

## What Was Done

### 1. Files Created/Updated

| File | Action | Notes |
|------|--------|-------|
| `Dockerfile` | Created | Python 3.11-slim, installs gcc + libpq for postgres |
| `railway.json` | Created | Dockerfile builder, restart on failure |
| `.railwayignore` | Created | Excludes .venv, __pycache__, *.db, .env |
| `.gitignore` | Created | Same exclusions + .env.local |
| `requirements.txt` | Updated | Added: anthropic, asyncpg, psycopg2-binary, email-validator |
| `db/database.py` | Updated | Now auto-detects postgresql:// vs sqlite:// and uses correct async driver |
| `api/auth.py` | Updated | JWT_SECRET env var (falls back to JWT_SECRET_KEY) |

### 2. Database URL Logic (db/database.py)

- If `DATABASE_URL` starts with `postgresql://` or `postgres://` → uses `postgresql+asyncpg://` 
- If `DATABASE_URL` starts with `sqlite://` → uses `sqlite+aiosqlite://`
- Default: SQLite at `./solray.db` (local dev)

### 3. Local Import Test

```
✅ python -c "from api.main import app; print('Import OK')"
```

### 4. GitHub Repository

- **Repo:** https://github.com/solraybob/solray-backend
- **Branch:** main
- **Commit:** b3bca92 — Initial deploy: Solray AI backend for Railway + Supabase PostgreSQL
- **22 files pushed**

---

## 🚀 Railway Deployment Steps (Manual)

### Step 1 — Go to Railway

👉 https://railway.app

Log in (or create account if you haven't).

### Step 2 — Create New Project

1. Click **"New Project"**
2. Select **"Deploy from GitHub repo"**
3. Authorize Railway to access your GitHub if needed
4. Select repo: **`solraybob/solray-backend`**
5. Railway will auto-detect the `Dockerfile` and build

### Step 3 — Add Environment Variables

In your Railway project, go to **Variables** tab and add:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | The Supabase pooled connection string. Get the current value from the Supabase dashboard, never from this doc. |
| `ANTHROPIC_API_KEY` | The current key from console.anthropic.com. |
| `JWT_SECRET` | A 64-character random string. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |

**SECURITY NOTE:** Earlier versions of this document committed the
literal Supabase password and JWT secret to git. Both values were
compromised the moment they were committed and have been rotated.
Never paste live credentials into this file. Always reference the
secrets manager. Codex P1.2 trust audit, May 2026.

### Step 4 — Deploy

Railway will build from the Dockerfile and start. First build takes ~2-3 minutes.

On startup it will:
1. Run `init_db()` — creates tables in Supabase if they don't exist
2. Start `uvicorn api.main:app` on `$PORT`

### Step 5 — Get your URL

Railway assigns a public URL like:  
`https://solray-backend-production-xxxx.up.railway.app`

Test it:
```bash
curl https://YOUR_URL.up.railway.app/
# Should return: {"status": "ok", "service": "Solray AI API", "version": "0.2.0"}
```

API docs available at:  
`https://YOUR_URL.up.railway.app/docs`

---

## Troubleshooting

### Build fails on `pyswisseph`
If Railway build fails on `pyswisseph`, the `gcc` + `libpq-dev` apt packages should cover it. If not, may need to add `build-essential` to the Dockerfile apt install.

### Database connection errors
The Supabase connection string uses the **pooler** endpoint (port 6543). asyncpg works with this. If you see SSL errors, add `?ssl=require` to the DATABASE_URL.

### Import errors
All dependencies are in `requirements.txt`. The local `.venv` is excluded via `.gitignore` and `.railwayignore`.

---

## Files NOT Pushed (Excluded)

- `.venv/` — local virtual environment
- `*.db` / `solray.db` — local SQLite database
- `secrets/` — any local secrets directory
- `.env` — local environment file
- `__pycache__/` — Python bytecode cache
