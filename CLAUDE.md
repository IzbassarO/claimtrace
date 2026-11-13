@AGENTS.md

## Claude Code specifics

- Subagent roles live in `.claude/agents/` (`developer`, `tester`). The lead (main session) owns
  `claimtrace/core/**`, `claimtrace/pipeline.py`, `pyproject.toml`, `uv.lock`, `docs/**` (except
  `docs/DEPLOY.md`), `README.md`, `AGENTS.md`, `CLAUDE.md`.
- Developers work in their own git worktree `.worktrees/feat/<task>` on `feat/<task>` and finish
  with an open PR. The lead reviews, reruns ruff + pytest, merges with `gh pr merge --merge`,
  removes the worktree, then runs a tester.
- Plan, contracts, task list: `docs/PLAN.md`, `docs/ARCHITECTURE.md`, `docs/TASKS.md`.
- Status log lives on the `status` branch (`docs/STATUS.md`), its own PR.
