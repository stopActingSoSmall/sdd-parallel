# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.3] - 2026-11-26

### Conceptual Highlights (Before → After)
- **Fail-Closed Runner & Process Tree Cleanup:** Previously, `BoundedCyclicReviewPolicy` could qualify failing executors, exhausted preflight gates, or missing reviewers (`reviewer_fn=None`), and track timeouts on Windows/POSIX could leave descendant processes holding `stdout`/`stderr` pipes open; now `_run_harness_subprocess()` kills the entire process tree (`taskkill /F /T` / `os.killpg`) and `BoundedCyclicReviewPolicy` fails closed before invoking any reviewer.
- **Honest Autopilot Exit & Partial Merge Reporting:** Previously, `sdd-parallel autopilot --execute --auto-merge` returned exit code `0` even when `verify_wave` or partial merge rejected tracks; now `WaveRunSummary` surfaces `partial_merge_used`, `merged_tracks`, `rejected_tracks`, and `backup_branches`, setting `auto_merged=False` and returning exit code `1` on incomplete merges.
- **Scoped ID Resolution & Read-Only Safety Across All 6 SDD Adapters:** Previously, rollback or state inspection could reset prefix-colliding IDs (`1-1` vs `1-10`), drop `spec-*.md` files, mutate Beads Dolt state during read-only loads, or touch archived OpenSpec changes; now all 6 adapters (`Beads`, `BMAD`, `Custom`, `GSD`, `OpenSpec`, `Spec Kit`) enforce strict scoped ID boundaries and read-only `bd export`.
- **Canonical Graph Paths, Monotonic Mergers & AST Registry Safety:** Previously, `/**/` globs missed zero-segment paths, relative `ImportFrom` nodes were skipped, deleted Python modules bypassed signature drift checks, and `server.py` fell back to `ADDITIVE_REGISTRY` unconditionally; now path normalization, relative imports, deleted-module drift, monotonic Beads/checklist tie-breaking, and atomic manifest writes are enforced end-to-end.

### Default Behavior & CLI Changes
- **`sdd-parallel autopilot --execute --policy cyclic-review`:** Fails closed with a structured JSON error and exit code `1` until external reviewer wiring lands in `v0.3.1`.
- **`sdd-parallel autopilot --execute --auto-merge`:** Exits `0` only when both `summary.all_succeeded` and `summary.auto_merged` are `True` (partial merges with rejected tracks now exit `1` and report `rejected_tracks` / `merged_tracks`).

### Changed & Hardened
- **Core Graph & AST Diff Gate:** Unified `normalize_rel_path()` and zero-segment `/**/` matching in `matches_path_pattern()`; shared `resolve_import_from_targets()` across `ASTHotspotScanner` and `ast_diff_gate`; added deleted-module drift detection in `check_signature_and_default_drift()`, completed-wave `SPINE_ORDER` preservation via `completed_wave_of`, and 2-char CLI flag detection (`--id`, `--db`) in `extract_namespace_tokens()`.
- **3-Way Status & AST Registry Mergers:** Narrowed `_CHECKLIST_KEY_RE` against `S3`/`OAuth2`/`V2` collisions, enforced monotonic `closed > in-progress` ranking in `Beads`, added branch-aware tie-breaking, atomic `.tmp-*` + `os.replace()` writes in `save_wave_manifest()`, strict `is_python_registry_candidate()` gating, and 5 fail-closed AST invariants in `registry_merger.py`.
- **SDD Adapters (`beads`, `bmad`, `custom`, `gsd_hijack`, `openspec`, `spec_kit`):** Read-only `bd export` and scoped `bd update`; structural round-trip `stories.yaml` reset and `spec-*.md` preservation in `BMAD`; duplicate ID qualification and YAML/JSON reset in `CustomManifestAdapter`; exact ID boundary matching in `GSDHijackAdapter`, `OpenSpecAdapter` (ignoring `archive/`), and `SpecKitAdapter`.
- **Subprocess & Runner Lifecycle:** Added `_run_harness_subprocess()` with process-group/tree termination and bounded pipe drain, strict fail-closed gates in `BoundedCyclicReviewPolicy.execute()`, and `PartialMergeOutcome` propagation in `WaveRunner.run_wave()`.

## [0.2.2] - 2026-11-25

### Conceptual Highlights (Before → After)
- **Concurrent Edits in Shared Registry Files (`cli.py`, `__init__.py`, `*/mcp/server.py`):** Previously, parallel tracks adding orthogonal commands or exports collided on adjacent lines and blocked the wave; now `registry_merger.py` performs deterministic Python stdlib `ast` 3-way merges with fail-closed fallback (`None` -> `Partial Barrier Merge`) if existing function bodies or colliding symbols are mutated.
- **Wave Barrier Resilience (`Partial Barrier Merge` & NACK Feedback Loop):** Previously, one broken track rolled back the entire wave all-or-nothing; now `sdd-parallel merge` defaults to `partial_merge=True`, landing clean tracks in OCC order, isolating culprits via greedy prefix bisection into `backup/wave-<K>-<track>-failed-<ts>`, writing `.parallel/barrier-feedback-<story_id>.json`, and re-scheduling remaining waves.
- **Pre-Merge Semantic Collision Interception (`ast_diff_gate.py`):** Previously, cross-track import cycles, signature/default drift, duplicated helpers, and concurrent lockfile edits surfaced only after `git merge`; now `verify-wave` inspects branch AST diffs against `merge-base` before merging.
- **Ordered Directory Serialization & Namespace Collisions:** `extract_spine_bucket()` serializes parallel creations in ordered directories (`**/migrations/**`, `**/versions/**`, `docs/adr/**`) and `extract_namespace_tokens()` detects cross-file CLI flag, ENV var, and HTTP route collisions during `plan`.

### Default Behavior & CLI Changes
- **`partial_merge=True` by default in `sdd-parallel merge`:** Pass `--no-partial-merge` to enforce legacy strict all-or-nothing rollback.
- **`--defer-ast-violations` in `sdd-parallel verify-wave`:** Allows deferring tracks flagged by `ast_diff_gate` directly into barrier NACK feedback and next-wave rescheduling.
- **Persistent Wave Plan Cache:** `prepare`, `claim-track`, `verify-wave`, `merge`, `autopilot`, `run-guide`, and `inspect` share `.parallel/wave-plan.json` via `_load_or_compute_repo_plan()`, preserving dynamic scope escalations and rescheduled tails.

### Added
- **Python AST-Aware 3-Way `ADDITIVE_REGISTRY` Merger (`registry_merger.py`):** Deterministic stdlib `ast` 3-way merge for concurrent header imports, `__all__`, and orthogonal top-level commands/functions (e.g., `cli.py`, `__init__.py`, `*/mcp/server.py`) with fail-closed fallback (`None` -> `Partial Barrier Merge`) when existing function bodies or colliding symbols are mutated.
- **Pre-Merge AST Diff Gate (`ast_diff_gate.py`):** Intercepts virtual cross-track import cycles (Tarjan SCC), signature/default/constant drift vs `merge-base`, structurally duplicate new helper functions via normalized AST shingles (Jaccard >= 0.75), and concurrent dependency manifest/lockfile edits inside `verify-wave` (with `--defer-ast-violations`).
- **Partial Barrier Merge & NACK Feedback Loop:** CLI `merge` defaults to `partial_merge=True` (use `--no-partial-merge` for strict all-or-nothing rollback): OCC-ordered track integration, sequential greedy prefix culprit bisection, `backup/wave-<K>-<track>-failed-<ts>` safety branches, `.parallel/barrier-feedback-<story_id>.json`, and remaining-wave re-planning via `BSPWaveScheduler.reschedule_remaining_waves()`.
- **Spine Buckets & Namespace Collision Extractor:** `extract_spine_bucket()` serializes parallel file creations inside ordered directories (`**/migrations/**`, `**/versions/**`, `docs/adr/**`); `extract_namespace_tokens()` detects cross-file CLI flag, ENV var, and HTTP route collisions.
- **Extended Retrospective Evaluator (`sdd-parallel bench`):** `evaluate_task_pairs()` now accounts for ordered directory (`spine_bucket`) and cross-file namespace token (`NAMESPACE_COLLISION`) overlaps alongside direct and import-graph collisions, backed by an offline 8-scenario semantic collision regression suite in `pytest`.
- **Honest Live Demo (`sdd-parallel demo`):** Concurrent `cli.py` edits merged via `registry_merger.py` and verified with live deterministic smoke `checkpoint_gate` (`run_quality_gates=True`) across all 5 SDD kits.

### Changed & Hardened
- **Static Precision & Intra-File AST Isolation:** Token-bounded `_ROUTER_RECEIVER_HINTS` in `_is_registry_decorator()` prevent non-router `@get`/`@register` false positives; `_NEGATED_CONSTRAINT_RE` filters read-only/negated clauses in `extract_namespace_tokens()`; `ASTHotspotScanner.are_functions_isolated()` verifies intra-file call reachability in `_has_isolated_function_scope()`.
- **Unified Wave Plan Cache & Atomic Barrier State:** `_load_or_compute_repo_plan()` preserves escalated/rescheduled `.parallel/wave-plan.json` across `prepare`, `claim-track`, `verify-wave`, `merge`, `autopilot`, and `run-guide`; `partial_merge` leaves `master` clean (`git status --porcelain == ""`) with atomic adapter state commits; `run_stdin_hook()` records scope escalations (`record_escalation=True`).
- **Harness Compatibility:** Added `.claude/settings.local.json` sandbox `autoAllowBashIfSandboxed`, `codex exec --trust`, and OpenSpec `/openspec-apply <id>`.
- **BMAD v7, OpenSpec v1.16.1, Custom SDD & Gate 8 Invariants:** Native `tickets.toml` + `story-*-plan.md` (`built` -> `REVIEW`), CommonMark `## Title ##` headings, `CustomManifestAdapter` (`init-custom`), and Dolt DB sync via `bd import --allow-stale`.
- **7-Language Documentation Parity:** Synchronized `README.md`, `ARCHITECTURE.md`, `docs/USER_GUIDE.md`, `docs/dossier/02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`, and all 6 translations (`es`, `ja`, `ko`, `pt-BR`, `ru`, `zh-CN`).

## [0.2.1] - 2026-11-10

### Added
- **GitHub Spec Kit Official Extension Package (`Task 36`):** Packaged canonical extension schema `1.0` in `extension.yml` with `/specify plan-waves`, `/specify prepare-wave`, and `/specify merge-wave`.

## [0.1.1] - 2026-10-30

### Added
- **Visual Identity & Bento Asset Pipeline:** Tactical Air Traffic Control / Dark Glass Bento graphics in `docs/assets/` (`hero-banner.png`, `merge-bomb-anatomy.png`, `whats-inside-worktrees.png`, `how-it-works-pipeline.png`) and HTML templates in `docs/marketing/banner_templates/`.
- **Landing-Grade `README.md` & Tangible 5-Kit Showcase:** Dual-Layer layout with ecosystem compatibility anchors, 4 annotated demo walkthrough chunks, interactive before/after trees for all 5 SDD kits, and harness execution profiles.
- **7-Language Documentation Parity:** Complete multilingual synchronization across English, Russian, Simplified Chinese, Spanish, Brazilian Portuguese, Japanese, and Korean with strict H2 parity gates.

### Changed
- **Canonical 5-Kit Demo Sandboxes:** Standardized synthetic task identifiers across all kits (`T001..T005 [P]` for Spec Kit, `01-01..02-02` for GSD, slug folders for OpenSpec, `bd-101..bd-202` for Beads).
- **Polymorphic Artifacts Hierarchy:** Non-BMAD frameworks now route execution plans to `.parallel/artifacts/` instead of polluting `_bmad-output/`.
- **Polymorphic Worker Commands:** Autopilot dynamically renders native slash commands via `adapter.render_worker_command(track)`.

## [0.1.0] - 2026-10-29

### Added
- **BSP Wave Scheduler (`sdd-parallel plan`):** Bulk-Synchronous Parallelism engine with Kahn's topological sorting, cyclic dependency detection, and guaranteed 100% task coverage.
- **AST Hotspot & CRG Blast Radius Scanner:** Static Python import graph analysis and `code-review-graph` sidecar integration catching Type 2 semantic collisions ($W_A \cap I_B \neq \emptyset$) with sub-second performance.
- **Fixed Track Slots (`track-<id>`):** Parametric fixed-slot worktree pool (`track-a`, `track-b`, ...) with $O(1)$ disk footprint and in-place `git checkout -B` reset without worktree teardown.
- **Defense-in-Depth Scope Guard:** Multi-layered agent isolation combining `PreToolUse` hook, Terminal Guard for bash/powershell/cmd, OS-level read-only file locks (`chmod a-w`), and merge-base git diff audits.
- **Monotonic 3-Way YAML Merger:** Semilattice-based 3-way merger for `sprint-status.yaml` preserving comments and structure with zero conflict markers (`<<<<<<<`).
- **LIFO Transactional Rollback (`sdd-parallel rollback-wave` / `abort-wave`):** Strict LIFO wave unwind with immutable safety backup branches (`backup/wave-<K>-before-rollback-*`) and polymorphic task status reset.
- **Multi-Kit SDD Adapters:** Out-of-the-box support for 5 major agent ecosystems: BMAD Method, GitHub Spec Kit, GSD Hijack, OpenSpec, and Beads.
- **Standalone Merge-Bomb Demo (`sdd-parallel demo`):** Self-contained, zero-API-key demonstration of Type 2 conflict detection and 3-way resolution running in <5 seconds.
- **CI / CD Automation:** GitHub Actions workflow testing on Ubuntu, Windows, and macOS across Python 3.11, 3.12, and 3.13 with automated packaging and PyPI Trusted Publishing.

[Unreleased]: https://github.com/stopActingSoSmall/sdd-parallel/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/stopActingSoSmall/sdd-parallel/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/stopActingSoSmall/sdd-parallel/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/stopActingSoSmall/sdd-parallel/compare/v0.1.1...v0.2.1
[0.1.1]: https://github.com/stopActingSoSmall/sdd-parallel/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/stopActingSoSmall/sdd-parallel/releases/tag/v0.1.0
