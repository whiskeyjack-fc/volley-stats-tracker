#!/bin/bash
set -e

cd ~/PlayerStats

# Record the current HEAD before pulling
OLD_HASH=$(git rev-parse HEAD)

git pull origin main

NEW_HASH=$(git rev-parse HEAD)

# Always reinstall dependencies to pick up any new packages
echo "Running pip install..."
workon volleystats
pip install -r requirements.txt

echo "DEPLOY_DONE"
