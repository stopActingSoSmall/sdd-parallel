# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[Unreleased]: https://github.com/stopActingSoSmall/sdd-parallel/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/stopActingSoSmall/sdd-parallel/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/stopActingSoSmall/sdd-parallel/releases/tag/v0.1.0
