#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Pull latest changes (optional)
git pull --rebase || true

source venv/bin/activate
set -a
[ -f .env ] && source .env
set +a

python bot.py
