# Security Policy

## Supported Versions

| Version | Supported |
| :--- | :---: |
| `0.1.x` | ✅ Supported |
| `< 0.1` | ❌ Unsupported |

## Security Model & Scope Guard Architecture

`sdd-parallel` is designed to orchestrate parallel AI coding agent tracks across multi-repository and spec-driven workflows.

Our **Defense-in-Depth Scope Guard** consists of four cooperating layers:
1. **PreToolUse Hook (`blast_radius_guard.py`):** Intercepts file write/edit tool invocations from AI agent environments (Google Antigravity, Claude Code, GSD, Codex) and enforces lock boundaries declared in `.parallel-lock.json`.
2. **Terminal Guard:** Intercepts shell execution tools (`Bash`, `run_command`, `cmd`, `powershell`) to block rogue operations (`>`, `sed -i`, `rm`, `python open`) that attempt to bypass tool-level hooks.
3. **OS-Level Read-Only File Locks:** Sets read-only permissions (`chmod a-w` on Unix / `stat.S_IREAD` on Windows) on unassigned files, paired with `core.fileMode false` to prevent dirty working trees.
4. **Git Diff Audit Gate (`verify_wave`):** Performs pre-merge validation of working tree status and git diff against merge-base to detect unauthorized modifications across all commits.

### Intended Threat Model

> [!IMPORTANT]
> The Scope Guard is engineered to prevent **unintentional LLM scope drift and semantic collisions** when autonomous coding agents execute parallel tasks.
> It is **not** a hardened cryptographic operating system sandbox and does not replace microVMs (e.g., Firecracker), containers (e.g., Docker), or OS-level virtualization when executing untrusted or adversarial code.

## Reporting a Vulnerability

If you discover a security vulnerability or bypass in `sdd-parallel`:
1. **Do not open a public issue.**
2. Report the vulnerability privately via [GitHub Security Advisories](https://github.com/stopActingSoSmall/sdd-parallel/security/advisories/new).
3. Provide a minimal reproduction script or steps to trigger the issue.

We will review, acknowledge, and provide an initial response to valid security disclosures within **48–72 hours** in accordance with responsible disclosure practices.
