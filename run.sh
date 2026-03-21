#!/usr/bin/env bash
# ============================================================
# run.sh — Solray AI Local Dev Startup Script
# ============================================================
# Usage:
#   ./run.sh          — Install deps (if needed) + start server
#   ./run.sh --skip-install — Skip pip install, just run server
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"

# ---- Colours ----
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}║        🌟 Solray AI — Phase 2        ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════╝${NC}"
echo ""

# ---- Create venv if it doesn't exist ----
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Creating Python virtual environment...${NC}"
    python3 -m venv "$VENV_DIR"
fi

# ---- Activate venv ----
source "$VENV_DIR/bin/activate"

# ---- Install dependencies ----
if [ "$1" != "--skip-install" ]; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    echo -e "${GREEN}✓ Dependencies installed${NC}"
fi

# ---- Set environment variables (dev defaults) ----
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-solray-dev-secret-change-in-production-please}"
export TOKEN_EXPIRE_HOURS="${TOKEN_EXPIRE_HOURS:-720}"
export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///$(pwd)/solray.db}"

echo ""
echo -e "${GREEN}Starting Solray AI API...${NC}"
echo -e "  DB:   ${DATABASE_URL}"
echo -e "  Docs: http://localhost:8000/docs"
echo ""

# ---- Start server ----
exec uvicorn api.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --reload-dir api \
    --reload-dir db
