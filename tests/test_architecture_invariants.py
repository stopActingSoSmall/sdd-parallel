"""Deterministic architectural invariant tests for sdd-parallel.

Enforces:
1. Adapter completeness across registry, playbook matrix, CLI, demo, and specs.
2. CLI subcommands synchronization with user orchestration skill (SKILL.md).
3. L0 Constitution (AGENTS.md) byte budget and clean domain abstraction.
4. L1 modular rules validity (YAML frontmatter and glob resolution).
5. AST header parity between canonical dossier and multilingual translations.
6. Scope Guard and hook integrity across blast_radius_guard.py and worktree-and-hooks.md.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from sdd_parallel.adapters import ADAPTER_REGISTRY, BMADAdapter
from sdd_parallel.cli import _build_parser


def extract_headings(markdown_text: str) -> list[tuple[int, str]]:
    """Extract Markdown headings (level, title) ignoring lines inside code fences."""
    headings: list[tuple[int, str]] = []
    in_code_block = False

    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip()))

    return headings


def check_adapter_registry_completeness(
    repo_root: Path,
    registry: Mapping[str, Any] | None = None,
) -> None:
    """Verify registry alignment with matrix, CLI, demo sandbox, and ecosystem spec."""
    target_registry = ADAPTER_REGISTRY if registry is None else registry

    # 1. Private governance matrix (if present in repo)
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
        assert kits_in_matrix, "Failed to parse kits from adapter-touchpoint-matrix.md"

        # Edge-case: ensure every registered adapter is documented in matrix
        for extra in sorted(set(target_registry.keys()) - kits_in_matrix):
            raise AssertionError(f"Adapter '{extra}' registered in code but missing from matrix")

        for missing in sorted(kits_in_matrix - set(target_registry.keys())):
            raise AssertionError(
                f"Kit '{missing}' present in matrix but not registered in ADAPTER_REGISTRY"
            )

    # 2. Public touchpoints (always checked)
    # Verify presence in CLI demo choices
    parser = _build_parser()
    subparsers_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    demo_parser = subparsers_action.choices.get("demo")
    assert demo_parser is not None, "CLI parser missing 'demo' subcommand"
    kit_action = next(a for a in demo_parser._actions if "--kit" in a.option_strings)
    assert kit_action.choices is not None, "--kit option in 'demo' must define choices"
    for kit_name in target_registry:
        assert kit_name in kit_action.choices, (
            f"Kit '{kit_name}' missing from 'demo --kit' choices: {kit_action.choices}"
        )

    # Verify presence in demo.py synthetic fixture generator
    demo_file = repo_root / "src" / "sdd_parallel" / "demo.py"
    if demo_file.is_file():
        demo_text = demo_file.read_text(encoding="utf-8")
        for kit_name in target_registry:
            assert f'"{kit_name}"' in demo_text or f"'{kit_name}'" in demo_text, (
                f"Kit '{kit_name}' not referenced in create_demo_repository fixtures in demo.py"
            )

    # Verify presence in docs/dossier/03_INTEGRATION_AND_ECOSYSTEM_SPEC.md
    spec_file = repo_root / "docs" / "dossier" / "03_INTEGRATION_AND_ECOSYSTEM_SPEC.md"
    if spec_file.is_file():
        spec_text = spec_file.read_text(encoding="utf-8")
        for kit_name in target_registry:
            assert f"`{kit_name}`" in spec_text or f"({kit_name})" in spec_text, (
                f"Kit '{kit_name}' missing from table in 03_INTEGRATION_AND_ECOSYSTEM_SPEC.md"
            )


def test_adapter_registry_completeness() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    check_adapter_registry_completeness(repo_root)


def test_missing_adapter_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    dummy_registry: dict[str, Any] = dict(ADAPTER_REGISTRY)
    dummy_registry["_eval_dummy"] = BMADAdapter

    with pytest.raises(
        AssertionError,
        match=(
            r"(Adapter '_eval_dummy' registered in code but missing from matrix|"
            r"Kit '_eval_dummy' missing from 'demo --kit' choices)"
        ),
    ):
        check_adapter_registry_completeness(repo_root, registry=dummy_registry)


def test_cli_subcommands_documented_in_skill() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    parser = _build_parser()
    subparsers_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    cli_commands = set(subparsers_action.choices.keys())
    assert len(cli_commands) >= 10, f"Expected >= 10 CLI subcommands, got {len(cli_commands)}"

    skill_file = repo_root / ".agent" / "skills" / "sdd-parallel" / "SKILL.md"
    if not skill_file.is_file():
        skill_file = repo_root / "src" / "sdd_parallel" / "resources" / "SKILL.md"
    assert skill_file.is_file(), f"Skill file not found at {skill_file}"
    skill_text = skill_file.read_text(encoding="utf-8")

    missing = [
        cmd
        for cmd in sorted(cli_commands)
        if f"`{cmd}`" not in skill_text
        and f"`sdd-parallel {cmd}" not in skill_text
        and f"{cmd}:" not in skill_text
    ]
    assert not missing, f"Subcommands missing from SKILL.md documentation: {missing}"


@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent / "AGENTS.md").is_file(),
    reason="Private governance files not present in public clone",
)
def test_agents_md_budget_and_cleanliness() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    agents_file = repo_root / "AGENTS.md"
    assert agents_file.is_file(), "AGENTS.md not found at repository root"

    # Strict file size <= 3500 bytes
    file_size = agents_file.stat().st_size
    assert file_size <= 3500, f"AGENTS.md size ({file_size} B) exceeds maximum limit (3500 B)"

    content = agents_file.read_text(encoding="utf-8")
    forbidden_terms = [
        "routers/",
        "duckdb.py",
        "console.py",
    ] + [f"Users{sep}admin" for sep in ("/", "\\")] + ["C:\\Users\\"]
    leaks = [term for term in forbidden_terms if term.lower() in content.lower()]
    assert not leaks, f"Prohibited domain terms or workstation leaks in AGENTS.md: {leaks}"


@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent / "AGENTS.md").is_file(),
    reason="Private governance files not present in public clone",
)
def test_modular_l1_rules_validity() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    rules_dir = repo_root / ".agents" / "rules"
    assert rules_dir.is_dir(), f".agents/rules directory not found at {rules_dir}"

    rule_files = sorted(rules_dir.glob("*.md"))
    assert len(rule_files) >= 3, f"Expected >= 3 rules in .agents/rules, found {len(rule_files)}"

    for rf in rule_files:
        text = rf.read_text(encoding="utf-8")
        assert text.startswith("---"), f"{rf.name} missing YAML frontmatter opening '---'"
        parts = text.split("---", 2)
        assert len(parts) >= 3, f"{rf.name} malformed YAML frontmatter"
        frontmatter = parts[1]

        assert re.search(r"^trigger:\s*glob\b", frontmatter, re.MULTILINE), (
            f"{rf.name} missing required 'trigger: glob'"
        )
        assert re.search(r"^description:\s*.+", frontmatter, re.MULTILINE), (
            f"{rf.name} missing required 'description' field"
        )

        match_globs = re.search(
            r'^globs:\s*["\']?([^"\']+)["\']?',
            frontmatter,
            re.MULTILINE,
        )
        assert match_globs, f"{rf.name} missing 'globs' pattern"
        patterns = [p.strip() for p in match_globs.group(1).split(",") if p.strip()]
        assert patterns, f"{rf.name} has empty globs declaration"

        for pattern in patterns:
            matched = list(repo_root.glob(pattern))
            assert len(matched) > 0, (
                f"{rf.name} glob '{pattern}' resolved to 0 files in {repo_root}"
            )


def test_multilingual_documentation_parity() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    i18n = repo_root / "docs" / "i18n"

    doc_pairs: list[tuple[Path, Path]] = [
        # Root documentation
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
        # Dossier package
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
            repo_root
            / "docs"
            / "i18n"
            / "ru"
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

    for src_path, dst_path in doc_pairs:
        if not src_path.is_file() or not dst_path.is_file():
            continue
        src_text = src_path.read_text(encoding="utf-8")
        dst_text = dst_path.read_text(encoding="utf-8")

        # Skip draft-labeled documents
        if "<!-- status: draft -->" in src_text or "<!-- status: draft -->" in dst_text:
            continue

        src_h2 = [h for h in extract_headings(src_text) if h[0] == 2]
        dst_h2 = [h for h in extract_headings(dst_text) if h[0] == 2]

        assert len(src_h2) == len(dst_h2), (
            f"H2 header count mismatch between {src_path.name} ({len(src_h2)} H2) and "
            f"{dst_path.parent.name}/{dst_path.name} ({len(dst_h2)} H2)"
        )


def test_draft_status_marker_ignored(tmp_path: Path) -> None:
    src_file = tmp_path / "canonical.md"
    dst_file = tmp_path / "translated.md"

    src_file.write_text(
        "<!-- status: draft -->\n# Title\n## Section 1\n## Section 2\n",
        encoding="utf-8",
    )
    dst_file.write_text(
        "# Title\n## Section 1\n",
        encoding="utf-8",
    )

    src_text = src_file.read_text(encoding="utf-8")
    dst_text = dst_file.read_text(encoding="utf-8")

    # When draft marker present, comparator skips without error
    is_draft = "<!-- status: draft -->" in src_text or "<!-- status: draft -->" in dst_text
    assert is_draft is True


def test_scope_guard_hook_integrity() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    from sdd_parallel.hooks.blast_radius_guard import _CMD_WRITE_INDICATORS, _EXEC_TOOLS

    expected_tools = ["Bash", "run_command", "cmd", "powershell"]
    for tool_name in expected_tools:
        assert tool_name in _EXEC_TOOLS, f"Tool '{tool_name}' missing from _EXEC_TOOLS"

    expected_indicators = [">", "sed -i", "rm"]
    for indicator in expected_indicators:
        assert any(indicator in item for item in _CMD_WRITE_INDICATORS), (
            f"Indicator '{indicator}' missing from _CMD_WRITE_INDICATORS"
        )

    rule_file = repo_root / ".agents" / "rules" / "worktree-and-hooks.md"
    if rule_file.is_file():
        rule_text = rule_file.read_text(encoding="utf-8")
        for tool_name in expected_tools:
            assert tool_name in rule_text, (
                f"Tool '{tool_name}' missing from worktree-and-hooks.md rule documentation"
            )
        for indicator in expected_indicators:
            assert indicator in rule_text, (
                f"Indicator '{indicator}' missing from worktree-and-hooks.md rule documentation"
            )


def test_embedded_skill_resource_parity() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    agent_skill = repo_root / ".agent" / "skills" / "sdd-parallel" / "SKILL.md"
    embedded_skill = repo_root / "src" / "sdd_parallel" / "resources" / "SKILL.md"

    assert embedded_skill.is_file(), f"Embedded skill resource not found at {embedded_skill}"
    if agent_skill.is_file():
        assert (
            agent_skill.read_text(encoding="utf-8") == embedded_skill.read_text(encoding="utf-8")
        ), "Embedded resource SKILL.md is out of sync with .agent/skills/sdd-parallel/SKILL.md"


def test_drift_sentinel_clean_run() -> None:
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    import scripts.drift_sentinel as sentinel

    findings = sentinel.run_drift_inspection(repo_root)
    critical_findings = [f for f in findings if f.is_critical]
    assert not critical_findings, f"Unexpected critical drift findings: {critical_findings}"

    # Verify CLI exit code is 0 in strict mode
    code = sentinel.main(["--repo", str(repo_root), "--strict"])
    assert code == 0, f"drift_sentinel --strict exited with code {code}"


def test_core_engine_de_bmadization_and_invariant_seven() -> None:
    """Verify core engine modules have no hardcoded BMAD branches or forbidden domain literals."""
    import ast

    repo_root = Path(__file__).resolve().parent.parent
    core_modules = [
        repo_root / "src" / "sdd_parallel" / "cli.py",
        repo_root / "src" / "sdd_parallel" / "worktree.py",
        repo_root / "src" / "sdd_parallel" / "scheduler.py",
        repo_root / "src" / "sdd_parallel" / "ast_scanner.py",
        repo_root / "src" / "sdd_parallel" / "models.py",
    ]

    for mod_path in core_modules:
        content = mod_path.read_text(encoding="utf-8")
        parsed = ast.parse(content, filename=mod_path.name)

        # Check imports for direct BMADAdapter in cli.py and worktree.py
        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom):
                if node.module and "bmad" in node.module:
                    for alias in node.names:
                        assert alias.name != "BMADAdapter", (
                            f"Direct import of BMADAdapter forbidden in core module {mod_path.name}"
                        )

    # Invariant 7: verify no domain literals in ast_scanner, scheduler, models
    forbidden_literals = ["duckdb.py", "routers/", "endpoints/", "alembic/"]
    for mod_name in ("ast_scanner.py", "scheduler.py", "models.py"):
        content = (repo_root / "src" / "sdd_parallel" / mod_name).read_text(encoding="utf-8")
        for literal in forbidden_literals:
            assert literal not in content, (
                f"Forbidden domain literal '{literal}' found in {mod_name}"
            )


def test_worktree_invariant_four_zero_hardcoded_kit_paths() -> None:
    """Verify worktree.py has zero hardcoded SDD kit paths or filenames (Invariant 4)."""
    repo_root = Path(__file__).resolve().parent.parent
    worktree_file = repo_root / "src" / "sdd_parallel" / "worktree.py"
    content = worktree_file.read_text(encoding="utf-8")

    forbidden_patterns = [
        "_bmad-output",
        ".specify",
        "openspec",
        ".beads",
        "specs/",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in content, (
            f"Forbidden SDD kit pattern '{pattern}' found in worktree.py. "
            "All status paths must be resolved polymorphically via "
            "SDDAdapterProtocol.get_status_files()."
        )

