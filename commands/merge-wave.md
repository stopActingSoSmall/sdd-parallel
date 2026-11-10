---
name: merge-wave
description: Barrier quality verification, 3-way tasks.md checklist merge, and slot teardown for wave K
syntax: specify merge-wave --wave <k> [--strict-gates]
arguments:
  - name: wave
    description: Wave number to merge (e.g. 1, 2)
    required: true
  - name: strict-gates
    description: Fail if any quality gate fails
    required: false
requirements:
  tools:
    - uv
    - git
---

# 🏁 Spec Kit Command: /merge-wave

Execute the Bulk Synchronous Parallel (BSP) barrier merge for Wave `<k>`.

## 🎯 Purpose & Behavior

1. **Barrier Checkpoint Gates:**
   Executes defined quality verification gates (`pytest`, `ruff`, `mypy`, or custom test scripts) across completed tracks.
2. **Monotonic 3-Way Status Merge:**
   Performs a monotonic 3-way merge on `.specify/specs/**/tasks.md` (or `specs/**/tasks.md`), advancing checkbox states (`- [ ]` -> `- [x]`) by maximum progress rank without conflict markers.
3. **Git Worktree Integration:**
   Merges track branches into the parent branch cleanly and records execution metadata into `.parallel/wave-<k>-manifest.json` and `.parallel/run-manifest.json`.
4. **Slot Reset:**
   Tears down worktree branches and resets slots for the next wave.

## 💻 Invocation

```bash
uvx sdd-parallel merge --wave <k> --kit spec-kit
```

Enforcing strict quality gates:
```bash
uvx sdd-parallel merge --wave <k> --kit spec-kit --strict-gates
```

## 📋 Instructions for Agent

1. Run `uvx sdd-parallel merge --wave <k> --kit spec-kit`.
2. If verification gates report failures, inspect `.parallel/wave-<k>-manifest.json` and fix issues in the offending track before retrying.
3. Upon clean merge, check status of remaining tasks in `tasks.md`.
4. If further waves exist, proceed to `specify prepare-wave --wave <k+1>`.
