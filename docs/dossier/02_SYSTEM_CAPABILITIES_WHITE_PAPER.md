# 🏛️ SDD-Parallel — System Capabilities & Architectural White Paper

**Product:** `sdd-parallel` (`parallel-flow`)  
**Category:** Architectural & Algorithmic Capabilities White Paper  
**Version:** 0.1.0  
**Target Audience:** Second-tier abstraction document for architects, distributed systems researchers, and staff engineers. Explains the algorithmic model, codebase topology, isolation mechanisms, and semantic conflict prevention independent of language-specific implementation code.

---

## 1. Fundamental Conflict Model in Agentic Development

Unlike conventional version control systems that operate purely on textual lines of code, `sdd-parallel` formalizes two fundamentally distinct classes of conflicts arising during concurrent AI agent execution:

```mermaid
flowchart TD
    subgraph K1["Type-1 Conflict (Syntactic Collision)"]
        A1["Task A writes to File_X"]
        A2["Task B writes to File_X"]
        A1 --- A2
        C1["Git Merge Conflict: W(A) ∩ W(B) ≠ ∅"]
    end

    subgraph K2["Type-2 Conflict (Semantic / Blast Radius Collision)"]
        B1["Task A alters signature in File_X\n(ReturnType: str -> str | None)"]
        B2["Task B invokes method from File_X\n(inside its own File_Y)"]
        B1 -.->|Semantic Blast Radius I(A)| B2
        C2["Silent Failure: Git Merge succeeds, but mypy / pytest fail!\nW(A) ∩ I(B) ≠ ∅"]
    end
```

### 1.1. Type-1 Conflict ($W_A \cap W_B \neq \emptyset$)
Direct intersection of the file sets that tasks create or modify. If two concurrent tasks write to the same standard file, executing them concurrently across separate branches results in Git textual merge conflicts requiring manual intervention.

### 1.2. Type-2 Conflict ($W_A \cap I_B \neq \emptyset$ or $W_B \cap I_A \neq \emptyset$)
Intersection of the modified files of one task with the **semantic blast radius (Impact Radius)** of another task:
- *Example:* Task $A$ modifies a function signature or schema invariant in file $F_1$. Concurrently, Task $B$ implements a feature in file $F_2$, which imports and calls $F_1$.
- Both branches commit without syntactic Git conflicts. Local tests pass in both isolated branches.
- Upon merging, the integrated codebase encounters type incompatibilities or broken business logic invariants.
`sdd-parallel` is the first orchestration harness that renders this failure mode deterministically impossible at the planning phase.

---

## 2. Three-Dimensional Codebase File Taxonomy

Every production repository consists of heterogeneous files. Naive file-level locks ("one file, one exclusive lock") paralyze parallelism. `sdd-parallel` classifies repository files into three distinct architectural roles:

```text
               ┌────────────────────────────────────────────────────────┐
               │                    REPOSITORY FILES                    │
               └───────────────────────────┬────────────────────────────┘
                                           │
         ┌─────────────────────────────────┼─────────────────────────────────┐
         ▼                                 ▼                                 ▼
┌───────────────────┐             ┌───────────────────┐             ┌───────────────────┐
│    SPINE LOCK     │             │ ADDITIVE REGISTRY │             │  STANDARD MODULE  │
│   (Schema Core)   │             │ (Extension Point) │             │  (Business Logic) │
├───────────────────┤             ├───────────────────┤             ├───────────────────┤
│ DB schemas, models│             │ CLI commands,     │             │ Standard funcs,   │
│ IR, migrations,   │             │ MCP registries,   │             │ handlers, isolated│
│ canonical types   │             │ __init__.__all__  │             │ feature modules   │
├───────────────────┤             ├───────────────────┤             ├───────────────────┤
│ RULE:             │             │ RULE:             │             │ RULE:             │
│ Strictly serial   │             │ Concurrent edit   │             │ Disjoint writes   │
│ (ordered chain)   │             │ permitted (merge) │             │ $W_A \cap W_B=∅$, check $I(T)$│
└───────────────────┘             └───────────────────┘             └───────────────────┘
```

1. **`Spine Lock` (Schema Cores):**
   Files defining fundamental data contracts (database migrations, schema versioning, core IR models). Any mutation of a `Spine` file is structured by the scheduler into a strictly serialized dependency chain. Tasks within the same Spine chain are never scheduled in the same wave.
2. **`Additive Registry` (Shared Extension Hubs):**
   High-traffic integration points where independent features append isolated registrations (e.g., CLI command decorators, `__all__` symbol exports, MCP tool registries).
   `sdd-parallel` allows concurrent tasks to append changes to these files simultaneously, isolating them across worktrees and reconciling them at the wave merge barrier.
3. **`Standard Module` (Application Logic):**
   Standard business logic modules. Governed by the classical disjoint write constraint ($W_A \cap W_B = \emptyset$) and the semantic impact radius check ($I(T)$).

### 2.1. Algorithmic Role Detection Pipeline (`ASTHotspotScanner` — 3 Passes Without Hardcoding)

In strict adherence to **Invariant 7 (Anti-Overfitting & Invariant-First Architecture)**, `sdd-parallel` rejects hardcoded file lists (`views.py`, `routers/`, `db.py`). The architectural role of every module is computed by a 3-pass static AST analyzer (`src/sdd_parallel/ast_scanner.py`) without executing untrusted code:

| Pass | What is Analyzed in the AST | Constants, AST Nodes & Mathematical Criteria |
| :--- | :--- | :--- |
| **Pass 1: Signature Parsing** | Parse source text into `ast.Module` and detect schema versioning / migration markers | `_SPINE_KEYWORDS = ("SCHEMA_VERSION", "auto_migrate", "_migrate_v")` $\to$ sets `has_schema_version = True` |
| **Pass 2: Directed Import Graph & Decorators** | Traverse `ast.Import`, `ast.ImportFrom`, `ast.FunctionDef`, `ast.AsyncFunctionDef` | 1. Constructs `imports` edges and computes node in-degree (**Fan-In**: `len(meta.imported_by)`).<br>2. Inspects `decorator_list` for registration patterns: `_ADDITIVE_DECORATORS = ("command", "tool", "get", "post", "put", "delete", "patch", "include_router", "register", "route")`. |
| **Pass 3: Deterministic `FileRole` Attribution** | Hierarchical classification prioritizing safety (`SPINE > ADDITIVE_REGISTRY > STANDARD`) | 1. Test paths (`tests/`, `test_*.py`, `*_test.py`) $\to$ always `STANDARD`.<br>2. **`SPINE`**, if `has_schema_version == True` $\lor$ file name in `{"models.py", "schema.py"}` $\lor$ **topological centrality $\text{In-Degree} \ge 5$**.<br>3. **`ADDITIVE_REGISTRY`**, if `has_registry_decorators == True` $\lor$ entrypoint name in `{"cli.py", "__init__.py"}` $\lor$ `*/mcp/server.py`.<br>4. **`STANDARD`** — all remaining business logic modules. |

---

## 3. Static Analysis Engine & Weighted Blast Radius

To calculate the semantic blast radius $I(T_i)$ of a task, the orchestrator employs a hybrid pipeline:

1. **AST Hotspot Scanner:**
   Rapid static pass over the abstract syntax tree. Discovers class definitions, function boundaries, decorators, and identifies `Spine Lock` or `Additive Registry` files.
2. **Call Graph Sidecar (`code-review-graph v2.3.9` WAL):**
   Constructs a static symbol dependency graph connecting codebase symbols via typed directed edges:
   - `CALLS` (weight = 1.0)
   - `INHERITS` / `IMPLEMENTS` (weight = 0.9)
   - `TESTED_BY` (weight = 0.8)
   - `IMPORTS_FROM` (weight = 0.7)
3. **Impact Radius Decay Formulation:**
   The weighted blast radius $I(T)$ is computed as the transitive closure of reachable symbols with exponential distance decay across traversal depth $d$:
   $$\text{Score}(v) = \text{Score}(u) \cdot w(u, v) \cdot \gamma^d, \quad \gamma = 0.7$$
   Noise pruning is enforced with a strict threshold $\text{Score} \ge 0.5$ using direct symbol resolution (`resolution="direct"`), filtering out false-positive noise on common method names (`.get()`, `.close()`).

---

## 4. Deterministic BSP Wave Scheduler (Bulk Synchronous Parallel)

Parallel task allocation is not delegated to non-deterministic LLM heuristics. It is calculated by a deterministic graph scheduler:

```text
[Input Backlog / Sprint Tasks]
               │
               ▼
   [Conflict Graph Construction]
   - Explicit spec dependencies (EXPLICIT_DEP)
   - Spine serialization chains (SPINE_ORDER)
   - Direct write collisions (DIRECT_COLLISION)
   - Semantic blast-radius collisions (SEMANTIC_COLLISION)
               │
               ▼
   [Topological Sorting & Acyclicity Gate (Kahn / Tarjan)]
   (Guarantees 100% task coverage and zero deadlocks)
               │
               ▼
   [Wave Bin-Packing Under K-Track Constraint]
               │
               ▼
[Output: WavePlan — Deterministic Phased Execution Plan]
```

### Barrier Synchronization (BSP):
Execution proceeds across discrete barrier waves:
$$\text{Wave } 1 \longrightarrow \text{Merge Checkpoint } 1 \longrightarrow \text{Wave } 2 \longrightarrow \text{Merge Checkpoint } 2 \dots$$
Within a wave, tasks run concurrently in isolated environments. Transition to the subsequent wave is barred until the barrier verification gate passes.

---

## 5. Isolation Harness: Zero Worktree Pollution & Fixed Track Slots

Many parallelization attempts fail due to file tree contamination: temporary config injection causes verification commands (`git status --porcelain`) to fail, halting CI pipelines and agent workflows, while continuous worktree creation/deletion triggers `Missing Folder` exceptions in Electron desktop applications.

`sdd-parallel` solves this through **Zero Worktree Pollution & Fixed Track Slots**:
1. Allocates a parametric pool of permanent slots `track-<id>` ($K = \text{max\_tracks}$, default `track-a`, `track-b`, `track-c`).
2. Worktree slots reside in `~/.gemini/antigravity/worktrees/<Project>/<track_id>` with junction links in `.worktrees/<track_id>`, guaranteeing an $O(1)$ disk footprint and preventing IDE folder detachment.
3. Task context boundaries are injected exclusively into an isolated `.tmp/track-context.md` file.
4. Orchestrator manifests (`.parallel-lock.json`, user workflow overrides) are locally masked in `.git/worktrees/<name>/info/exclude`.
5. **In-Place Reset:** When transitioning between waves, slot directories are never deleted; they are atomically reset to master via `git checkout -B parallel/<track_id> master ; git clean -fd`.
6. **Result:** Inside every parallel worktree, `git status --porcelain` yields **100% empty output**. Agents and linters operate in an immaculate working tree.

---

## 6. Active Runtime Guard: Three-Tier Defense-in-Depth Scope Guard

Instructional constraints ("please do not edit out-of-scope files") are inherently unreliable: under refactoring pressure, LLMs frequently widen their edit radius, modifying neighboring files or bypassing tool locks by executing terminal scripts.

`sdd-parallel` enforces multi-tiered boundary security:
- **Tier 1 (PreToolUse Hook):** Intercepts direct IDE file manipulation tools (`write_file`, `edit_file`, `replace_file_content`). Prior to execution, target file paths are checked against `.parallel-lock.json`:
  - If included in `allowed_write_globs` or `additive_shared_files`, the operation proceeds.
  - If locked by a sibling track or classified as a Spine Lock, the call is rejected with a `BLAST RADIUS VIOLATION` error.
- **Tier 2 (Terminal Guard):** Intercepts shell execution tools (`Bash`, `run_command`, `cmd`, `powershell`). Commands are audited for write operations or destructive patterns (`>`, `>>`, `sed -i`, `rm`, `python open`) targeting files outside `allowed_write_globs`.
- **Tier 3 (OS Read-Only Locks):** At the operating system filesystem level, write access to sibling files is stripped (`chmod a-w`). Paired with `git config core.fileMode false`, this prevents out-of-scope disk modification without polluting Git index permissions.
- **Pre-Merge Diff Gate (`verify-wave`):** Final automated audit of multi-commit `git diff` against `merge-base` and `git status --porcelain` before integration. Prevents scope bypass across intermediate commits and fails the wave integration gate immediately upon unauthorized modifications.

### 6.1. Three Tiers of Defense: Cognitive vs. Execution vs. Kernel/VCS Level

During slot provisioning (`sdd-parallel prepare`), two distinct artifacts are written into each worktree: **`.tmp/track-context.md`** and **`.parallel-lock.json`**, complemented by OS-level file permissions. This enforces strict separation of security concerns:

| Defense Tier | Artifact / Mechanism | Consumer | Architectural Purpose (What Breaks Without It) |
| :--- | :--- | :--- | :--- |
| **1. Cognitive Tier (Prompt-Level)** | `.tmp/track-context.md` *(Markdown)* | LLM agent (via system prompt / persistent context) | **Token optimization & retry loop prevention.** The agent immediately observes its `Allowed Exclusive Write Scope` and `Forbidden Files` with human-readable rationale (`Locked by sibling track-b`). Without this file, the agent blindly attempts out-of-scope edits, encounters hook rejections, and exhausts token context on failed retries. |
| **2. Execution Tier (Hook-Level Gate)** | `.parallel-lock.json` *(JSON manifest)* | Python `PreToolUse` hook (`blast_radius_guard.py`) | **Deterministic protection against LLM hallucinations.** Prompt instructions alone fail in 5–12% of long multi-turn sessions. The hook validates `write_file`, `edit_file`, and shell execution arguments in `<2ms` against the manifest and issues a `DENY` **prior** to disk access. |
| **3. Kernel & VCS Tier (OS & Git Hygiene)** | `chmod a-w` (`FILE_ATTRIBUTE_READONLY`) + `git config --worktree core.fileMode false` | OS Kernel (NTFS / VFS) and Git index | **Subshell escape defense & `100% Clean Git Status`.** If a rogue script bypasses IDE hooks, the OS kernel throws an unhandled `PermissionDenied` / `UnauthorizedAccessException`. The worktree-local flag `core.fileMode = false` ensures read-only bit flips do not register as permission diffs (`100644 -> 100444`) in `git status`. |

---

## 7. Monotonic 3-Way Merger and LIFO Rollback Engine

In SDD environments, sprint state is persisted in repository files (`sprint-status.yaml`, `tasks.md`, `STATE.md`). When parallel agents concurrently transition stories, standard `git merge` produces fatal textual conflicts on adjacent lines.

### 7.1. Monotonic 3-Way Merger
`sdd-parallel` provides a dedicated domain-aware status resolver:
- Story statuses are ordered along a monotonic progression semi-lattice:
  $$\text{backlog} (0) < \text{ready-for-dev} (1) < \text{in-progress} (2) < \text{review} (3) < \text{done} (4)$$
- When reconciling track branches, each task's status resolves to the maximum progress rank:
  $$\text{Status}_{\text{merged}} = \max(\text{Status}_{\text{base}}, \text{Status}_{\text{ours}}, \text{Status}_{\text{theirs}})$$
- Comments, YAML structure, and timestamp headers are preserved cleanly with zero conflict markers.

### 7.2. LIFO Transactional Wave Rollback Engine
If a wave fails integration testing or requires rollback:
- The command `abort-wave --wave K` removes unmerged track branches and unlocks slots.
- The command `rollback-wave --wave K [--save-backup]` executes a safe, atomic rollback of the latest integrated wave:
  - Enforces strict LIFO ordering (only the most recently integrated wave can be reversed).
  - Automatically provisions an immutable backup branch `backup/wave-<K>-before-rollback-<timestamp>`.
  - Resets `master` to the pre-merge commit.
  - Polymorphically resets story statuses across SDD manifests back to `ready-for-dev` and removes generated intermediate specifications.

---

## 8. Execution Orchestration Topologies

The system adapts to various execution environments:

### Topology A: `Two-Stage Assisted (BMAD Native)` — Recommended Standard
- Overcomes the platform limitation in Google Antigravity (`Max Subagent Depth = 1`).
- The orchestrator calculates the wave schedule, provisions fixed track slots, and displays an operator launch guide via `sdd-parallel run-guide`.
- The human operator opens track slots across independent windows and executes native `/bmad-build <story-id>` commands at `Depth 0`, preserving the complete multi-lens review cycle.
- The orchestrator audits locks and executes barrier merges with full test suite validation.

### Topology B: `Multi-Window Autopilot` (Autonomous Swarm)
- The orchestrator provisions worktrees and invokes `AutopilotLauncher` to launch external worker instances (`antigravity`, `claude-code`, `codex`).
- Each worker runs as an independent `Depth 0` process with dynamic model selection via `Model Effort Routing` (`High` / `Medium` / `Low`).

### Topology C: `In-Session Flat Dispatch` (Deprecated / Fallback Only)
- Flat execution of nested subagents within a single chat session. Retained strictly for headless environments where independent CLI windows cannot be spawned, with full awareness of subagent depth constraints.
