# ⚡ SDD-Parallel Demo: Observing Parallel Agent Governance in 4 Seconds

<p align="center">
  <b>🌐 Language:</b>
  <b>English</b> |
  <a href="../i18n/zh-CN/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">简体中文</a> |
  <a href="../i18n/es/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Español</a> |
  <a href="../i18n/pt-BR/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Português (BR)</a> |
  <a href="../i18n/ja/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">日本語</a> |
  <a href="../i18n/ko/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">한국어</a> |
  <a href="../i18n/ru/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Русский</a>
</p>

**Version:** 0.1.0  

Running multiple AI coding agents (Claude Code, Google Antigravity, Codex) concurrently across a single repository can accelerate engineering velocity by 2x–3x. However, without deterministic coordination, it almost invariably results in three critical failure modes:

1. **Silent Contract Breakage (Semantic `Merge-Bomb`):** Agent A changes a function signature in a service, while Agent B concurrently writes a call to the old signature in a controller. Different files—`git merge` finishes without a single conflict, but the build explodes in production.
2. **Scope Hallucination (Scope Creep):** An agent working on a localized task decides to "improve" a database schema or neighboring module, corrupting concurrent workflows.
3. **Status File Collision Wars:** Two agents close their respective stories, modifying adjacent lines in a shared roadmap file (`sprint-status.yaml` or `STATE.md`). Standard `git merge` aborts with `<<<<<<< HEAD` conflict markers.

The **`sdd-parallel demo`** command allows you to **verify how `sdd-parallel` prevents all three failure modes in ~4 seconds on a real Git repository—offline, with zero API keys and zero setup**.

---

## 1. How the Demo Operates: Real Execution, Not Mocked Terminal Text

`uv run sdd-parallel demo` **is not a simulated text output**. Upon every execution, the engine performs 100% genuine disk operations:
- Initializes a real Git repository in your OS temporary directory (`Workspace: .../sdd-parallel-demo-<id>`);
- Parses actual Python files via Abstract Syntax Trees (`AST`);
- Provisions genuine isolated `git worktree` slots and applies OS-level filesystem locks;
- Creates authentic commits across two parallel track branches and executes a real `git merge --no-ff`.

The command supports targeted verification for any of the 5 supported SDD frameworks or matrix verification across all of them:

```bash
# Mode 1: Quick verification of default framework (BMAD)
uv run sdd-parallel demo

# Mode 2: Targeted SDD framework (bmad, spec-kit, gsd, openspec, beads)
uv run sdd-parallel demo --kit spec-kit

# Mode 3: Complete matrix verification (all 5 frameworks with summary scorecard)
uv run sdd-parallel demo --kit all

# Mode 4: Interactive Sandbox (leaves the repository on disk for manual exploration)
uv run sdd-parallel demo --keep-temp

# Mode 5: Fast Scaffold Sandbox (provisions a clean sandbox ready for manual E2E walkthrough)
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir "$env:TEMP/eval-speckit"
```

### Complete 5-Kit Matrix Verification Output (`--kit all`)

When running `uv run sdd-parallel demo --kit all`, the engine evaluates all 5 adapters sequentially and outputs a unified scorecard:

```text
============================================================================
5/5 SDD KITS VERIFIED (BMAD, SPEC KIT, GSD, OPENSPEC, BEADS)
============================================================================
Kit          | Merge-Bomb   | Worktrees    | Scope Guard  | 3-Way Merge 
----------------------------------------------------------------------------
bmad         | PASSED       | PASSED       | PASSED       | PASSED      
spec-kit     | PASSED       | PASSED       | PASSED       | PASSED      
gsd          | PASSED       | PASSED       | PASSED       | PASSED      
openspec     | PASSED       | PASSED       | PASSED       | PASSED      
beads        | PASSED       | PASSED       | PASSED       | PASSED      
============================================================================
```

### Sandbox Artifacts & Governance Matrix Across All 5 Frameworks

| Framework (`--kit`) | Generated Specification Files | Protected Contracts (`forbidden_files`) | 3-Way Monotonic Merge Target |
| :--- | :--- | :--- | :--- |
| `spec-kit` | `specs/001-telemetry/{spec.md, plan.md, tasks.md}` | `.specify/memory/constitution.md`, `.specify/feature.json`, `spec.md`, `plan.md` | `specs/001-telemetry/tasks.md` (`- [x]`) |
| `gsd` | `.planning/phases/01-core/*-PLAN.md` (XML `<files>`) | `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/PROJECT.md` | `.planning/STATE.md` (Post-Merge Hook) |
| `openspec` | `openspec/changes/<id>/{proposal.md, tasks.md}` | `openspec/config.yaml`, `openspec/specs/**`, sibling `changes/<sibling>/**` | `openspec/changes/<id>/tasks.md` (`- [x]`) |
| `beads` | `.beads/beads.jsonl` (typed `dependencies`) | `.beads/config.yaml` | `.beads/beads.jsonl` (JSONL `max(rank)`) |
| `bmad` | `_bmad-output/planning-artifacts/epics.md`, `spec-*.md` | `_bmad-output/**/epic-*-context.md` | `sprint-status.yaml` (`max(rank)`) |

> **What changes between runs?**  
> Only the randomized directory suffix in `Workspace:` and the elapsed verification time `VERIFIED IN X.XXs` (dependent on host disk speed, typically `2–4.5` s). The role classification tables, identified collisions, wave schedules, and merge outputs are **100% deterministic**, because the scheduler relies on graph mathematics rather than non-deterministic LLM generation.

---

## 2. Step-by-Step Anatomy: What Happens in Each Demo Stage

### Frame 0. The Starting Point (Initial State on Disk)

The temporary directory initializes with an initial commit (`chore: initial demo repository`), containing a 6-module telemetry application and a backlog structured according to the **BMAD** pattern:

```text
sdd-parallel-demo-<id>/
├── src/demo_app/
│   ├── __init__.py          # Package exports (__all__)
│   ├── schema.py            # DB schema & migrations (SCHEMA_VERSION = 1, auto_migrate())
│   ├── cli.py               # CLI registration hub (@app.command)
│   ├── normalizer.py        # Data cleaning: normalize_payload(raw)
│   ├── api.py               # HTTP endpoint: imports normalize_payload from normalizer.py!
│   └── exporter.py          # Event export: export_events(events)
└── _bmad-output/
    ├── planning-artifacts/          # Epics roadmap and compiled context documents
    └── implementation-artifacts/    # sprint-status.yaml + 5 story specifications (spec-*.md)
```

**Five queued stories (`ready-for-dev`) and three hidden architectural traps:**
- **`1-1`** *(Upgrade schema to v2)* $\to$ modifies `schema.py` (depends on `1-2`).
- **`1-2`** *(Normalize payload)* $\to$ modifies `normalizer.py` and registers command in `cli.py`.
- **`1-3`** *(CLI batch export)* $\to$ modifies `exporter.py` and also registers command in `cli.py` *(Trap #1: contention over shared `cli.py`)*.
- **`2-1`** *(Expose ingestion API)* $\to$ modifies only `api.py` *(Trap #2 — The Semantic Merge-Bomb: `api.py` imports a function from `normalizer.py`, which is being rewritten concurrently in `1-2`)*.
- **`2-2`** *(Add audit log schema)* $\to$ modifies `schema.py` (depends on `1-1`) *(Trap #3: double DB schema migration)*.

---

### Frame 1. AST Architectural Role Classification (`[Step 1/4]`)

```text
[Step 1/4] AST & Architectural Role Classification
----------------------------------------------------------------------------
File Path                              | Detected Role          | Importers
----------------------------------------------------------------------------
src/demo_app/__init__.py               | additive_registry      | -
src/demo_app/api.py                    | standard               | -
src/demo_app/cli.py                    | additive_registry      | -
src/demo_app/exporter.py               | standard               | -
src/demo_app/normalizer.py             | standard               | src/demo_app/api.py
src/demo_app/schema.py                 | spine                  | -
```

**What happened:**  
The engine inspects the codebase syntax tree (`AST`) without pre-configured file lists, determining the **concurrency profile** of each module:
1. **`spine` (Schema Core — Strict Sequence):** Files governing data contracts or carrying broad transitive dependencies (`SCHEMA_VERSION` markers, `models.py`, or in-degree $\ge 5$). Two tasks touching a `spine` file are **never** scheduled in the same wave.
2. **`additive_registry` (Additive Hub — Safe Append):** Modules where features only register exports or commands (`@app.command`, `@router.get`, `__all__`). Concurrent edits in parallel worktrees are explicitly permitted and reconciled at the merge checkpoint.
3. **`standard` (Business Logic — Mutual Exclusion + Dependency Tracking):** Standard application services. Concurrent edits to the same file are barred unless functional scopes are isolated, and reverse import relationships (**`Importers`**) are strictly tracked. Notice that the scanner detected `src/demo_app/api.py` importing `normalizer.py`!

---

### Frame 2. Defusing the Semantic Merge-Bomb & Wave Scheduling (`[Step 2/4]`)

```text
[Step 2/4] Type-2 Semantic Merge-Bomb Detection & BSP Wave Schedule
----------------------------------------------------------------------------
  Collision Analysis:
    - Naive Scheduler : W(1-2)={normalizer.py, cli.py} vs W(2-1)={api.py}
                        -> DISJOINT WRITES (False Safe!)
    - BSPWaveScheduler: Detected semantic_collision between '1-2' and '2-1'
                        -> Overlap W(1-2) ^ I(2-1) on: src/demo_app/api.py
    - Additive Registry: Tasks '1-2' and '1-3' both touch 'src/demo_app/cli.py'
                        -> Role: ADDITIVE_REGISTRY (Parallel allowed in Wave 1)

  Wave Schedule:
    * Wave 1
        - track-a: Story 1-2 -> writes: src/demo_app/normalizer.py (+ shared: src/demo_app/cli.py)
        - track-b: Story 1-3 -> writes: src/demo_app/exporter.py (+ shared: src/demo_app/cli.py)
    * Wave 2
        - track-a: Story 1-1 -> writes: src/demo_app/schema.py
        - track-b: Story 2-1 -> writes: src/demo_app/api.py
    * Wave 3
        - track-a: Story 2-2 -> writes: src/demo_app/schema.py
```

**Key Takeaways:**
1. **Caught Hidden Merge-Bomb Between `1-2` and `2-1`:**
   - A `Naive Scheduler` checks only write paths: `1-2` touches `normalizer.py`, `2-1` touches `api.py`. They appear disjoint (`DISJOINT WRITES -> False Safe!`).
   - `BSPWaveScheduler` calculates the **blast radius ($I$)**: since `api.py` imports `normalizer.py`, `api.py` falls within the impact zone of `1-2`. Detecting this `semantic_collision`, the scheduler schedules `1-2` in **Wave 1** and defers `2-1` to **Wave 2** (after the updated contract has merged).
2. **Preserved Useful Parallelism for `1-2` and `1-3`:** Both stories touch `cli.py`, but because it is an `ADDITIVE_REGISTRY`, they execute concurrently in **Wave 1** (`track-a` and `track-b`).
3. **Structured DB Migration Pipeline:** `1-1` and `2-2` mutate `schema.py` (`spine`), placing them sequentially across **Wave 2** and **Wave 3** without starvation.

---

### Frame 3. Fixed-Slot Worktree Provisioning & PreToolUse Scope Guard (`[Step 3/4]`)

```text
[Step 3/4] Fixed-Slot Worktree Provisioning & PreToolUse Scope Guard
----------------------------------------------------------------------------
  Provisioned Fixed Slots (100% Clean git status: True):
    - track-a (.worktrees/track-a)
    - track-b (.worktrees/track-b)

  PreToolUse Scope Guard Check (track-a -> 'src/demo_app/schema.py'):
    - Decision : DENY
    - Violation: File 'src/demo_app/schema.py' cannot be modified by Wave 1 / track-a (stories ['1-2'])
    - Reason   : Locked by Spine Chain invariant.
```

**Disk State Changes:**
- Fixed worktree slots are provisioned: **`.worktrees/track-a`** (branch `parallel/track-a`) and **`.worktrees/track-b`** (branch `parallel/track-b`). These slots are permanent across waves—resetting in place without IDE folder recreation.
- Boundary artifacts are injected into each slot:
  - `.parallel-lock.json` — machine-readable specification of permitted and locked files;
  - `.tmp/track-context.md` & `ACTIVE_TRACK.md` — context boundaries for the AI agent;
  - OS-level read-only permissions (`chmod a-w`) are placed on out-of-scope and spine files.
- **Why `100% Clean git status: True` matters:** Orchestrator metadata is masked via `.git/worktrees/<slot>/info/exclude`, and permission flags are ignored via `core.fileMode = false`. The agent encounters an **immaculate Git workspace**.
- **Guard Validation:** The demo calls `evaluate_tool_access()` against `src/demo_app/schema.py` inside `track-a`, reproducing an out-of-scope edit attempt. The hook inspects `.parallel-lock.json` and immediately issues a **`DENY`**.

---

### Frame 4. Barrier Checkpoint & Monotonic 3-Way YAML Merge (`[Step 4/4]`)

```text
[Step 4/4] Barrier Checkpoint & Monotonic 3-Way YAML Merge
----------------------------------------------------------------------------
  Merged Branches:
    - parallel/track-a
    - parallel/track-b

  Resolved sprint-status.yaml (0 conflict markers):
    - 1-2-normalize-telemetry-payload: done
    - 1-3-cli-batch-export: done
============================================================================
ALL 5 CORE INVARIANTS VERIFIED IN 3.21s
============================================================================
```

**Why Status Merge Wars Occur & How `sdd-parallel` Resolves Them:**
1. Prior to execution, `1-2` and `1-3` occupied adjacent lines in `sprint-status.yaml` as `ready-for-dev`.
   - Agent in `track-a` marked `1-2` as `done`.
   - Agent in `track-b` concurrently marked `1-3` as `done`.
2. Standard `git merge` evaluates changes in 3-line hunks, interpreting adjacent edits as a collision and outputting `<<<<<<< HEAD` markers.
3. **The Solution:** The **Monotonic 3-Way Merger (`status_merger.py`)** evaluates statuses along a progression semi-lattice (`backlog (0) < ready-for-dev (1) < in-progress (2) < review (3) < done (4)`), selecting $\max(\text{base}, \text{track-a}, \text{track-b})$. Both stories reconcile to `done` with **0 conflict markers**.
4. **Post-Merge Hygiene:** Read-only locks are restored to writable state, stale Wave 1 lock manifests are purged, and the transaction is committed to **`.parallel/state.json`**.

---

## 3. Interactive Hands-on Quest: Experimenting With Live Commands

Launch the demo with disk persistence:

```bash
uv run sdd-parallel demo --keep-temp
```

Copy the directory path printed on the `Workspace:` line (referred to as `<DEMO_DIR>`).

---

### Step 1. Inspect Merge History and Transaction Journal
```bash
git -C "<DEMO_DIR>" log --graph --oneline --all
```
*Observation:* You will observe the initial commit and two Wave 1 merge commits (`integrate track-a` and `integrate track-b`). Inspecting `<DEMO_DIR>/.parallel/state.json` reveals the recorded `pre_merge_commit` anchor.

---

### Step 2. Schedule Remaining Backlog Tasks (`plan`)
```bash
# 1. Inspect full 5-task roadmap (including completed 1-2 and 1-3):
uv run sdd-parallel plan --repo "<DEMO_DIR>" --include-done --dry-run

# 2. Plan the 3 remaining active stories (1-1, 2-1, 2-2):
uv run sdd-parallel plan --repo "<DEMO_DIR>"
```
*Result on Disk:*
- Planning artifacts in `_bmad-output/planning-artifacts/` remain **untouched**.
- `<DEMO_DIR>/_bmad-output/parallel-artifacts/` contains `execution-plan-parallel.md` (with `Effort: low/medium/high` tags) and `wave-plan.json`.
- Stories `1-1` and `2-1` are scheduled into the upcoming **Wave 1**.

---

### Step 3. Provision Fixed Slots for Next Wave (`prepare` & `run-guide`)
```bash
uv run sdd-parallel prepare --repo "<DEMO_DIR>" --wave 1
uv run sdd-parallel run-guide --repo "<DEMO_DIR>" --wave 1
```
*Result on Disk:*
- Existing slots `track-a` and `track-b` are reset in place to current `master`.
- Inside `.worktrees/track-b/`: `.parallel-lock.json` permits `src/demo_app/api.py` while explicitly locking `src/demo_app/schema.py` (`"Locked by sibling track-a"`).
- `run-guide` displays the operator cheat sheet for each terminal window.

---

### Step 4. Test Runtime Interception Under Pressure (`check-lock`)
```bash
# Test 1: Agent accesses assigned file (api.py)
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/api.py"

# Test 2: Agent attempts out-of-scope modification to schema.py
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/schema.py"
```
*Output:* Test 1 returns `{"decision": "allow"}` (exit code 0); Test 2 returns `{"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}` (exit code 2).

---

### Step 5. Abort an Unmerged Wave (`abort-wave`)
```bash
uv run sdd-parallel abort-wave --repo "<DEMO_DIR>" --wave 1
```
*Result:* Track slots are reset and unmerged wave manifests are safely removed without altering `master`.

---

### Step 6. Transactional LIFO Wave Rollback (`rollback-wave`)
To revert the merged Wave 1 (`1-2` and `1-3`):

```bash
uv run sdd-parallel rollback-wave --repo "<DEMO_DIR>" --wave 1 --save-backup
```
*Result:*
1. Preserves safety branch `backup/wave-1-before-rollback-...` (`git -C "<DEMO_DIR>" branch -a`).
2. Resets `master` to the commit preceding the Wave 1 integration.
3. Automatically resets task statuses in `sprint-status.yaml` from `done` back to `ready-for-dev`.

---

## 4. Scenario B: Full Greenfield E2E Walkthrough From Scratch

While Section 3 explored a pre-configured demo repository, this scenario demonstrates how Forward-Deployed and Staff Engineers can validate `sdd-parallel` end-to-end on a clean sandbox without writing 40 lines of boilerplate files manually.

Using **`sdd-parallel demo --scaffold-only --output-dir <path>`**, the engine seeds a real Git repository with authentic framework specifications and source modules in <1 second.

---

### Track A: GitHub Spec Kit Walkthrough (`--kit spec-kit`)

#### Step 1. Scaffold Sandbox and Inspect Starting State
```powershell
# 1. Provision a clean Spec Kit sandbox in an ephemeral directory
$SANDBOX = "$env:TEMP\eval-speckit"
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir $SANDBOX
Set-Location $SANDBOX

# 2. Inspect created files
git status
git log -1 --oneline
```
*Repository layout:*
- `specs/001-telemetry/{spec.md, plan.md, tasks.md}` — authentic GitHub Spec Kit task checklist (`- [ ] T001`, `- [ ] T002 [P]`, `- [ ] T003 [P]`, `- [ ] T004`, `- [ ] T005`).
- `.specify/memory/constitution.md` and `.specify/feature.json` — immutable project contracts.
- `src/demo_app/` — 6 Python modules with telemetry logic and hidden Type-2 semantic collision.

#### Step 2. Calculate Parallel Wave Plan (`plan`)
```powershell
uv run sdd-parallel plan
```
*What happens:*
- The scanner detects GitHub Spec Kit automatically via `specs/**/tasks.md` and `.specify/`.
- AST static analysis classifies `schema.py` as `SPINE`, `cli.py` as `ADDITIVE_REGISTRY`, and catches the semantic dependency between `api.py` and `normalizer.py`.
- Tasks `T002` and `T003` are packed into **Wave 1**, while `T004` is isolated into **Wave 2** to prevent the Semantic Merge-Bomb.

#### Step 3. Provision Fixed Track Slots (`prepare`)
```powershell
uv run sdd-parallel prepare --wave 1
```
*Result on disk:*
- Slots `.worktrees/track-a` and `.worktrees/track-b` are created and linked.
- `.parallel-lock.json` manifests are generated in both worktrees.
- Protected contracts (`.specify/memory/constitution.md`, `.specify/feature.json`, `spec.md`, `plan.md`) are locked with OS read-only permissions (`chmod a-w`).

#### Step 4. Test Defense-in-Depth Scope Guard & Crash Test
```powershell
# 1. Valid modification in track-a:
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/normalizer.py
# -> {"decision": "allow"}

# 2. Scope violation in track-a (attempting to touch schema.py locked by track-b):
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/schema.py
# -> {"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}

# 3. Direct OS-level crash test (attempting to mutate protected constitution.md):
Add-Content -Path .worktrees\track-a\.specify\memory\constitution.md -Value "unauthorized edit"
# -> Expected: System.UnauthorizedAccessException (OS Read-Only Lock enforced)
```

#### Step 5. Simulate Parallel Feature Commits
```powershell
# Agent in track-a implements task T002:
Set-Content -Path .worktrees\track-a\src\demo_app\normalizer.py -Value "def normalize_payload(raw): return {k.strip(): v.strip() for k, v in raw.items()}"
(Get-Content .worktrees\track-a\specs\001-telemetry\tasks.md) -replace '\[ \] T002', '[x] T002' | Set-Content .worktrees\track-a\specs\001-telemetry\tasks.md
git -C .worktrees\track-a commit -am "feat: complete task T002"

# Agent in track-b implements task T003:
Set-Content -Path .worktrees\track-b\src\demo_app\exporter.py -Value "def export_events(events): return len(events)"
(Get-Content .worktrees\track-b\specs\001-telemetry\tasks.md) -replace '\[ \] T003', '[x] T003' | Set-Content .worktrees\track-b\specs\001-telemetry\tasks.md
git -C .worktrees\track-b commit -am "feat: complete task T003"
```

#### Step 6. Verify Blast Radius and Execute Barrier Checkpoint Merge
```powershell
# 1. Audit diffs against .parallel-lock.json:
uv run sdd-parallel verify-wave --wave 1
# -> {"wave": 1, "compliant": true, "violations": []}

# 2. Execute barrier merge with 3-way monotonic status reconciliation:
uv run sdd-parallel merge --wave 1
```
*Observation:* Both `- [x] T002` and `- [x] T003` are reconciled in `specs/001-telemetry/tasks.md` with **0 Git conflict markers**.

#### Step 7. Transactional LIFO Wave Rollback
```powershell
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```
*Verification:*
- Safety backup branch `backup/wave-1-before-rollback-...` is created.
- `master` branch is reverted cleanly to the pre-merge commit.
- In `specs/001-telemetry/tasks.md`, tasks `T002` and `T003` are polymorphically reset back to unchecked `[ ]`.

---

### Track B: BMAD Method Walkthrough (`--kit bmad`)

For teams utilizing BMAD (`_bmad-output/`), the workflow remains identical with automatic framework adaptation:

```powershell
# 1. Scaffold BMAD sandbox:
$BMAD_SANDBOX = "$env:TEMP\eval-bmad"
uv run sdd-parallel demo --kit bmad --scaffold-only --output-dir $BMAD_SANDBOX
Set-Location $BMAD_SANDBOX

# 2. Plan and prepare:
uv run sdd-parallel plan
uv run sdd-parallel prepare --wave 1

# 3. Simulate worktree progress (updates sprint-status.yaml):
(Get-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-2-normalize-telemetry-payload: ready-for-dev', '1-2-normalize-telemetry-payload: done' | Set-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-a commit -am "feat: finish story 1-2"

(Get-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-3-cli-batch-export: ready-for-dev', '1-3-cli-batch-export: done' | Set-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-b commit -am "feat: finish story 1-3"

# 4. Barrier verify and merge:
uv run sdd-parallel verify-wave --wave 1
uv run sdd-parallel merge --wave 1

# 5. LIFO Rollback:
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```

---

### Framework Quick Reference: Key Differences Across All 5 Kits

| Step / Mechanism | `spec-kit` | `gsd` | `openspec` | `beads` | `bmad` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Scaffold command** | `--kit spec-kit` | `--kit gsd` | `--kit openspec` | `--kit beads` | `--kit bmad` |
| **Tasks format** | `specs/**/tasks.md` | `.planning/phases/*-PLAN.md` | `openspec/changes/*/tasks.md` | `.beads/beads.jsonl` | `sprint-status.yaml` |
| **Worker command** | `/speckit.implement <id>` | `/gsd-execute-phase <num>` | `/opsx:apply <id>` | `bd update <id>` | `/bmad-build <id>` |
| **Status progression** | `- [ ]` $\to$ `- [x]` | Phase completed $\to$ `STATE.md` | `- [ ]` $\to$ `- [x]` | `open` $\to$ `closed` | `ready-for-dev` $\to$ `done` |
| **Status merge target** | `tasks.md` (Markdown 3-way) | `.planning/STATE.md` | `tasks.md` (Markdown 3-way) | `beads.jsonl` (JSONL 3-way) | `sprint-status.yaml` (YAML 3-way) |
| **Protected contracts** | `constitution.md`, `spec.md` | `STATE.md`, `ROADMAP.md` | `config.yaml`, `specs/**` | `config.yaml`, `metadata.json` | `epic-*-context.md` |

---

## 5. Senior / Staff Engineer Guide: Diátaxis-lite Navigation

- 📐 **[System Capabilities & Architectural White Paper (`02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`)](02_SYSTEM_CAPABILITIES_WHITE_PAPER.md)** — Decay formulation for blast radius $I(T)$, Kahn acyclicity proofs, Three-Tier Scope Guard, and status lattice algebra.
- 🔌 **[Integration & Ecosystem Specification (`03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`)](03_INTEGRATION_AND_ECOSYSTEM_SPEC.md)** — Integration specifications for **BMAD**, **GitHub Spec Kit (`.specify/`)**, **GSD (`.planning/`)**, **OpenSpec**, and **Beads**, with `.parallel-lock.json` schema and MCP definitions.
- 📊 **Brownfield Verification & Case Studies** — Empirical validation of the Two-Stage Assisted workflow across complex multi-epic production repositories.
- 🛡️ **[Core Architecture & Invariants in `ARCHITECTURE.md`](../../ARCHITECTURE.md)** — Complete checklist of architectural guarantees.
