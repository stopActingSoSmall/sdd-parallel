## Summary

Briefly describe the purpose and outcome of this pull request.

## Motivation & Context

Why is this change required? What problem or issue does it solve?
Fixes #(issue number)

## Affected Components

- [ ] Core Engine (`models.py`, `ast_scanner.py`, `scheduler.py`)
- [ ] SDD Adapters (`bmad`, `spec-kit`, `gsd`, `openspec`, `beads`)
- [ ] Worktrees & Track Slots (`worktree.py`)
- [ ] Hooks & Blast-Radius Guard (`hooks/`)
- [ ] CLI & Packaging (`cli.py`, `pyproject.toml`)
- [ ] Documentation & Translations (`docs/`, `README.md`)

## Mandatory Verification Gates

All pull requests must pass the four core quality gates:

- [ ] `uv run pytest` (100% passing test suite)
- [ ] `uv run ruff check src/ tests/ scripts/` (0 lint/formatting errors)
- [ ] `uv run mypy --strict src/sdd_parallel` (0 type errors)
- [ ] `uv run python -X utf8 -m sdd_parallel.cli demo --kit all` (all 5 adapters pass demo)

## Breaking Changes

- [ ] No breaking changes
- [ ] Yes (explain migration path below)
