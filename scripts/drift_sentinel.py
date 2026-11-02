#!/usr/bin/env python3
"""Architecture Drift Sentinel & Universal Delta Inspector (Task 15j).

Enforces repository integrity, blast-radius delta projection,
L0 byte budgets, workstation leak prevention, and documentation sync.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Ensure `src/` is importable when executed directly
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from sdd_parallel.adapters import ADAPTER_REGISTRY  # noqa: E402
from sdd_parallel.cli import _build_parser  # noqa: E402


@dataclass
class DriftFinding:
    """Represents a detected architectural gap, leak, or drift issue."""

    category: str
    summary: str
    details: list[str] = field(default_factory=list)
    remediation: str | None = None
    is_critical: bool = True


def get_git_delta(repo_root: Path, staged_only: bool = False) -> list[str]:
    """Retrieve list of modified and added files relative to repo_root."""
    try:
        if staged_only:
            proc = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            lines = [
                line.strip().replace("\\", "/")
                for line in proc.stdout.splitlines()
                if line.strip()
            ]
            return lines

        proc_diff = subprocess.run(
            ["git", "diff", "HEAD", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        proc_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        files: set[str] = set()
        if proc_diff.returncode == 0:
            for line in proc_diff.stdout.splitlines():
                if line.strip():
                    files.add(line.strip().replace("\\", "/"))

        if proc_status.returncode == 0:
            for line in proc_status.stdout.splitlines():
                if line.strip():
                    # Format: XY filename or XY orig -> dest
                    parts = line[3:].strip().split(" -> ")
                    files.add(parts[-1].strip().replace("\\", "/"))

        return sorted(files)
    except Exception:
        return []


def check_workstation_leaks(repo_root: Path, target_files: Sequence[str]) -> list[DriftFinding]:
    """Check target files for prohibited workstation paths."""
    # Assemble dynamically to avoid self-matching during script inspection
    forbidden = [
        f"users{sep}admin" for sep in ("/", "\\")
    ] + ["c:\\users\\"]
    findings: list[DriftFinding] = []

    files_to_check: list[Path] = []
    if target_files:
        for f in target_files:
            p = repo_root / f
            if p.is_file():
                files_to_check.append(p)
    else:
        # Default scan: inspect critical code and doc trees
        for ext in ("*.py", "*.md", "*.yaml", "*.yml", "*.json", "*.toml"):
            files_to_check.extend(repo_root.glob(f"src/**/{ext}"))
            files_to_check.extend(repo_root.glob(f"docs/**/{ext}"))
            files_to_check.extend(repo_root.glob(f".agents/**/{ext}"))
            files_to_check.extend(repo_root.glob(f".agent/**/{ext}"))

    seen: set[Path] = set()
    for file_path in files_to_check:
        if file_path in seen or not file_path.is_file():
            continue
        seen.add(file_path)
        if file_path.suffix in (".png", ".ico", ".db", ".pyc", ".lock"):
            continue
        # Skip self, remediator, and test files to prevent false positives on validator logic
        if file_path.name in (
            "drift_sentinel.py",
            "remediate_drift.py",
            "test_architecture_invariants.py",
            "test_drift_remediation.py",
        ):
            continue

        try:
            content = file_path.read_text(encoding="utf-8-sig")
        except Exception:
            continue

        content_lower = content.lower()
        matched_leaks = [term for term in forbidden if term in content_lower]
        if matched_leaks:
            rel = file_path.relative_to(repo_root).as_posix()
            findings.append(
                DriftFinding(
                    category="[SECURITY]",
                    summary=f"Workstation path leak detected in '{rel}'",
                    details=[f"Found prohibited string patterns: {matched_leaks}"],
                    remediation=f"Sanitize local workstation paths in {rel}",
                    is_critical=True,
                )
            )

    return findings


def check_agents_budget(repo_root: Path) -> list[DriftFinding]:
    """Verify AGENTS.md byte limit (<= 3500 B) and clean domain abstraction."""
    findings: list[DriftFinding] = []
    agents_file = repo_root / "AGENTS.md"
    if not agents_file.is_file():
        # Private governance file absent in public clean clone
        return findings

    size = agents_file.stat().st_size
    if size > 3500:
        findings.append(
            DriftFinding(
                category="[BUDGET]",
                summary=f"AGENTS.md size ({size} B) exceeds maximum limit (3500 B)",
                details=[f"File must be compressed by at least {size - 3500} bytes"],
                remediation="Compress AGENTS.md to stay within <= 3500 bytes",
                is_critical=True,
            )
        )

    content = agents_file.read_text(encoding="utf-8")
    forbidden = ["routers/", "duckdb.py", "console.py", "users/admin", "users\\admin"]
    leaks = [term for term in forbidden if term.lower() in content.lower()]
    if leaks:
        findings.append(
            DriftFinding(
                category="[BUDGET]",
                summary=f"Prohibited domain terms found in AGENTS.md: {leaks}",
                details=["L0 Constitution must remain strictly framework- and project-agnostic"],
                remediation="Remove domain-specific entities from AGENTS.md",
                is_critical=True,
            )
        )

    return findings


def check_adapter_registry(repo_root: Path) -> list[DriftFinding]:
    """Verify adapter completeness across matrix, CLI, demo sandbox, and ecosystem spec."""
    findings: list[DriftFinding] = []
    registered = set(ADAPTER_REGISTRY.keys())

    # 1. Public touchpoints
    # CLI demo choices
    parser = _build_parser()
    subparsers_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if subparsers_action and "demo" in subparsers_action.choices:
        demo_parser = subparsers_action.choices["demo"]
        kit_action = next(
            (a for a in demo_parser._actions if "--kit" in a.option_strings),
            None,
        )
        if kit_action and kit_action.choices is not None:
            for kit in sorted(registered):
                if kit not in kit_action.choices:
                    findings.append(
                        DriftFinding(
                            category="[CODE]",
                            summary=f"Kit '{kit}' missing from CLI demo choices",
                            details=[f"Available choices: {kit_action.choices}"],
                            remediation=f"Add '{kit}' to choices of '--kit' in cli.py",
                            is_critical=True,
                        )
                    )

    # Demo synthetic fixture generator
    demo_file = repo_root / "src" / "sdd_parallel" / "demo.py"
    if demo_file.is_file():
        demo_text = demo_file.read_text(encoding="utf-8")
        for kit in sorted(registered):
            if f'"{kit}"' not in demo_text and f"'{kit}'" not in demo_text:
                findings.append(
                    DriftFinding(
                        category="[CODE]",
                        summary=f"Kit '{kit}' missing from demo.py fixtures",
                        remediation=f"Add '{kit}' synthetic fixture in demo.py",
                        is_critical=True,
                    )
                )

    # Integration and ecosystem spec
    spec_file = repo_root / "docs" / "dossier" / "03_INTEGRATION_AND_ECOSYSTEM_SPEC.md"
    if spec_file.is_file():
        spec_text = spec_file.read_text(encoding="utf-8")
        for kit in sorted(registered):
            if f"`{kit}`" not in spec_text and f"({kit})" not in spec_text:
                findings.append(
                    DriftFinding(
                        category="[DOCS]",
                        summary=f"Kit '{kit}' missing from 03_INTEGRATION_AND_ECOSYSTEM_SPEC.md",
                        remediation=f"Document '{kit}' in 03_INTEGRATION_AND_ECOSYSTEM_SPEC.md",
                        is_critical=True,
                    )
                )

    # 2. Private governance matrix (if present)
    matrix_file = (
        repo_root
        / ".agents"
        / "skills"
        / "sdd-dev-playbooks"
        / "references"
        / "adapter-touchpoint-matrix.md"
    )
    if matrix_file.is_file():
        matrix_text = matrix_file.read_text(encoding="utf-8")
        kits_in_matrix = set(re.findall(r"\|\s*\*\*`([a-z0-9_-]+)`\*\*", matrix_text))

        missing_in_matrix = registered - kits_in_matrix
        if missing_in_matrix:
            for kit in sorted(missing_in_matrix):
                findings.append(
                    DriftFinding(
                        category="[CODE]",
                        summary=f"Adapter '{kit}' registered in code but missing from matrix",
                        details=[
                            f"Missing entry in '{matrix_file.relative_to(repo_root).as_posix()}'"
                        ],
                        remediation=f"Document '{kit}' in adapter-touchpoint-matrix.md",
                        is_critical=True,
                    )
                )

        missing_in_code = kits_in_matrix - registered
        if missing_in_code:
            for kit in sorted(missing_in_code):
                findings.append(
                    DriftFinding(
                        category="[CODE]",
                        summary=(
                            f"Kit '{kit}' present in matrix but not registered in ADAPTER_REGISTRY"
                        ),
                        remediation=f"Register '{kit}' in src/sdd_parallel/adapters/__init__.py",
                        is_critical=True,
                    )
                )

    return findings


def check_cli_skill_sync(repo_root: Path) -> list[DriftFinding]:
    """Verify all CLI subcommands are documented in user orchestration skill."""
    findings: list[DriftFinding] = []
    skill_file = repo_root / ".agent" / "skills" / "sdd-parallel" / "SKILL.md"
    if not skill_file.is_file():
        skill_file = repo_root / "src" / "sdd_parallel" / "resources" / "SKILL.md"
    if not skill_file.is_file():
        return findings

    parser = _build_parser()
    subparsers_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    cli_commands = set(subparsers_action.choices.keys())
    skill_text = skill_file.read_text(encoding="utf-8")

    missing = [
        cmd
        for cmd in sorted(cli_commands)
        if f"`{cmd}`" not in skill_text
        and f"`sdd-parallel {cmd}" not in skill_text
        and f"{cmd}:" not in skill_text
    ]
    if missing:
        findings.append(
            DriftFinding(
                category="[CLI]",
                summary=f"CLI subcommands missing from SKILL.md: {missing}",
                details=[f"Commands {missing} need documentation in SKILL.md"],
                remediation="Update .agent/skills/sdd-parallel/SKILL.md with CLI subcommands",
                is_critical=True,
            )
        )

    return findings


def _extract_headings(text: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    in_code = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            headings.append((len(m.group(1)), m.group(2).strip()))
    return headings


def check_i18n_parity(repo_root: Path) -> list[DriftFinding]:
    """Verify header parity between canonical docs and translations."""
    findings: list[DriftFinding] = []
    i18n = repo_root / "docs" / "i18n"
    pairs = [
        (repo_root / "README.md", i18n / "ru" / "README.md"),
        (repo_root / "README.md", i18n / "zh-CN" / "README.md"),
        (repo_root / "README.md", i18n / "es" / "README.md"),
        (repo_root / "README.md", i18n / "pt-BR" / "README.md"),
        (repo_root / "README.md", i18n / "ja" / "README.md"),
        (repo_root / "README.md", i18n / "ko" / "README.md"),
        (repo_root / "ARCHITECTURE.md", i18n / "ru" / "ARCHITECTURE.md"),
        (repo_root / "ARCHITECTURE.md", i18n / "zh-CN" / "ARCHITECTURE.md"),
        (repo_root / "ARCHITECTURE.md", i18n / "es" / "ARCHITECTURE.md"),
        (repo_root / "ARCHITECTURE.md", i18n / "pt-BR" / "ARCHITECTURE.md"),
        (repo_root / "ARCHITECTURE.md", i18n / "ja" / "ARCHITECTURE.md"),
        (repo_root / "ARCHITECTURE.md", i18n / "ko" / "ARCHITECTURE.md"),
        (
            repo_root / "docs" / "dossier" / "01_CORE_PRODUCT_BRIEF.md",
            repo_root / "docs" / "i18n" / "ru" / "dossier" / "01_CORE_PRODUCT_BRIEF.md",
        ),
        (
            repo_root / "docs" / "dossier" / "02_SYSTEM_CAPABILITIES_WHITE_PAPER.md",
            repo_root
            / "docs"
            / "i18n"
            / "ru"
            / "dossier"
            / "02_SYSTEM_CAPABILITIES_WHITE_PAPER.md",
        ),
        (
            repo_root / "docs" / "dossier" / "03_INTEGRATION_AND_ECOSYSTEM_SPEC.md",
            repo_root
            / "docs"
            / "i18n"
            / "ru"
            / "dossier"
            / "03_INTEGRATION_AND_ECOSYSTEM_SPEC.md",
        ),
        (
            repo_root / "docs" / "dossier" / "04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md",
            repo_root
            / "docs"
            / "i18n"
            / "ru"
            / "dossier"
            / "04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md",
        ),
        (
            repo_root / "docs" / "dossier" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
            repo_root
            / "docs"
            / "i18n"
            / "ru"
            / "dossier"
            / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
        ),
        (
            repo_root / "docs" / "dossier" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
            repo_root / "docs" / "i18n" / "zh-CN" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
        ),
        (
            repo_root / "docs" / "dossier" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
            repo_root / "docs" / "i18n" / "es" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
        ),
        (
            repo_root / "docs" / "dossier" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
            repo_root / "docs" / "i18n" / "pt-BR" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
        ),
        (
            repo_root / "docs" / "dossier" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
            repo_root / "docs" / "i18n" / "ja" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
        ),
        (
            repo_root / "docs" / "dossier" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
            repo_root / "docs" / "i18n" / "ko" / "07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md",
        ),
    ]

    for src_path, dst_path in pairs:
        if not src_path.is_file() or not dst_path.is_file():
            continue
        src_text = src_path.read_text(encoding="utf-8")
        dst_text = dst_path.read_text(encoding="utf-8")
        if "<!-- status: draft -->" in src_text or "<!-- status: draft -->" in dst_text:
            continue

        src_h2 = [h for h in _extract_headings(src_text) if h[0] == 2]
        dst_h2 = [h for h in _extract_headings(dst_text) if h[0] == 2]
        if len(src_h2) != len(dst_h2):
            findings.append(
                DriftFinding(
                    category="[DOCS]",
                    summary=(
                        f"H2 count mismatch between {src_path.name} ({len(src_h2)}) and "
                        f"{dst_path.parent.name}/{dst_path.name} ({len(dst_h2)})"
                    ),
                    remediation=f"Synchronize section headers in {dst_path.as_posix()}",
                    is_critical=True,
                )
            )

    return findings


def check_delta_blast_radius(
    repo_root: Path,
    delta_files: Sequence[str],
) -> list[DriftFinding]:
    """Project modified files to necessary documentation and matrix updates."""
    findings: list[DriftFinding] = []

    adapter_changes = [f for f in delta_files if f.startswith("src/sdd_parallel/adapters/")]
    if adapter_changes:
        matrix_file = (
            repo_root
            / ".agents"
            / "skills"
            / "sdd-dev-playbooks"
            / "references"
            / "adapter-touchpoint-matrix.md"
        )
        if matrix_file.is_file():
            matrix_text = matrix_file.read_text(encoding="utf-8")
            kits_in_matrix = set(re.findall(r"\|\s*\*\*`([a-z0-9_-]+)`\*\*", matrix_text))
            for kit in sorted(set(ADAPTER_REGISTRY.keys()) - kits_in_matrix):
                findings.append(
                    DriftFinding(
                        category="[CODE]",
                        summary=f"Modified adapter '{kit}' missing from touchpoint matrix",
                        details=[
                            f"Missing entry in '{matrix_file.relative_to(repo_root).as_posix()}'"
                        ],
                        remediation=(
                            "Run playbook: .agents/skills/sdd-dev-playbooks/references/"
                            "adapter-touchpoint-matrix.md"
                        ),
                        is_critical=True,
                    )
                )

    if "src/sdd_parallel/cli.py" in delta_files:
        skill_file = repo_root / ".agent" / "skills" / "sdd-parallel" / "SKILL.md"
        if skill_file.is_file():
            parser = _build_parser()
            subparsers_action = next(
                a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
            )
            cli_commands = set(subparsers_action.choices.keys())
            skill_text = skill_file.read_text(encoding="utf-8")
            missing = [
                cmd
                for cmd in sorted(cli_commands)
                if f"`{cmd}`" not in skill_text
                and f"`sdd-parallel {cmd}" not in skill_text
                and f"{cmd}:" not in skill_text
            ]
            if missing:
                findings.append(
                    DriftFinding(
                        category="[CLI]",
                        summary=f"CLI subcommands {missing} missing from SKILL.md",
                        details=[f"Commands {missing} need documentation in SKILL.md"],
                        remediation="Update skill: .agent/skills/sdd-parallel/SKILL.md",
                        is_critical=True,
                    )
                )

    return findings


def check_knowledge_graph_integrity(repo_root: Path) -> list[DriftFinding]:
    """Verify reachability, INS-* cross-bridges, and Progressive Disclosure boundaries."""
    findings: list[DriftFinding] = []
    gtm_index = repo_root / "docs" / "marketing" / "MASTER_GTM_INDEX.md"
    master_index = repo_root / "MASTER_INDEX.md"
    agents_md = repo_root / "AGENTS.md"
    ledger_md = repo_root / "docs" / "marketing" / "INTELLIGENCE_LEDGER.md"

    if not gtm_index.is_file() or not master_index.is_file():
        return findings

    gtm_text = gtm_index.read_text(encoding="utf-8")
    master_text = master_index.read_text(encoding="utf-8")

    # 1. Cross-dispatcher bridge check (L0 <-> Engineering <-> GTM)
    if agents_md.is_file():
        agents_text = agents_md.read_text(encoding="utf-8")
        for required_link in ("MASTER_INDEX.md", "MASTER_GTM_INDEX.md", "INTELLIGENCE_LEDGER.md"):
            if required_link not in agents_text:
                findings.append(
                    DriftFinding(
                        category="[KNOWLEDGE_GRAPH]",
                        summary=f"L0 AGENTS.md missing routing link to '{required_link}'",
                        remediation=f"Add '{required_link}' router entry to AGENTS.md §3",
                        is_critical=True,
                    )
                )

    if "MASTER_GTM_INDEX.md" not in master_text:
        findings.append(
            DriftFinding(
                category="[KNOWLEDGE_GRAPH]",
                summary=(
                    "MASTER_INDEX.md missing cross-bridge to docs/marketing/MASTER_GTM_INDEX.md"
                ),
                remediation="Link MASTER_GTM_INDEX.md in MASTER_INDEX.md header",
                is_critical=True,
            )
        )

    if "MASTER_INDEX.md" not in gtm_text:
        findings.append(
            DriftFinding(
                category="[KNOWLEDGE_GRAPH]",
                summary="MASTER_GTM_INDEX.md missing cross-bridge to MASTER_INDEX.md",
                remediation="Link MASTER_INDEX.md in MASTER_GTM_INDEX.md header",
                is_critical=True,
            )
        )

    # 2. Zero Orphan Marketing Registries & GTM Task Specs
    marketing_dir = repo_root / "docs" / "marketing"
    for md_file in sorted(marketing_dir.glob("*.md")):
        if md_file.name == "MASTER_GTM_INDEX.md":
            continue
        if md_file.name not in gtm_text:
            findings.append(
                DriftFinding(
                    category="[KNOWLEDGE_GRAPH]",
                    summary=(
                        f"Orphan marketing registry '{md_file.name}' "
                        "not routed in MASTER_GTM_INDEX.md"
                    ),
                    remediation=f"Register '{md_file.name}' in docs/marketing/MASTER_GTM_INDEX.md",
                    is_critical=True,
                )
            )

    gtm_tasks_dir = marketing_dir / "tasks"
    if gtm_tasks_dir.is_dir():
        for spec_file in sorted(gtm_tasks_dir.glob("gtm_*.md")):
            if spec_file.name not in gtm_text:
                findings.append(
                    DriftFinding(
                        category="[KNOWLEDGE_GRAPH]",
                        summary=(
                            f"Orphan GTM task spec 'tasks/{spec_file.name}' "
                            "not routed in MASTER_GTM_INDEX.md"
                        ),
                        remediation=(
                            f"Add 'tasks/{spec_file.name}' to MASTER_GTM_INDEX.md queue or archive"
                        ),
                        is_critical=True,
                    )
                )

    # 3. Context Boundary, Token-Hygiene & Handoff Contract enforcement on active specs
    active_specs: list[Path] = []
    if gtm_tasks_dir.is_dir():
        active_specs.extend(sorted(gtm_tasks_dir.glob("gtm_*.md")))
    phases_dir = repo_root / "phases"
    if phases_dir.is_dir():
        for task_num in ("29", "30", "31", "32"):
            active_specs.extend(sorted(phases_dir.glob(f"task_{task_num}_*.md")))

    for spec in active_specs:
        spec_text = spec.read_text(encoding="utf-8")
        rel_spec = spec.relative_to(repo_root).as_posix()
        if "Context Boundary" not in spec_text or "Handoff Contract" not in spec_text:
            findings.append(
                DriftFinding(
                    category="[KNOWLEDGE_GRAPH]",
                    summary=(
                        f"Spec '{rel_spec}' missing 'Context Boundary' "
                        "or 'Handoff Contract' section"
                    ),
                    remediation=(
                        f"Add mandatory Context Boundary and Handoff Contract to {rel_spec}"
                    ),
                    is_critical=True,
                )
            )
        # Check that Files Allowed to Read does not pull in ARCHIVE_INDEX.md
        read_block_match = re.search(
            r"###\s+Files Allowed to Read.*?(?=###|\n##\s+|\Z)",
            spec_text,
            re.DOTALL,
        )
        if read_block_match:
            read_block = read_block_match.group(0)
            if "ARCHIVE_INDEX.md" in read_block:
                findings.append(
                    DriftFinding(
                        category="[KNOWLEDGE_GRAPH]",
                        summary=(
                            f"Token bloat in '{rel_spec}': ARCHIVE_INDEX.md "
                            "listed in Files Allowed to Read"
                        ),
                        remediation=(
                            f"Remove ARCHIVE_INDEX.md from Files Allowed to Read in {rel_spec}"
                        ),
                        is_critical=True,
                    )
                )

    # 4. Bidirectional Insight Bridge: INTELLIGENCE_LEDGER.md <-> Task/GTM specs
    if ledger_md.is_file():
        ledger_text = ledger_md.read_text(encoding="utf-8")
        for line in ledger_text.splitlines():
            ins_match = re.search(r"\*\*(INS-\d{4}-\d{2}-\d{2}-\d{2})\*\*", line)
            if not ins_match:
                continue
            ins_id = ins_match.group(1)

            # Check referenced engineering tasks (Task NN)
            for task_num in set(re.findall(r"\bTask\s+(\d+)\b", line)):
                matched_tasks = (
                    list(phases_dir.glob(f"task_{task_num}_*.md"))
                    if phases_dir.is_dir()
                    else []
                )
                for t_file in matched_tasks:
                    t_text = t_file.read_text(encoding="utf-8")
                    if ins_id not in t_text:
                        rel_t = t_file.relative_to(repo_root).as_posix()
                        findings.append(
                            DriftFinding(
                                category="[KNOWLEDGE_GRAPH]",
                                summary=(
                                    f"Broken insight bridge: '{ins_id}' references "
                                    f"Task {task_num}, but '{rel_t}' lacks '{ins_id}'"
                                ),
                                remediation=f"Add '{ins_id}' reference to {rel_t}",
                                is_critical=True,
                            )
                        )

            # Check referenced GTM tasks (GTM-NN)
            for gtm_num in set(re.findall(r"\bGTM-(\d{2})\b", line)):
                matched_gtm = (
                    list(gtm_tasks_dir.glob(f"gtm_{gtm_num}_*.md"))
                    if gtm_tasks_dir.is_dir()
                    else []
                )
                for g_file in matched_gtm:
                    g_text = g_file.read_text(encoding="utf-8")
                    if ins_id not in g_text:
                        rel_g = g_file.relative_to(repo_root).as_posix()
                        findings.append(
                            DriftFinding(
                                category="[KNOWLEDGE_GRAPH]",
                                summary=(
                                    f"Broken insight bridge: '{ins_id}' references "
                                    f"GTM-{gtm_num}, but '{rel_g}' lacks '{ins_id}'"
                                ),
                                remediation=f"Add '{ins_id}' reference to {rel_g}",
                                is_critical=True,
                            )
                        )

    return findings


def run_drift_inspection(
    repo_root: Path,
    staged_only: bool = False,
) -> list[DriftFinding]:
    """Execute complete drift inspection across repository."""
    findings: list[DriftFinding] = []

    delta_files = get_git_delta(repo_root, staged_only=staged_only)

    # 1. Workstation leaks
    findings.extend(check_workstation_leaks(repo_root, delta_files))

    # 2. AGENTS.md budget and cleanliness
    findings.extend(check_agents_budget(repo_root))

    # 3. Adapter registry parity
    findings.extend(check_adapter_registry(repo_root))

    # 4. CLI vs SKILL.md sync
    findings.extend(check_cli_skill_sync(repo_root))

    # 5. Multilingual parity
    findings.extend(check_i18n_parity(repo_root))

    # 6. Knowledge graph reachability & INS-* bridge integrity
    findings.extend(check_knowledge_graph_integrity(repo_root))

    # 7. Delta blast-radius projection
    if delta_files:
        findings.extend(check_delta_blast_radius(repo_root, delta_files))

    return findings


def generate_manifest_markdown(
    findings: list[DriftFinding],
    delta_files: Sequence[str],
) -> str:
    """Format structured gap manifest in Markdown."""
    lines: list[str] = [
        "# 🛡️ Architecture & Documentation Drift Manifest",
    ]

    if not findings:
        lines.append("- Status: ✅ CLEAN (0 gaps detected)\n")
        lines.append("## 🔍 Detected Deltas & Required Actions:")
        lines.append(
            "- All architecture invariants, CLI documentation, and i18n parity gates satisfied.\n"
        )
        lines.append("## 📋 Remediation Checklist:")
        lines.append("- [x] Repository clean and synchronized.\n")
        return "\n".join(lines)

    status_tag = f"⚠️ GAPS DETECTED ({len(findings)} items require sync)"
    lines.append(f"- Status: {status_tag}\n")

    lines.append("## 🔍 Detected Deltas & Required Actions:")
    for idx, f in enumerate(findings, 1):
        icon = "❌" if f.is_critical else "⚠️"
        lines.append(f"{idx}. {f.category} {f.summary}")
        for d in f.details:
            lines.append(f"   - {icon} {d}")
        if not f.details:
            lines.append(f"   - {icon} Action required")

    lines.append("\n## 📋 Remediation Checklist:")
    for f in findings:
        if f.remediation:
            lines.append(f"- [ ] {f.remediation}")
        else:
            lines.append(f"- [ ] Resolve: {f.summary}")

    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for drift_sentinel.py."""
    parser = argparse.ArgumentParser(
        prog="drift_sentinel",
        description="Architecture Drift Sentinel & Universal Delta Inspector for sdd-parallel",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Target repository root path",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Blocking gate mode (exit code 1 if critical gaps found)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate .tmp/DRIFT_MANIFEST.md session report",
    )
    parser.add_argument(
        "--staged-only",
        action="store_true",
        help="Inspect only git staged files",
    )
    parser.add_argument(
        "--auto-fix",
        action="store_true",
        help="Automatically remediate detected drift issues (Tier 1 & Tier 2)",
    )

    args = parser.parse_args(argv)
    repo_root = args.repo.resolve()

    delta_files = get_git_delta(repo_root, staged_only=args.staged_only)
    findings = run_drift_inspection(repo_root, staged_only=args.staged_only)

    if args.auto_fix and findings:
        from remediate_drift import remediate_repository

        remediate_repository(
            repo_root=repo_root,
            tier="all",
            dry_run=False,
            yes=True,
            findings=findings,
        )
        delta_files = get_git_delta(repo_root, staged_only=args.staged_only)
        findings = run_drift_inspection(repo_root, staged_only=args.staged_only)

    manifest_md = generate_manifest_markdown(findings, delta_files)

    # Persist manifest to .tmp
    tmp_dir = repo_root / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = tmp_dir / "DRIFT_MANIFEST.md"
    manifest_path.write_text(manifest_md, encoding="utf-8")

    # Persist JSON telemetry
    telemetry_path = tmp_dir / "drift-report.json"
    telemetry_data = {
        "status": "clean" if not findings else "gaps_detected",
        "total_findings": len(findings),
        "critical_findings": sum(1 for f in findings if f.is_critical),
        "delta_files_count": len(delta_files),
        "findings": [
            {
                "category": f.category,
                "summary": f.summary,
                "details": f.details,
                "remediation": f.remediation,
                "is_critical": f.is_critical,
            }
            for f in findings
        ],
    }
    telemetry_path.write_text(json.dumps(telemetry_data, indent=2), encoding="utf-8")

    print(manifest_md)

    if args.strict:
        critical_count = sum(1 for f in findings if f.is_critical)
        if critical_count > 0:
            sys.stderr.write(
                f"\n[drift_sentinel] Strict gate failed: {critical_count} critical gap(s) found.\n"
            )
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
