---
description: Commit staged and unstaged changes with an auto-generated commit message
user-invocable: true
---

# /commit

Commit all current changes to git with a meaningful commit message.

## Steps

1. Run `git status` to see all changed and untracked files.
2. Run `git diff` to review the actual changes.
3. Run `git log --oneline -5` to understand the existing commit message style.
4. Analyze the changes and generate a concise, descriptive commit message following conventional commits format (e.g. `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`).
5. Stage all relevant changed files using `git add` with specific file names (do NOT use `git add .` or `git add -A`). Do NOT stage files that may contain secrets (`.env`, credentials, etc.).
6. Create the commit. Always use a HEREDOC for the commit message:

```
git commit -m "$(cat <<'EOF'
<type>: <description>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

7. Run `git status` after the commit to verify success.

## Rules

- If there are no changes to commit, inform the user and do nothing.
- Never amend existing commits unless explicitly asked.
- Never skip hooks (no `--no-verify`).
- Do not stage `.env`, credentials, or secret files.
