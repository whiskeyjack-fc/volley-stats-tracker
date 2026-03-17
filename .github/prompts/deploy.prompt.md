---
agent: agent
description: Deploy the latest code to PythonAnywhere by running deploy.sh on the server and reloading the web app.
---

Deploy the latest code to PythonAnywhere using the REST API. Follow these steps exactly:

## Step 0 — Push local changes to GitHub
Before touching PythonAnywhere, ensure all local changes are pushed:

1. Run `git status` in the workspace root to check for uncommitted changes.
2. If there are uncommitted changes, run:
   ```
   git add -A
   git commit -m "deploy: auto-commit before deploy"
   git push origin main
   ```
3. If there are no uncommitted changes but the branch is ahead of `origin/main`, run:
   ```
   git push origin main
   ```
4. If there is nothing to push (branch is up to date), skip to Step 1.

If `git push` fails for any reason, stop and report the error to the user — do not proceed with the server deploy.

## Prerequisites
Load credentials from the `.env` file in the workspace root. Read the file and extract:
- `PA_API_TOKEN`
- `PA_USERNAME`
- `PA_DOMAIN`

If any value is missing or still set to the placeholder default, stop and tell the user to configure `.env` first.

## Step 1 — Find a running console
New consoles require a browser session to start and cannot be used immediately via API. Instead, list existing consoles and use the first one:

```
GET https://www.pythonanywhere.com/api/v0/user/{PA_USERNAME}/consoles/
Authorization: Token {PA_API_TOKEN}
```

Use the `id` of the first console in the returned array. If the array is empty, stop and tell the user to open a Bash console at pythonanywhere.com/user/{PA_USERNAME}/consoles/ and then retry.

## Step 2 — Run the deploy script
Send the deploy command to the console:

```
POST https://www.pythonanywhere.com/api/v0/user/{PA_USERNAME}/consoles/{console_id}/send_input/
Authorization: Token {PA_API_TOKEN}
Content-Type: application/json

{"input": "bash ~/PlayerStats/deploy.sh\n"}
```

## Step 3 — Poll until complete
Poll the console output every 3 seconds:

```
GET https://www.pythonanywhere.com/api/v0/user/{PA_USERNAME}/consoles/{console_id}/get_latest_output/
Authorization: Token {PA_API_TOKEN}
```

Continue polling until the output contains `DEPLOY_DONE`. If polling exceeds 120 seconds without seeing `DEPLOY_DONE`, stop and report the last output to the user as an error.

## Step 4 — Reload the web app
Once `DEPLOY_DONE` is detected, trigger the web app reload:

```
POST https://www.pythonanywhere.com/api/v0/user/{PA_USERNAME}/webapps/{PA_DOMAIN}/reload/
Authorization: Token {PA_API_TOKEN}
```

## Step 5 — Report outcome
Tell the user:
- Whether any local changes were committed and pushed
- Whether the server pull was a fresh update or already up to date
- Whether `pip install` was run (requirements.txt changed)
- That the web app has been reloaded and the changes are live
