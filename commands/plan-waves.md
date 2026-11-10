---
name: plan-waves
description: Deterministic BSP wave scheduler for Spec Kit tasks and isolated git worktrees
syntax: specify plan-waves [--max-tracks <n>] [--routing-file <path>]
arguments:
  - name: max-tracks
    description: Maximum concurrent tracks per wave (default 3)
    required: false
  - name: routing-file
    description: Optional path to JSON routing overrides
    required: false
requirements:
  tools:
    - uv
    - git
---

# 🌊 Spec Kit Command: /plan-waves

Plan conflict-free parallel waves from `.specify/specs/*/tasks.md` (or `specs/*/tasks.md`) across isolated git worktree slots.

## 🎯 Purpose & Behavior

1. **Deterministic Discovery:**
   Discovers all user story tasks in `.specify/specs/**/tasks.md` or `specs/**/tasks.md`.
2. **Blast-Radius Analysis:**
   Scans codebase AST and Call Review Graph (CRG) to classify `Spine` files (e.g. schemas, migrations, central registries) and shared files.
3. **Safety Override:**
   Overrides hand-written `[P]` markers that share `Spine` or direct collision files, ordering them into sequential waves to guarantee zero merge-bombs.
4. **Offline & Zero LLM Overhead:**
   Runs completely offline with 0 LLM calls and 0 token burn.
5. **Manifest Generation:**
   Saves `.parallel/wave-plan.json` and outputs a formatted wave schedule.

## 💻 Invocation

```bash
uvx sdd-parallel plan --kit spec-kit
```

With custom tracks or routing overrides:
```bash
uvx sdd-parallel plan --kit spec-kit --max-tracks 3 --routing-file .parallel/routing-overrides.json
```

## 📋 Instructions for Agent

1. Verify the repository is clean (`git status --porcelain`).
2. Execute `uvx sdd-parallel plan --kit spec-kit`.
3. Inspect `.parallel/wave-plan.json` and summarize the wave tracks for the operator.
4. Recommend proceeding to Wave 1 via `specify prepare-wave --wave 1`.
