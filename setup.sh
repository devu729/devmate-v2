#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# DevMate v2 — Mac/Linux quick-start
# Usage: chmod +x setup.sh && ./setup.sh
# ─────────────────────────────────────────────────────────
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  ____            __  __       _        "
echo " |  _ \\  _____   _|  \\/  | __ _| |_ ___ "
echo " | | | |/ _ \\ \\ / / |\\/| |/ _\` | __/ _ \\"
echo " | |_| |  __/\\ V /| |  | | (_| | ||  __/"
echo " |____/ \\___| \\_/ |_|  |_|\\__,_|\\__\\___|  v2"
echo -e "${NC}"
echo "Context-aware AI coding assistant — powered by DigitalOcean Gradient"
echo ""

# ── 1. Check for .env ────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}[1/3] .env not found — creating from .env.example${NC}"
    cp .env.example .env
    echo -e "${RED}      ⚠  Open .env and add your DO_GRADIENT_API_KEY before continuing.${NC}"
    echo "      Then re-run this script."
    exit 1
fi

source .env
if [ -z "$DO_GRADIENT_API_KEY" ]; then
    echo -e "${RED}[ERROR] DO_GRADIENT_API_KEY is empty in .env${NC}"
    echo "        Get your key at https://cloud.digitalocean.com/gradient"
    exit 1
fi

echo -e "${GREEN}[1/3] .env validated ✓${NC}"

# ── 2. Check for Docker ──────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo -e "${RED}[ERROR] Docker not found. Install Docker Desktop from https://docker.com${NC}"
    exit 1
fi

if ! docker info &>/dev/null; then
    echo -e "${RED}[ERROR] Docker daemon is not running. Start Docker Desktop and retry.${NC}"
    exit 1
fi

echo -e "${GREEN}[2/3] Docker detected and running ✓${NC}"

# ── 3. Build + start ─────────────────────────────────────────────────────────
echo -e "${CYAN}[3/3] Building and starting DevMate v2...${NC}"
docker compose up --build -d

# Wait for health check
echo -n "      Waiting for service to be healthy"
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    echo -n "."
    sleep 2
    if [ $i -eq 30 ]; then
        echo -e " ${RED}timed out${NC}"
        echo "Check logs: docker compose logs devmate"
        exit 1
    fi
done

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  DevMate v2 is live!${NC}"
echo -e "${GREEN}  → http://localhost:8000${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Paste any public GitHub URL to get started."
echo "  Stop with: docker compose down"
echo ""

# Try to open browser
if command -v open &>/dev/null; then
    open http://localhost:8000
elif command -v xdg-open &>/dev/null; then
    xdg-open http://localhost:8000
fi
