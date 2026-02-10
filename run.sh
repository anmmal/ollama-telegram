#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Optional: auto pull only if AUTO_PULL=1
if [ "$AUTO_PULL" = "1" ]; then
  git pull --rebase || true
fi

source venv/bin/activate
set -a
[ -f .env ] && source .env
set +a

python3 bot.py
