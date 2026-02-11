#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

git pull --rebase || true

source venv/bin/activate
set -a
[ -f .env ] && source .env
set +a

echo "Deploy ok (no bot start here)"
