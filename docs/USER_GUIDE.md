# 📘 SDD-Parallel — Operator User Guide & Day-1 Runbook

<p align="center">
  <a href="../README.md">← Back to Landing Page</a> ·
  <a href="#1-harness-connection-recipes-local-slots-vs-antigravity-layout">1. Harness Setup</a> ·
  <a href="#2-sdd-project-layouts-before--after-all-5-frameworks">2. SDD Project Layouts</a> ·
  <a href="#3-interactive-wave-inspector--orchestration-modes-mode-a-vs-mode-b">3. Inspector &amp; Modes</a> ·
  <a href="#4-day-1-troubleshooting--recovery-runbook">4. Day-1 Runbook</a> ·
  <a href="dossier/README.md">Architecture Dossier</a>
</p>

> **Purpose:** Complete Day-1 reference for running `sdd-parallel` on real repositories after trying `uvx sdd-parallel demo`. Covers harness setup, all 5 Before/After SDD directory trees, Wave Inspector tabs, Assisted vs. Cyclic Autopilot modes, and recovery procedures.

---

## 1. Harness Connection Recipes (Local Slots vs. Antigravity Layout)

### 1.1. Layout Modes: Standard Local Slots (`.worktrees/`) vs. Antigravity Layout

By default, `sdd-parallel prepare --wave K` provisions reusable fixed slots inside your repository under `.worktrees/`. Use `--antigravity-layout` when running inside Google Antigravity so slots align with Antigravity's native workspace tree.

| Aspect | Default Local Layout (`.worktrees/`) | Antigravity Layout (`--antigravity-layout`) |
| :--- | :--- | :--- |
| **Command** | `sdd-parallel prepare --wave 1` | `sdd-parallel prepare --wave 1 --antigravity-layout` |
| **Slot Directory** | `<repo>/.worktrees/track-a`, `track-b`, `track-c` | `~/.gemini/antigravity/worktrees/<Project>/track-a`, `track-b`, … |
| **Git Branch** | `parallel/track-a`, `parallel/track-b`, … | `parallel/track-a`, `parallel/track-b`, … |
| **Reset Strategy** | In-place reset (`git checkout -B` + `git clean -fd`) | In-place reset (`git checkout -B` + `git clean -fd`) |
| **Environment Sync** | Symlinks `.venv` / `node_modules` into slots (zero pollution) | Symlinks `.venv` / `node_modules` into slots (zero pollution) |
| **Injected Boundaries** | `ACTIVE_TRACK.md`, `.parallel-lock.json`, `.tmp/track-context.md` | `ACTIVE_TRACK.md` (slot root), `.parallel-lock.json`, `.tmp/track-context.md` |
| **Clean `git status`** | Hidden via `.git/worktrees/<slot>/info/exclude` + `core.fileMode = false` | Hidden via `.git/worktrees/<slot>/info/exclude` + `core.fileMode = false` |

> **Legacy Dynamic Directories:** Pass `--dynamic-worktrees` to `prepare`, `verify-wave`, `merge`, `abort-wave`, or `rollback-wave` only if your workflow requires per-task ephemeral directories instead of fixed reusable slots.

### 1.2. Claude Code (`claude`)

1. Plan and provision Wave 1:
   ```bash
   sdd-parallel plan
   sdd-parallel prepare --wave 1
   sdd-parallel run-guide --wave 1
   ```
2. **Interactive mode:** open a terminal inside each slot and start Claude Code:
   ```bash
   cd .worktrees/track-a && claude
   ```
   Paste or run the native task command shown in `ACTIVE_TRACK.md` (for example `/speckit.implement T002` or `/bmad-build 1-2`). Out-of-scope edits are stopped by `permissions.blockWritesOutsideWorkingDirectories: true`, the `PreToolUse` + Terminal Guard hook, and OS `chmod a-w` locks.
3. **Autopilot mode:** spawn all Wave 1 tracks with model effort routing and cyclic review:
   ```bash
   sdd-parallel autopilot --wave 1 --harness claude-code --execute --policy cyclic --max-review-rounds 2
   ```

### 1.3. OpenAI Codex CLI (`codex`)

1. Provision slots and launch Codex targeting each slot directory:
   ```bash
   sdd-parallel prepare --wave 1
   codex --cd .worktrees/track-a
   codex --cd .worktrees/track-b
   ```
2. Read-only contracts and Spine files are protected at the OS layer via `chmod a-w`.
3. Before merging, audit all commits across slots against `.parallel-lock.json`:
   ```bash
   sdd-parallel verify-wave --wave 1
   sdd-parallel merge --wave 1
   ```
4. **Autopilot mode:**
   ```bash
   sdd-parallel autopilot --wave 1 --harness codex --execute
   ```

### 1.4. Google Antigravity (`agy`)

1. Install the portable Antigravity plugin (includes the `PreToolUse` scope-guard hook):
   ```bash
   sdd-parallel install-plugin
   ```
2. Provision Wave 1 using the Antigravity worktree layout:
   ```bash
   sdd-parallel prepare --wave 1 --antigravity-layout
   sdd-parallel run-guide --wave 1 --antigravity-layout
   ```
   `ACTIVE_TRACK.md` is placed directly at the root of `~/.gemini/antigravity/worktrees/<Project>/track-a`.
3. **Depth-0 Headless Wave Runner (`agy` CLI / SDK):**
   ```bash
   sdd-parallel autopilot --wave 1 --harness antigravity --antigravity-layout --execute --policy cyclic
   ```

### 1.5. Cursor, Windsurf, OpenCode, Kimi CLI, Aider & External Worktrees (`claim-track`)

Any worktree-capable CLI or IDE (**Cursor**, **Windsurf**, **OpenCode**, **Kimi CLI**, **MiniMax Code**, **Aider**, **Cline**, **Trae**) works with zero extra plugins:

1. **Standard workflow:** run `sdd-parallel prepare --wave 1`, open `.worktrees/track-a` and `.worktrees/track-b` as separate IDE windows, follow `ACTIVE_TRACK.md` in each root, and run `sdd-parallel verify-wave --wave 1` + `sdd-parallel merge --wave 1` at the barrier. OS `chmod a-w` protects locked files during execution, and `verify-wave` inspects `git diff merge-base...HEAD` before merge.
2. **External worktrees (`claim-track`):** if you already created a worktree manually or via an IDE agent launcher, bind Wave $K$ scope locks and `.parallel-lock.json` to it:
   ```bash
   sdd-parallel claim-track --wave 1 --track track-a --worktree /path/to/existing-worktree
   ```
3. **MCP Server (stdio JSON-RPC):** for Cursor, Windsurf, Claude Desktop, Antigravity, and OpenHands, `sdd-parallel` also exposes `sdd_plan_waves`, `sdd_prepare_track`, and `sdd_verify_and_merge` (see [Integration & Ecosystem Spec](dossier/03_INTEGRATION_AND_ECOSYSTEM_SPEC.md#42-model-context-protocol-mcp-server)).

### 1.6. GitHub Spec Kit Official Extension (`extension.yml`)

Install `sdd-parallel` directly as a first-class Spec Kit extension:

```bash
# Direct Zero-Wait installation (v0.2.1):
specify extension add --from https://github.com/stopActingSoSmall/sdd-parallel/archive/refs/tags/v0.2.1.zip

# Or via Community Catalog:
specify extension add sdd-parallel
```

Once installed, run the workflow via native Spec Kit slash commands:
- `/specify plan-waves` — deterministic parallel wave planning from `specs/*/tasks.md`
- `/specify prepare-wave --wave 1` — provisions isolated `track-a/b/c` worktrees and scope locks
- `/specify merge-wave --wave 1` — barrier gates, monotonic 3-way checkbox merge, and slot teardown

---

## 2. SDD Project Layouts: Before & After (All 5 Frameworks)

`sdd-parallel` uses read-only **Anti-Corruption Layer (ACL)** adapters to normalize all supported SDD formats into a unified `CanonicalTask` IR without modifying your specifications.

| SDD Framework (`--kit`) | Read-Only Artifacts (Locked in Slots) | Task Discovery | Status Synchronization | Native Worker Command |
| :--- | :--- | :--- | :--- | :--- |
| **GitHub Spec Kit** (`spec-kit`) | `.specify/memory/constitution.md`, `feature.json`, `spec.md`, `plan.md` | `specs/*/tasks.md` (`T001 [P]`) | 3-way Markdown checkbox merge (`- [x]`) | `/speckit.implement <id>` |
| **BMAD Method** (`bmad`) | `epic-*-context.md` (Zero Cache Invalidation) | `sprint-status.yaml`, `stories.yaml`, `epics.md`, `spec-*.md` | Monotonic 3-way YAML merge | `/bmad-build <id>` (or `/implement-spec`) |
| **GSD** (`gsd`) | `PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md` | `.planning/phases/*/NN-NN-PLAN.md` | Centralized `STATE.md` Sync on merge | `/gsd-execute-phase <id>` |
| **OpenSpec** (`openspec`) | `openspec/specs/**`, `project.md`, `config.yaml`, sibling change folders | `openspec/changes/<slug>/tasks.md` | 3-way Markdown checkbox merge (`- [x]`) | `/opsx:apply <id>` |
| **Beads** (`beads`) | `.beads/config.yaml` | `.beads/*.jsonl` (`bd-101` + `blocks` edges) | Line-wise 3-way semi-lattice JSONL merge | `bd update <id> --status in-progress` |

Every snapshot below comes from the real `sdd-parallel demo --scaffold-only` fixtures after `plan` + `prepare --wave 1`.

**Legend:** 🔒 read-only (`chmod a-w` + `PreToolUse` deny) · 👻 injected, hidden from Git via `.git/worktrees/<slot>/info/exclude` · ✍️ writable scope of the track.

### 2.1. GitHub Spec Kit (`.specify/` & `specs/*/tasks.md`)

**Before** — your project as-is:

```text
my-project/
├── .specify/
│   ├── memory/
│   │   └── constitution.md
│   ├── templates/
│   │   └── tasks-template.md
│   └── feature.json
├── specs/
│   └── 001-telemetry/
│       ├── plan.md
│       ├── spec.md
│       └── tasks.md
└── src/…
```

`specs/001-telemetry/tasks.md` — note the hand-written `[P]` on `T004`, which is unsafe next to `T002` because `api.py` imports `normalizer.py`:

```markdown
- [ ] T001 [US1] Upgrade schema in `src/demo_app/schema.py` (depends on T002)
- [ ] T002 [P] [US1] Normalize `src/demo_app/normalizer.py`, `src/demo_app/cli.py`
- [ ] T003 [P] [US1] Export `src/demo_app/exporter.py`, `src/demo_app/cli.py`
- [ ] T004 [P] [US2] Expose ingestion in `src/demo_app/api.py`
- [ ] T005 [US2] Add audit log in `src/demo_app/schema.py` (depends on T001)
```

**After** `sdd-parallel plan` + `prepare --wave 1`:

```text
my-project/
├── .parallel/                        # planner state (new)
│   ├── artifacts/
│   │   ├── execution-plan-parallel.md
│   │   └── wave-plan.json
│   ├── history/run-001-<timestamp>/wave-plan.json
│   └── state.json
├── .specify/…                        # untouched
├── .worktrees/
│   ├── track-a/                      # Wave 1 → T002
│   │   ├── .specify/memory/constitution.md   # 🔒 read-only
│   │   ├── specs/001-telemetry/
│   │   │   ├── plan.md               # 🔒 read-only
│   │   │   ├── spec.md               # 🔒 read-only
│   │   │   └── tasks.md
│   │   ├── src/…
│   │   ├── .tmp/track-context.md     # 👻 hidden via info/exclude
│   │   ├── .parallel-lock.json       # 👻 hidden via info/exclude
│   │   └── ACTIVE_TRACK.md           # 👻 → /speckit.implement T002
│   └── track-b/                      # Wave 1 → T003 (same layout)
├── specs/…                           # untouched
└── src/…
```

### 2.2. BMAD Method (`v6.3+ / v7` — `_bmad-output/`)

**Before:**

```text
my-project/
├── _bmad-output/
│   ├── implementation-artifacts/
│   │   ├── epic-1-context.md
│   │   ├── epic-2-context.md
│   │   ├── epics.md
│   │   ├── spec-1-1-upgrade-schema-v2.md
│   │   ├── spec-1-2-normalize-telemetry-payload.md
│   │   ├── spec-1-3-cli-batch-export.md
│   │   ├── spec-2-1-expose-ingestion-api.md
│   │   ├── spec-2-2-add-audit-log-schema.md
│   │   └── sprint-status.yaml
│   └── planning-artifacts/
│       ├── epic-1-context.md
│       ├── epic-2-context.md
│       ├── epics.md
│       └── project-context.md
└── src/…
```

**After** `sdd-parallel plan` + `prepare --wave 1`:

```text
my-project/
├── .parallel/
│   ├── history/run-001-<timestamp>/wave-plan.json
│   └── state.json
├── .worktrees/
│   ├── track-a/                      # Wave 1 → story 1-2
│   │   ├── _bmad/custom/             # 👻 per-slot BMAD overlay (hidden)
│   │   │   ├── bmad-build.toml
│   │   │   ├── bmad-build.user.toml
│   │   │   ├── bmad-build-auto.toml
│   │   │   └── bmad-build-auto.user.toml
│   │   ├── _bmad-output/
│   │   │   ├── implementation-artifacts/   # sprint-status.yaml merged 3-way
│   │   │   └── planning-artifacts/
│   │   │       ├── epic-1-context.md # 🔒 Zero Cache Invalidation lock
│   │   │       ├── epic-2-context.md # 🔒
│   │   │       └── project-context.md
│   │   ├── src/…
│   │   ├── .tmp/track-context.md     # 👻
│   │   ├── .parallel-lock.json       # 👻
│   │   └── ACTIVE_TRACK.md           # 👻 → /bmad-build 1-2
│   └── track-b/                      # Wave 1 → story 1-3
├── _bmad-output/
│   ├── implementation-artifacts/…
│   ├── parallel-artifacts/           # planner output (BMAD keeps it under _bmad-output)
│   │   ├── execution-plan-parallel.md
│   │   └── wave-plan.json
│   └── planning-artifacts/…
└── src/…
```

> Community variants: `sdd-parallel` also recognizes `.scratch/<slug>/issues/*.md` (`mattpocock/skills`, launching `/implement-spec`) and `bmad-loop.toml` / frontmatter status blocks.

### 2.3. GSD — Get Shit Done (`.planning/phases/` — Centralized `STATE.md` Sync)

**Before:**

```text
my-project/
├── .planning/
│   ├── phases/
│   │   ├── 01-core/
│   │   │   ├── 01-01-PLAN.md
│   │   │   ├── 01-02-PLAN.md
│   │   │   └── 01-03-PLAN.md
│   │   └── 02-api/
│   │       ├── 02-01-PLAN.md
│   │       └── 02-02-PLAN.md
│   ├── PROJECT.md
│   ├── REQUIREMENTS.md
│   ├── ROADMAP.md
│   └── STATE.md
└── src/…
```

`01-02-PLAN.md` (normalizer) and `02-01-PLAN.md` (API) live in different phases and share no files, yet `02-01` calls into `01-02`'s code. The planner schedules them into separate waves.

**After** `sdd-parallel plan` + `prepare --wave 1`:

```text
my-project/
├── .parallel/
│   ├── artifacts/
│   │   ├── execution-plan-parallel.md
│   │   └── wave-plan.json
│   ├── history/run-001-<timestamp>/wave-plan.json
│   └── state.json
├── .planning/…                       # untouched in main
├── .worktrees/
│   ├── track-a/                      # Wave 1 → 01-02
│   │   ├── .planning/
│   │   │   ├── phases/01-core/ · 02-api/
│   │   │   ├── PROJECT.md            # 🔒 read-only
│   │   │   ├── REQUIREMENTS.md       # 🔒 read-only
│   │   │   ├── ROADMAP.md            # 🔒 read-only
│   │   │   └── STATE.md              # 🔒 locked in slots, updated centrally on merge
│   │   ├── src/…
│   │   ├── .tmp/track-context.md     # 👻
│   │   ├── .parallel-lock.json       # 👻
│   │   └── ACTIVE_TRACK.md           # 👻 → /gsd-execute-phase 01-02
│   └── track-b/                      # Wave 1 → 01-03
└── src/…
```

### 2.4. OpenSpec (`openspec/changes/`)

**Before:**

```text
my-project/
├── openspec/
│   ├── changes/
│   │   ├── audit-log-schema/
│   │   ├── cli-batch-export/
│   │   ├── ingestion-api-endpoint/
│   │   ├── normalize-telemetry/
│   │   │   ├── design.md
│   │   │   ├── proposal.md
│   │   │   └── tasks.md
│   │   └── upgrade-schema-v2/
│   ├── specs/
│   │   └── telemetry/
│   │       └── spec.md
│   ├── config.yaml
│   └── project.md
└── src/…
```

**After** `sdd-parallel plan` + `prepare --wave 1`:

```text
my-project/
├── .parallel/
│   ├── artifacts/
│   │   ├── execution-plan-parallel.md
│   │   └── wave-plan.json
│   ├── history/run-001-<timestamp>/wave-plan.json
│   └── state.json
├── .worktrees/
│   ├── track-a/                      # Wave 1 → normalize-telemetry
│   │   ├── openspec/
│   │   │   ├── changes/
│   │   │   │   ├── normalize-telemetry/   # ✍️ this track's change folder
│   │   │   │   ├── cli-batch-export/      # 🔒 locked by sibling track-b
│   │   │   │   └── …
│   │   │   ├── specs/                # 🔒 canonical specs (changed only via openspec archive)
│   │   │   ├── config.yaml           # 🔒
│   │   │   └── project.md            # 🔒
│   │   ├── src/…
│   │   ├── .tmp/track-context.md     # 👻
│   │   ├── .parallel-lock.json       # 👻
│   │   └── ACTIVE_TRACK.md           # 👻 → /opsx:apply normalize-telemetry
│   └── track-b/                      # Wave 1 → cli-batch-export (mirror locks)
├── openspec/…                        # untouched
└── src/…
```

### 2.5. Beads (`.beads/beads.jsonl`)

**Before:**

```text
my-project/
├── .beads/
│   ├── beads.jsonl
│   ├── config.yaml
│   └── issues.jsonl
└── src/…
```

The graph tasks `bd-101`, `bd-102`, `bd-103`, `bd-201`, `bd-202`, with `blocks` edges between them:

```json
{"id": "bd-101", "title": "Upgrade schema to v2", "status": "open", "files": ["src/demo_app/schema.py"], "dependencies": [{"target": "bd-102", "type": "blocks"}], "priority": 1, "epic": "epic-1"}
{"id": "bd-102", "title": "Normalize telemetry payload", "status": "open", "files": ["src/demo_app/normalizer.py", "src/demo_app/cli.py"], "dependencies": [], "priority": 2, "epic": "epic-1"}
```

**After** `sdd-parallel plan` + `prepare --wave 1`:

```text
my-project/
├── .beads/…                          # main copy, merged line-wise on `merge --wave 1`
├── .parallel/
│   ├── artifacts/
│   │   ├── execution-plan-parallel.md
│   │   └── wave-plan.json
│   ├── history/run-001-<timestamp>/wave-plan.json
│   └── state.json
├── .worktrees/
│   ├── track-a/                      # Wave 1 → bd-102
│   │   ├── .beads/
│   │   │   ├── beads.jsonl           # status edits merged 3-way per line (open < closed)
│   │   │   ├── config.yaml           # 🔒 read-only
│   │   │   └── issues.jsonl
│   │   ├── src/…
│   │   ├── .tmp/track-context.md     # 👻
│   │   ├── .parallel-lock.json       # 👻
│   │   └── ACTIVE_TRACK.md           # 👻 → bd update bd-102 --status in-progress
│   └── track-b/                      # Wave 1 → bd-103
└── src/…
```

After `merge --wave 1`, `bd-102` and `bd-103` are both `closed` in `.beads/beads.jsonl`, with 0 conflict markers.

### 2.6. Custom SDD Frameworks (`.parallel/custom-kit.yaml`)

For proprietary or in-house roadmaps, onboard via `sdd-parallel init-custom` to generate `.parallel/custom-kit.yaml` (or `.parallel/custom-adapter.yaml`) defining `allowed_write_globs`, `forbidden_files`, `status_ranks`, and your templated `worker_command` (see [Integration & Ecosystem Spec](dossier/03_INTEGRATION_AND_ECOSYSTEM_SPEC.md#25-pluggable-custom-sdd-adapter-custom)).

---

## 3. Interactive Wave Inspector & Orchestration Modes (Mode A vs. Mode B)

### 3.1. Launching the Browser Wave Inspector (`sdd-parallel inspect --open`)

Generate and open the standalone, zero-dependency HTML cockpit (`assets/inspector.html`):

```bash
# Auto-load from the current repository's .parallel/ artifacts:
sdd-parallel inspect --open

# Or inspect an explicit wave-plan.json and write to a custom output path:
sdd-parallel inspect --plan .parallel/artifacts/wave-plan.json --output ./wave-report.html --open
```

Prefer to try it with zero local state? Open the **[⚡ Live Interactive Wave Inspector](https://stopactingsosmall.github.io/sdd-parallel/)**.

### 3.2. Walkthrough of the 4 Wave Inspector Tabs & CLI Action Bridge

- **Top Summary Bar & 4-Step Pipeline Strip:** Shows live counts (`Waves`, `Tracks`, `Tasks`, `Conflicts Caught`, `Spine Locks`) and tracks the 4-stage execution loop (`1 plan` ➔ `2 prepare --wave K` ➔ `3 execute tracks` ➔ `4 merge --wave K`).
- **Tab 1 — `🌊 Waves & Tracks` & CLI Action Bridge:**
  - Displays planned waves and parallel track cards (`track-a`, `track-b`, …) with color-coded file roles:
    - 🟢 **Exclusive Write Scope:** files owned exclusively by that slot.
    - 🔷 **Additive Registry (`ADDITIVE_REGISTRY`):** shared append-only modules with AST function scope isolation (e.g., `fn: register_...`).
    - 🟣 **Spine Contract (`SPINE`):** protected schema/contract files serialized across waves.
  - Shows dependency badges (e.g., `T002 blocks T004`) and provides one-click **Copy** buttons in the wave header (`sdd-parallel prepare --wave K`) and barrier footer (`sdd-parallel merge --wave K`).
- **Tab 2 — `📊 Conflict DAG` & Task Specification Modal:**
  - Interactive SVG directed acyclic graph organizing tasks into topological wave columns.
  - Clicking any task node opens the **Task Spec Modal** showing its *Exclusive Write Scope*, *Additive Shared Files & AST Function Scopes*, *Protected Spine Locks*, and *Related Conflict Constraints*.
- **Tab 3 — `⚡ Conflict Matrix`:**
  - Pairwise audit table separating Type-1 direct write overlaps ($W_A \cap W_B \neq \emptyset$) from Type-2 semantic import hazards ($W_A \cap I_B \neq \emptyset$), explaining why colliding tasks were placed into separate waves.
- **Tab 4 — `🔒 Architectural Roles`:**
  - Consolidated AST classification inventory mapping repository modules to `SPINE`, `ADDITIVE_REGISTRY`, and `STANDARD`.

### 3.3. Mode A: Interactive Two-Stage Assisted (Wave Inspector + Fixed IDE Slots)

Best when you want human-in-the-loop review before spawning agents and between waves:

1. Run `sdd-parallel plan` (optionally passing `--max-tracks N` or `--routing-file FILE`).
2. Run `sdd-parallel inspect --open` to verify the Conflict DAG, Spine locks, and track assignments.
3. Click **Copy** in the wave header to run `sdd-parallel prepare --wave 1` (add `--antigravity-layout` for Antigravity).
4. Open `.worktrees/track-a` and `.worktrees/track-b` in your IDE/CLI windows and execute the command from `ACTIVE_TRACK.md` (or `sdd-parallel run-guide --wave 1`).
5. Click **Copy** in the barrier footer to run `sdd-parallel verify-wave --wave 1` and `sdd-parallel merge --wave 1`.
6. Repeat from `sdd-parallel plan` for Wave 2 (the fixed slots reset cleanly in place).

### 3.4. Mode B: Multi-Window & Headless Cyclic Autopilot (`--policy cyclic`)

Best for autonomous batch execution across fixed slots:

```bash
sdd-parallel autopilot --wave 1 \
  --harness claude-code \
  --execute \
  --policy cyclic \
  --max-review-rounds 2 \
  --routing-file routing.json
```

- **Harnesses:** `--harness {antigravity,claude-code,codex}` (add `--antigravity-layout` for Antigravity slot paths). Omit `--execute` for a dry-run preview of generated per-track launch commands.
- **Policies (`--policy`):**
  - `single-shot` — launches each track once and leaves barrier verification to `verify-wave` / `merge`.
  - `cyclic` (`BoundedCyclicReviewPolicy`) — drives an automated `implement -> verify -> review -> fix` loop bounded by `--max-review-rounds N`.
- **Cognitive Model Routing (`--routing-file`):** accepts JSON/YAML overrides per track or per task (`model`, `model_effort` in `low`/`medium`/`high`, `execution_mode`), while preserving AST structural signals (`TaskStructuralSignals`) in `.parallel/run-manifest.json`. See [Section 3.4 of the Integration Spec](dossier/03_INTEGRATION_AND_ECOSYSTEM_SPEC.md#34-cognitive-routing-override-contract---routing-file).

---

## 4. Day-1 Troubleshooting & Recovery Runbook

### 4.1. Quality Gate or Test Failures on `verify-wave` / `merge` (`abort-wave` vs. `rollback-wave --save-backup`)

Use this decision path when a wave fails verification or breaks tests at the barrier:

| Situation | Command | What Happens on Disk |
| :--- | :--- | :--- |
| **Unmerged wave:** `verify-wave --wave K` flags a scope violation or uncommitted drift | Fix inside `.worktrees/track-<id>`, commit, and re-run `sdd-parallel verify-wave --wave K` | No changes to `main`; manifest `.parallel/wave-<K>-manifest.json` updates when compliant |
| **Unmerged wave:** you want to throw away Wave $K$ slots and re-plan | `sdd-parallel abort-wave --wave K` | Resets fixed slots in place, deletes temporary `parallel/*` branches; `main` untouched |
| **During `merge --wave K`:** tests/quality gates fail | Automatic transactional rollback | Engine automatically resets `main` back to `pre_merge_commit` recorded in `.parallel/state.json` |
| **Already merged wave:** tests fail later or you need to undo Wave $K$ | `sdd-parallel rollback-wave --wave K --save-backup` | Preserves `backup/wave-<K>-before-rollback-*` branch, reverts `HEAD` in strict **LIFO** order, and polymorphically resets SDD task statuses (`[x]` → `[ ]`, `done` → `ready-for-dev`, `closed` → `open`) |

**Common `merge` flags:**
- `sdd-parallel merge --wave K --strict-gates` — enforce strict non-zero exit if any quality gate command fails or is missing.
- `sdd-parallel merge --wave K --skip-gates` — merge after `verify-wave` scope audit without running test/lint gates (use only when gates are run externally in CI).
- `sdd-parallel merge --wave K --cleanup` — tear down slots after merging the final wave.

> **LIFO Order Rule:** Merged waves must be rolled back strictly in reverse order (Wave $K$ before Wave $K-1$). Always prefer `--save-backup` (default safety behavior) when reverting work you may want to cherry-pick from `backup/wave-<K>-before-rollback-*`.

### 4.2. Scope Guard Blocked a Needed File (`.tmp/scope-escalation.json` & `file_roles` / `contract_globs`)

When an agent is denied access by `PreToolUse`, Terminal Guard, or OS `chmod a-w`:

1. **Diagnose why the file is locked:**
   ```bash
   sdd-parallel check-lock --file .worktrees/track-a/src/app/shared.py
   ```
   Exit code `0` returns `{"decision": "allow"}`; exit code `2` returns `{"decision": "deny", "reason": "..."}` (either owned by a sibling track, locked by `SPINE`, or an immutable SDD contract).
2. **Request missing files mid-wave via Dynamic Scope Escalation (`.tmp/scope-escalation.json`):**
   If a track discovers an extra `write_file` it genuinely needs, write an escalation request inside that slot's `.tmp/scope-escalation.json` (which stays hidden from Git via `info/exclude`). At the `verify-wave` barrier, `sdd-parallel` evaluates the escalation: if the requested file is disjoint from sibling tracks and not Spine-locked, scope can be reconciled; if it introduces a Type-1 or Type-2 collision, the task is safely deferred and re-planned into a subsequent wave.
3. **Override heuristics permanently via `.parallel-config.json`:**
   When a file should always be serialized (`spine`), safely shared (`additive_registry`), or treated as a cross-layer contract (`contract_globs`), declare it explicitly in `.parallel-config.json` at your repository root:
   ```json
   {
     "file_roles": {
       "src/core/contracts.py": "spine",
       "src/api/routes.py": "additive_registry"
     },
     "contract_globs": [
       "**/schemas/**",
       "**/migrations/**",
       "openapi.*",
       "*.proto",
       "*.graphql"
     ]
   }
   ```
   Then re-run `sdd-parallel plan` so `WavePlan` reflects the updated roles.

### 4.3. Debugging Non-Python & Polyglot Repositories (`CRGSidecarAdapter` + Explicit File Declarations)

The built-in zero-dependency AST scanner parses Python modules natively. For **TypeScript/JavaScript, Go, Rust**, or multi-language repositories:

1. **Make write targets explicit in your SDD specs:**
   Ensure every task lists its target paths via `Code Map`, `Files:`, GSD XML `<files>`, JSONL `"files": [...]`, or backticked file paths (`src/routes/auth.ts`). `sdd-parallel` parses these directly as Tier-1 write targets before any code is written.
2. **Enable the Tree-sitter sidecar (`code-review-graph` / `CRGSidecarAdapter`):**
   When `code-review-graph` (`v2.3.9` WAL index) is available in the repository, `CRGSidecarAdapter` supplies cross-file call and import edges across polyglot source files so `BSPWaveScheduler` computes Type-2 semantic impact radii ($W_A \cap I_B \neq \emptyset$) beyond Python.
3. **Protect cross-layer boundaries with `DEFAULT_CONTRACT_GLOBS`:**
   Pure AST import graphs cannot see runtime serialization boundaries (e.g., Pydantic JSON → TypeScript client, SQL foreign keys, OpenAPI, Protobuf, GraphQL). Built-in `DEFAULT_CONTRACT_GLOBS` (`**/schemas/**`, `**/migrations/**`, `openapi.*`, `*.proto`, `*.graphql`) automatically elevate schema/migration/IDL files to `SPINE` serialization. Add project-specific globs (such as `shared/types/*.ts` or `prisma/schema.prisma`) in `.parallel-config.json`.

### 4.4. Multi-Kit Coexistence & Disambiguation (`--kit`)

If your repository contains artifacts from more than one SDD framework (for example, legacy `_bmad-output/` alongside active `.specify/`):

- In `auto` mode, `sdd-parallel` inspects detected adapters and selects the first kit with uncompleted tasks (`include_done=False`), while sandboxing all inactive SDD directories in `.parallel-lock.json` (`"Locked by inactive SDD framework sandboxing"`).
- To pin a framework explicitly, pass `--kit {bmad,spec-kit,gsd,openspec,beads,custom}` on CLI commands or set `"default_kit": "spec-kit"` in `.parallel-config.json`.
