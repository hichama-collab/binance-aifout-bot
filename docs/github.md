GitHub workflow (simple and safe)

Initial setup (one time)
- git clone <repo>
- cd <repo>
- ./scripts/setup-venv.sh

Daily update
- ./scripts/git-update.sh

Commit
- ./scripts/git-commit.sh "message"

Push
- ./scripts/git-push.sh

Rules
- Never commit .env (already gitignored)
- Never commit .venv
- Keep data/ as you prefer:
  - If you want logs local only, keep data/ in .gitignore
  - If you want empty folders tracked, keep them and ignore files inside
