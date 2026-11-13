@AGENTS.md

## Claude Code specifics

- Subagent roles live in `.claude/agents/` (`developer`, `tester`). The lead (main session) owns
  `claimtrace/core/**`, `claimtrace/pipeline.py`, `pyproject.toml`, `uv.lock`, `docs/**` (except
  `docs/DEPLOY.md`), `README.md`, `AGENTS.md`, `CLAUDE.md`.
- Developers work in their own git worktree on `feat/<task>` and finish with an open PR.
- Plan, contracts, task list: `docs/PLAN.md`, `docs/ARCHITECTURE.md`, `docs/TASKS.md`.
- Status log lives on the `status` branch (`docs/STATUS.md`), its own PR.
