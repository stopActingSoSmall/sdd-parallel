# Contributing to SDD-Parallel

Thank you for your interest in contributing to `sdd-parallel`!

## Getting Started

### Prerequisites
- Python 3.11 or 3.12
- Git 2.30+
- [`uv`](https://docs.astral.sh/uv/) package manager (mandatory; direct `pip` usage is not recommended)

### Environment Setup
Clone the repository and install dependencies using `uv`:
```bash
git clone https://github.com/stopActingSoSmall/sdd-parallel.git
cd sdd-parallel
uv sync
```

## Adding a New SDD Adapter

To add support for a new Spec-Driven Development (SDD) framework or tool:
1. Implement the `SDDAdapterProtocol` defined in `src/sdd_parallel/adapters/__init__.py`.
2. Ensure your adapter implements:
   - `detect() -> bool`
   - `load_tasks() -> list[TaskNode]`
   - `reset_tasks_status(task_ids: list[str]) -> None`
   - `cleanup_wave_artifacts(wave_number: int) -> None`
3. Register your adapter in `get_adapter()` and `detect_sdd_kits()`.
4. Add comprehensive unit tests in `tests/test_multi_sdd_adapters.py`.

## Pre-PR Verification Gates

Every pull request must pass all four mandatory verification gates:

```bash
# 1. Full test suite (100% green)
uv run pytest

# 2. Ruff linter & formatter checks (0 errors)
uv run ruff check src/ tests/ scripts/

# 3. Strict type checking (0 errors)
uv run mypy --strict src/sdd_parallel

# 4. Standalone demo sanity check (<5s execution)
uv run sdd-parallel demo
```

## Commit Guidelines

We follow Conventional Commits formatting:
- `feat(...)`: New features or adapters
- `fix(...)`: Bug fixes
- `test(...)`: Adding or updating test cases
- `docs(...)`: Documentation improvements
- `refactor(...)`: Code refactoring without behavior changes
