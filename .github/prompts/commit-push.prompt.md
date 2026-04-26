---
agent: agent
description: Stage all changes, commit with a message, and push to the remote. Use when you want to commit and push, git commit, or save changes to GitHub.
---

Commit all local changes and push to the remote. Follow these steps:

1. Run `git status` to show what will be committed.
2. Run `git diff --cached HEAD` (and `git diff` for unstaged) to review what changed.
3. Run `git add -A` to stage all changes.
4. Generate a concise commit message based on the changes and the conversation context in which this prompt was triggered. Use the conventional-commits style (`feat:`, `fix:`, `chore:`, etc.) where appropriate. Do not ask the user — infer the message from context.
5. Run `git commit -m "<generated message>"`.
6. Run `git push origin main` to push to the remote.
7. Report the commit message used and the result. If any step fails, stop and show the error.
