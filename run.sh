#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

git pull --rebase || true

# Restart LaunchAgent instead of running bot directly
launchctl kickstart -k gui/$(id -u)/com.ark.ollama-telegram || true
