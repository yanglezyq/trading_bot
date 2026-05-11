---
description: Push committed changes to the remote repository
user-invocable: true
---

# /push

Push the current branch's commits to the remote repository.

## Steps

1. Run `git status` to confirm there are commits to push and check the current branch.
2. Run `git log origin/<branch>..HEAD --oneline` to show which commits will be pushed. If the remote branch doesn't exist yet, show all commits on the current branch.
3. Confirm with the user before pushing, showing:
   - The branch name
   - The remote URL
   - The commits that will be pushed
4. Push using `git push -u origin <current-branch>`.
5. Report the result to the user.

## Rules

- Never force push (`--force` or `-f`) unless the user explicitly asks.
- Never push to `main` or `master` without confirming with the user first.
- If the push fails due to diverged branches, inform the user and suggest options (pull, rebase, etc.) instead of force pushing.
