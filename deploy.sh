#!/bin/bash
set -e

cd ~/PlayerStats

# Snapshot the database before pulling (safety net before any migration runs)
python backup.py

# Record the current HEAD before pulling
OLD_HASH=$(git rev-parse HEAD)

git pull origin main

NEW_HASH=$(git rev-parse HEAD)

# Only reinstall dependencies if requirements.txt changed
if git diff --name-only "$OLD_HASH" "$NEW_HASH" | grep -q "requirements.txt"; then
    echo "requirements.txt changed — running pip install..."
    ~/.virtualenvs/volleystats/bin/pip install -r requirements.txt
fi

echo "DEPLOY_DONE"
