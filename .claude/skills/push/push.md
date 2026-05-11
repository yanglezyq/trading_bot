---
description: Commit (if needed) and push changes to the remote repository
user-invocable: true
---

# /push

Commit any pending changes and push the current branch's commits to the remote repository.

## Steps

1. Run `git status` to check the current branch and whether there are uncommitted changes.
2. **If there are staged or unstaged changes (modified/untracked files):**
   - Invoke the `/commit` skill to commit the changes first.
3. Run `git log origin/<branch>..HEAD --oneline` to show which commits will be pushed. If the remote branch doesn't exist yet, show all commits on the current branch.
4. If there are no commits to push, inform the user and stop.
5. Confirm with the user before pushing, showing:
   - The branch name
   - The remote URL
   - The commits that will be pushed
6. Push using `git push -u origin <current-branch>`.
7. Report the result to the user.

## Rules

- Never force push (`--force` or `-f`) unless the user explicitly asks.
- Never push to `main` or `master` without confirming with the user first.
- If the push fails due to diverged branches, inform the user and suggest options (pull, rebase, etc.) instead of force pushing.
