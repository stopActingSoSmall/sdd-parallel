---
name: prepare-wave
description: Provision isolated git worktree slots (track-a/b/c) and scope locks for wave K
syntax: specify prepare-wave --wave <k>
arguments:
  - name: wave
    description: Wave number to prepare (e.g. 1, 2)
    required: true
requirements:
  tools:
    - uv
    - git
---

# 🛠️ Spec Kit Command: /prepare-wave

Provision isolated `git worktree` slots for Wave `<k>` with context boundaries, track context files, and blast-radius locks.

## 🎯 Purpose & Behavior

1. **Pre-flight Gate:**
   Verifies that the working tree is clean and baseline checkpoints pass before spawning slots.
2. **Fixed Slot Allocation:**
   Spawns or resets fixed slots `.worktrees/track-a`, `.worktrees/track-b`, etc., without repository bloat.
3. **Shared Environments:**
   Links `.venv` and `node_modules` (NTFS junctions on Windows, symlinks on POSIX) for instant zero-copy setup.
4. **Context & Scope Boundary:**
   Generates `.tmp/track-context.md` in each slot with the assigned task specification, allowed write files, and forbidden spine files.
5. **Scope Guard Protection:**
   Generates `.parallel-lock.json` and configures local harness permissions (`permissions.blockWritesOutsideWorkingDirectories: true`) to strictly prevent cross-track contamination.

## 💻 Invocation

```bash
uvx sdd-parallel prepare --wave <k> --kit spec-kit
```

Example for Wave 1:
```bash
uvx sdd-parallel prepare --wave 1 --kit spec-kit
```

## 📋 Instructions for Agent

1. Execute `uvx sdd-parallel prepare --wave <k> --kit spec-kit`.
2. Review the printed per-track worker instructions:
   - For track A: `cd .worktrees/track-a && SPECIFY_FEATURE=<slug> /speckit.implement <task_id>`
   - For track B: `cd .worktrees/track-b && SPECIFY_FEATURE=<slug> /speckit.implement <task_id>`
3. Launch or guide workers in their isolated worktree slots.
4. When workers finish, proceed to `specify merge-wave --wave <k>`.
