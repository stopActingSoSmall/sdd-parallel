"""Tests for Task 50 Python AST-aware 3-way additive registry merger."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from sdd_parallel.models import EffortTier, TaskNode, Wave, WavePlan, WaveTrack
from sdd_parallel.registry_merger import (
    is_python_registry_candidate,
    merge_python_additive_registry,
)
from sdd_parallel.worktree import WorktreeManager


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return proc.stdout.strip()


def _init_git_repo(repo_root: Path) -> None:
    _git(repo_root, "init")
    _git(repo_root, "config", "user.name", "Test User")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "checkout", "-B", "master")


def test_is_python_registry_candidate_filters_paths() -> None:
    assert is_python_registry_candidate("src/app/cli.py") is True
    assert is_python_registry_candidate("src/app/__init__.py") is True
    assert is_python_registry_candidate("pkg/mcp/server.py") is True
    assert is_python_registry_candidate("src/service/server.py") is False
    assert is_python_registry_candidate("src/service/router.py") is False
    assert is_python_registry_candidate("src/service/routes.py") is False
    assert is_python_registry_candidate("src/service/registry.py") is False
    assert is_python_registry_candidate("src/app/cli.py", additive_files=set()) is False
    assert is_python_registry_candidate("src/app/utils.py") is False
    assert is_python_registry_candidate("README.md") is False

    assert is_python_registry_candidate(
        "src/app/custom_reg.py", additive_files=["src/app/custom_reg.py"]
    ) is True
    assert is_python_registry_candidate(
        "src/app/cli.py", additive_files=["src/app/custom_reg.py"]
    ) is False
    assert is_python_registry_candidate(
        "docs/tasks.md", additive_files=["docs/tasks.md"]
    ) is False


def test_concurrent_imports_and_eof_decorated_commands_merge_cleanly() -> None:
    base = (
        '"""CLI entrypoint."""\n\n'
        "from __future__ import annotations\n\n"
        "import typer\n"
        "from app.core import status_check\n\n"
        "app = typer.Typer()\n\n\n"
        '@app.command("status")\n'
        "def cmd_status() -> None:\n"
        "    status_check()\n\n\n"
        'if __name__ == "__main__":\n'
        "    app()\n"
    )
    ours = (
        '"""CLI entrypoint."""\n\n'
        "from __future__ import annotations\n\n"
        "import typer\n"
        "from app.core import status_check\n"
        "from app.norm import normalize\n\n"
        "app = typer.Typer()\n\n\n"
        '@app.command("status")\n'
        "def cmd_status() -> None:\n"
        "    status_check()\n\n\n"
        '@app.command("normalize")\n'
        "def cmd_norm() -> None:\n"
        "    normalize()\n\n\n"
        'if __name__ == "__main__":\n'
        "    app()\n"
    )
    theirs = (
        '"""CLI entrypoint."""\n\n'
        "from __future__ import annotations\n\n"
        "import typer\n"
        "from app.core import status_check\n"
        "from app.exp import export_data\n\n"
        "app = typer.Typer()\n\n\n"
        '@app.command("status")\n'
        "def cmd_status() -> None:\n"
        "    status_check()\n\n\n"
        '@app.command("export")\n'
        "def cmd_exp() -> None:\n"
        "    export_data()\n\n\n"
        'if __name__ == "__main__":\n'
        "    app()\n"
    )

    merged = merge_python_additive_registry(base, ours, theirs)
    assert merged is not None
    ast.parse(merged)
    assert "from app.norm import normalize" in merged
    assert "from app.exp import export_data" in merged
    assert "def cmd_norm() -> None:" in merged
    assert "def cmd_exp() -> None:" in merged
    assert merged.index("def cmd_norm") < merged.index('if __name__ == "__main__":')
    assert merged.index("def cmd_exp") < merged.index('if __name__ == "__main__":')
    assert merged.rstrip().endswith("app()")


def test_multiline_from_import_parenthesized_merge() -> None:
    base = (
        "from app.utils import (\n"
        "    a,\n"
        "    b,\n"
        ")\n\n"
        "VALUE = a() + b()\n"
    )
    ours = (
        "from app.utils import (\n"
        "    a,\n"
        "    b,\n"
        "    c,\n"
        ")\n\n"
        "VALUE = a() + b()\n"
        "EXTRA_A = c()\n"
    )
    theirs = (
        "from app.utils import (\n"
        "    a,\n"
        "    b,\n"
        "    d,\n"
        ")\n\n"
        "VALUE = a() + b()\n"
        "EXTRA_B = d()\n"
    )

    merged = merge_python_additive_registry(base, ours, theirs)
    assert merged is not None
    tree = ast.parse(merged)
    imp = tree.body[0]
    assert isinstance(imp, ast.ImportFrom)
    assert [alias.name for alias in imp.names] == ["a", "b", "c", "d"]
    assert "(\n" in merged
    assert "EXTRA_A = c()" in merged
    assert "EXTRA_B = d()" in merged


def test_dunder_all_list_additive_merge_in_init_py() -> None:
    base = (
        '"""Package exports."""\n\n'
        "from .core import Core\n\n"
        '__all__ = ["Core"]\n'
    )
    ours = (
        '"""Package exports."""\n\n'
        "from .core import Core\n"
        "from .mod_x import X\n\n"
        '__all__ = ["Core", "X"]\n'
    )
    theirs = (
        '"""Package exports."""\n\n'
        "from .core import Core\n"
        "from .mod_y import Y\n\n"
        '__all__ = ["Core", "Y"]\n'
    )

    merged = merge_python_additive_registry(base, ours, theirs)
    assert merged is not None
    tree = ast.parse(merged)
    assert "from .mod_x import X" in merged
    assert "from .mod_y import Y" in merged

    all_assign = [
        stmt for stmt in tree.body if isinstance(stmt, ast.Assign)
    ][-1]
    assert isinstance(all_assign.value, ast.List)
    exported = [
        elt.value
        for elt in all_assign.value.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    ]
    assert exported == ["Core", "X", "Y"]


def test_fail_closed_when_both_branches_modify_same_existing_function() -> None:
    base = (
        "def main(cmd: str) -> int:\n"
        '    if cmd == "ping":\n'
        "        return 0\n"
        "    return 1\n"
    )
    ours = (
        "def main(cmd: str) -> int:\n"
        '    if cmd == "ping":\n'
        "        return 0\n"
        '    elif cmd == "norm":\n'
        "        return 2\n"
        "    return 1\n"
    )
    theirs = (
        "def main(cmd: str) -> int:\n"
        '    if cmd == "ping":\n'
        "        return 0\n"
        '    elif cmd == "export":\n'
        "        return 3\n"
        "    return 1\n"
    )

    assert merge_python_additive_registry(base, ours, theirs) is None


def test_fail_closed_on_duplicate_new_function_or_conflicting_import_alias() -> None:
    base = "import os\n\nBASE = 1\n"

    # Case 1: duplicate new function with different bodies
    ours_fn = base + "\n\ndef helper() -> int:\n    return 1\n"
    theirs_fn = base + "\n\ndef helper() -> int:\n    return 2\n"
    assert merge_python_additive_registry(base, ours_fn, theirs_fn) is None

    # Case 2: conflicting import alias
    ours_alias = "import os\nimport x as alias\n\nBASE = 1\n"
    theirs_alias = "import os\nimport y as alias\n\nBASE = 1\n"
    assert merge_python_additive_registry(base, ours_alias, theirs_alias) is None

    # Case 3: cross-kind symbol collision (new import vs new def)
    ours_imp_sym = "import os\nfrom pkg import helper\n\nBASE = 1\n"
    assert merge_python_additive_registry(base, ours_imp_sym, theirs_fn) is None


def test_syntax_error_and_complex_conflict_fail_closed_while_one_sided_succeeds() -> None:
    base = (
        "def existing() -> int:\n"
        "    return 1\n"
    )
    ours_valid = (
        "def existing() -> int:\n"
        "    return 42\n"
    )
    theirs_add = (
        "def existing() -> int:\n"
        "    return 1\n\n\n"
        "def added() -> int:\n"
        "    return 2\n"
    )
    merged = merge_python_additive_registry(base, ours_valid, theirs_add)
    assert merged is not None
    assert "return 42" in merged
    assert "def added() -> int:" in merged

    assert merge_python_additive_registry(base, "def broken(:\n", theirs_add) is None

    complex_base = "if True:\n    MODE = 'a'\n"
    complex_ours = "if True:\n    MODE = 'b'\n"
    complex_theirs = "if True:\n    MODE = 'c'\n"
    assert merge_python_additive_registry(complex_base, complex_ours, complex_theirs) is None


def test_worktree_merge_wave_integrates_two_cli_tracks_without_conflict(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "_bmad-output" / "implementation-artifacts").mkdir(parents=True)
    (repo / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml").write_text(
        "development_status:\n"
        "  1-1-norm: ready-for-dev\n"
        "  1-2-export: ready-for-dev\n",
        encoding="utf-8",
    )
    src_app = repo / "src" / "app"
    src_app.mkdir(parents=True)
    (src_app / "__init__.py").write_text("", encoding="utf-8")
    (src_app / "norm.py").write_text(
        "def normalize() -> str:\n    return 'norm'\n", encoding="utf-8"
    )
    (src_app / "exp.py").write_text(
        "def export_data() -> str:\n    return 'exp'\n", encoding="utf-8"
    )

    cli_base = (
        '"""Sample CLI."""\n\n'
        "from __future__ import annotations\n\n"
        "COMMANDS: dict[str, object] = {}\n\n\n"
        "def command(name: str):\n"
        "    def _wrap(fn):\n"
        "        COMMANDS[name] = fn\n"
        "        return fn\n"
        "    return _wrap\n\n\n"
        '@command("status")\n'
        "def cmd_status() -> str:\n"
        "    return 'ok'\n\n\n"
        'if __name__ == "__main__":\n'
        "    print(sorted(COMMANDS))\n"
    )
    (src_app / "cli.py").write_text(cli_base, encoding="utf-8")
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: base repo")

    gate_cmd = f"{sys.executable.replace(chr(92), '/')} -m py_compile src/app/cli.py"
    plan = WavePlan(
        sdd_kit="bmad",
        additive_registries=["src/app/cli.py"],
        tasks=[
            TaskNode(
                id="1-1-norm",
                title="Add normalize command",
                write_files=["src/app/norm.py", "src/app/cli.py"],
                effort=EffortTier.LOW,
            ),
            TaskNode(
                id="1-2-export",
                title="Add export command",
                write_files=["src/app/exp.py", "src/app/cli.py"],
                effort=EffortTier.LOW,
            ),
        ],
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["1-1-norm"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/app/norm.py"],
                        additive_shared_files=["src/app/cli.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["1-2-export"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/app/exp.py"],
                        additive_shared_files=["src/app/cli.py"],
                    ),
                ],
                checkpoint_gates=[gate_cmd],
            )
        ],
        edges=[],
    )

    manager = WorktreeManager(repo)
    manager.prepare_wave(plan, 1)

    wt_a = manager.worktree_path_for_track(1, "track-a")
    wt_b = manager.worktree_path_for_track(1, "track-b")

    cli_a = (
        '"""Sample CLI."""\n\n'
        "from __future__ import annotations\n\n"
        "from app.norm import normalize\n\n"
        "COMMANDS: dict[str, object] = {}\n\n\n"
        "def command(name: str):\n"
        "    def _wrap(fn):\n"
        "        COMMANDS[name] = fn\n"
        "        return fn\n"
        "    return _wrap\n\n\n"
        '@command("status")\n'
        "def cmd_status() -> str:\n"
        "    return 'ok'\n\n\n"
        '@command("normalize")\n'
        "def cmd_norm() -> str:\n"
        "    return normalize()\n\n\n"
        'if __name__ == "__main__":\n'
        "    print(sorted(COMMANDS))\n"
    )
    (wt_a / "src" / "app" / "cli.py").write_text(cli_a, encoding="utf-8")
    _git(wt_a, "add", "src/app/cli.py")
    _git(wt_a, "commit", "-m", "feat: add normalize command")

    cli_b = (
        '"""Sample CLI."""\n\n'
        "from __future__ import annotations\n\n"
        "from app.exp import export_data\n\n"
        "COMMANDS: dict[str, object] = {}\n\n\n"
        "def command(name: str):\n"
        "    def _wrap(fn):\n"
        "        COMMANDS[name] = fn\n"
        "        return fn\n"
        "    return _wrap\n\n\n"
        '@command("status")\n'
        "def cmd_status() -> str:\n"
        "    return 'ok'\n\n\n"
        '@command("export")\n'
        "def cmd_exp() -> str:\n"
        "    return export_data()\n\n\n"
        'if __name__ == "__main__":\n'
        "    print(sorted(COMMANDS))\n"
    )
    (wt_b / "src" / "app" / "cli.py").write_text(cli_b, encoding="utf-8")
    _git(wt_b, "add", "src/app/cli.py")
    _git(wt_b, "commit", "-m", "feat: add export command")

    merged = manager.merge_wave(
        plan,
        1,
        run_quality_gates=True,
        partial_merge=True,
    )
    assert len(merged) == 2
    assert manager.last_merge_outcome is not None
    assert manager.last_merge_outcome.rejected_tracks == {}
    assert len(manager.last_merge_outcome.merged_branches) == 2

    final_cli = (src_app / "cli.py").read_text(encoding="utf-8")
    ast.parse(final_cli)
    assert "from app.norm import normalize" in final_cli
    assert "from app.exp import export_data" in final_cli
    assert "def cmd_norm() -> str:" in final_cli
    assert "def cmd_exp() -> str:" in final_cli
    assert final_cli.index("def cmd_norm") < final_cli.index('if __name__ == "__main__":')
    assert final_cli.index("def cmd_exp") < final_cli.index('if __name__ == "__main__":')
    assert _git(repo, "status", "--porcelain") == ""


def test_worktree_merge_wave_falls_back_to_partial_merge_on_incompatible_edit(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo_fail_closed"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "_bmad-output" / "implementation-artifacts").mkdir(parents=True)
    (repo / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml").write_text(
        "development_status:\n"
        "  1-1-a: ready-for-dev\n"
        "  1-2-b: ready-for-dev\n",
        encoding="utf-8",
    )
    src_app = repo / "src" / "app"
    src_app.mkdir(parents=True)
    (src_app / "__init__.py").write_text("", encoding="utf-8")
    (src_app / "cli.py").write_text(
        "def main(cmd: str) -> int:\n"
        '    if cmd == "status":\n'
        "        return 0\n"
        "    return 1\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: base repo")

    plan = WavePlan(
        sdd_kit="bmad",
        additive_registries=["src/app/cli.py"],
        tasks=[
            TaskNode(
                id="1-1-a",
                title="Modify main A",
                write_files=["src/app/cli.py"],
                effort=EffortTier.LOW,
            ),
            TaskNode(
                id="1-2-b",
                title="Modify main B",
                write_files=["src/app/cli.py"],
                effort=EffortTier.LOW,
            ),
        ],
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["1-1-a"],
                        effort=EffortTier.LOW,
                        additive_shared_files=["src/app/cli.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["1-2-b"],
                        effort=EffortTier.LOW,
                        additive_shared_files=["src/app/cli.py"],
                    ),
                ],
            )
        ],
        edges=[],
    )

    manager = WorktreeManager(repo)
    manager.prepare_wave(plan, 1)

    wt_a = manager.worktree_path_for_track(1, "track-a")
    wt_b = manager.worktree_path_for_track(1, "track-b")

    (wt_a / "src" / "app" / "cli.py").write_text(
        "def main(cmd: str) -> int:\n"
        '    if cmd == "status":\n'
        "        return 0\n"
        '    elif cmd == "a":\n'
        "        return 2\n"
        "    return 1\n",
        encoding="utf-8",
    )
    _git(wt_a, "add", "src/app/cli.py")
    _git(wt_a, "commit", "-m", "feat: add a branch")

    (wt_b / "src" / "app" / "cli.py").write_text(
        "def main(cmd: str) -> int:\n"
        '    if cmd == "status":\n'
        "        return 0\n"
        '    elif cmd == "b":\n'
        "        return 3\n"
        "    return 1\n",
        encoding="utf-8",
    )
    _git(wt_b, "add", "src/app/cli.py")
    _git(wt_b, "commit", "-m", "feat: add b branch")

    merged = manager.merge_wave(
        plan,
        1,
        run_quality_gates=False,
        partial_merge=True,
    )
    assert merged == ["parallel/track-a"]
    assert manager.last_merge_outcome is not None
    assert "track-b" in manager.last_merge_outcome.rejected_tracks
    assert manager.last_merge_outcome.rejected_tracks["track-b"].failed_stage == "git_merge"
    assert _git(repo, "status", "--porcelain") == ""


def test_empty_additive_set_blocks_standard_server_py_registry_merge(
    tmp_path: Path,
) -> None:
    """Task 54b: Empty effective_additive must NOT fall back to server.py basename matching."""
    repo = tmp_path / "repo_std_server"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "_bmad-output" / "implementation-artifacts").mkdir(parents=True)
    (repo / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml").write_text(
        "development_status:\n"
        "  1-1-a: ready-for-dev\n"
        "  1-2-b: ready-for-dev\n",
        encoding="utf-8",
    )
    svc_dir = repo / "src" / "service"
    svc_dir.mkdir(parents=True)
    (svc_dir / "__init__.py").write_text("", encoding="utf-8")
    (svc_dir / "server.py").write_text(
        "def start() -> str:\n    return 'base'\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: base repo")

    plan = WavePlan(
        sdd_kit="bmad",
        additive_registries=[],
        tasks=[
            TaskNode(
                id="1-1-a",
                title="Edit server A",
                write_files=["src/service/server.py"],
                effort=EffortTier.LOW,
            ),
            TaskNode(
                id="1-2-b",
                title="Edit server B",
                write_files=["src/service/server.py"],
                effort=EffortTier.LOW,
            ),
        ],
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["1-1-a"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/service/server.py"],
                        additive_shared_files=[],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["1-2-b"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/service/server.py"],
                        additive_shared_files=[],
                    ),
                ],
            )
        ],
        edges=[],
    )

    manager = WorktreeManager(repo)
    manager.prepare_wave(plan, 1)

    wt_a = manager.worktree_path_for_track(1, "track-a")
    wt_b = manager.worktree_path_for_track(1, "track-b")

    (wt_a / "src" / "service" / "server.py").write_text(
        "def start() -> str:\n    return 'base'\n\n\ndef extra_a() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    _git(wt_a, "add", "src/service/server.py")
    _git(wt_a, "commit", "-m", "feat: add extra_a")

    (wt_b / "src" / "service" / "server.py").write_text(
        "def start() -> str:\n    return 'base'\n\n\ndef extra_b() -> int:\n    return 2\n",
        encoding="utf-8",
    )
    _git(wt_b, "add", "src/service/server.py")
    _git(wt_b, "commit", "-m", "feat: add extra_b")

    merged = manager.merge_wave(
        plan,
        1,
        run_quality_gates=False,
        partial_merge=True,
    )
    assert merged == ["parallel/track-a"]
    assert manager.last_merge_outcome is not None
    assert "track-b" in manager.last_merge_outcome.rejected_tracks
    assert manager.last_merge_outcome.rejected_tracks["track-b"].failed_stage == "git_merge"
    assert _git(repo, "status", "--porcelain") == ""


def test_registry_merger_expr_deletion_commutativity() -> None:
    """Task 54b (5a): Deleting/replacing expr in ours vs adding expr in theirs is symmetric."""
    base = 'register("init")\n'
    branch_replaced = 'register("init_v2")\n'
    branch_added = 'register("init")\nregister("extra")\n'

    assert merge_python_additive_registry(base, branch_added, branch_replaced) is None
    assert merge_python_additive_registry(base, branch_replaced, branch_added) is None


def test_registry_merger_forbids_deleted_used_import_and_live_symbol_rebind() -> None:
    """Task 54b (5b): Fail closed on deleted used import or cross-kind collision with base."""
    # Case 1: ours deletes import used by theirs new function
    base_imp = "from pkg import helper\n\ndef existing() -> int:\n    return 1\n"
    ours_del_imp = "def existing() -> int:\n    return 1\n"
    theirs_use_imp = (
        "from pkg import helper\n\n"
        "def existing() -> int:\n    return 1\n\n\n"
        "def cmd_new() -> int:\n    return helper()\n"
    )
    assert merge_python_additive_registry(base_imp, ours_del_imp, theirs_use_imp) is None
    assert merge_python_additive_registry(base_imp, theirs_use_imp, ours_del_imp) is None

    # Case 2: var over live base def
    base_def = "def handler() -> int:\n    return 1\n"
    ours_edit_def = "def handler() -> int:\n    return 2\n"
    theirs_var = "def handler() -> int:\n    return 1\n\nhandler = 42\n"
    assert merge_python_additive_registry(base_def, ours_edit_def, theirs_var) is None

    # Case 3: new import over live base def, and new def over live base import
    theirs_imp = "from other import handler\n\ndef handler() -> int:\n    return 1\n"
    assert merge_python_additive_registry(base_def, ours_edit_def, theirs_imp) is None
    theirs_redef_imp = (
        "from pkg import helper\n\n"
        "def existing() -> int:\n    return 1\n\n\n"
        "def helper() -> int:\n    return 99\n"
    )
    assert (
        merge_python_additive_registry(base_imp, base_imp + "\nX = 1\n", theirs_redef_imp)
        is None
    )


def test_registry_merger_dunder_all_type_mismatch_when_base_absent() -> None:
    """Task 54b (5c): Fail closed when both introduce __all__ with mismatched list vs tuple."""
    base = "A = 1\nB = 2\n"
    ours = 'A = 1\nB = 2\n\n__all__ = ["A"]\n'
    theirs = 'A = 1\nB = 2\n\n__all__ = ("B",)\n'
    assert merge_python_additive_registry(base, ours, theirs) is None


def test_registry_merger_registration_key_collisions() -> None:
    """Task 54b (5d): Fail closed when new expr calls collide on literal registry keys."""
    base = "COMMANDS = {}\n"
    ours_upd = 'COMMANDS = {}\nCOMMANDS.update(run="a")\n'
    theirs_upd = 'COMMANDS = {}\nCOMMANDS.update(run="b")\n'
    assert merge_python_additive_registry(base, ours_upd, theirs_upd) is None

    theirs_dict_upd = 'COMMANDS = {}\nCOMMANDS.update({"run": "b"})\n'
    assert merge_python_additive_registry(base, ours_upd, theirs_dict_upd) is None

    ours_reg = 'COMMANDS = {}\nCOMMANDS.register("run", fn_a)\n'
    theirs_reg = 'COMMANDS = {}\nCOMMANDS.register("run", fn_b)\n'
    assert merge_python_additive_registry(base, ours_reg, theirs_reg) is None

    ours_sd = 'COMMANDS = {}\nCOMMANDS.setdefault("run", fn_a)\n'
    theirs_sd = 'COMMANDS = {}\nCOMMANDS.setdefault("run", fn_b)\n'
    assert merge_python_additive_registry(base, ours_sd, theirs_sd) is None

    ours_cmd = 'COMMANDS = {}\ncli.add_command(cmd_a, name="run")\n'
    theirs_cmd = 'COMMANDS = {}\ncli.add_command(cmd_b, name="run")\n'
    assert merge_python_additive_registry(base, ours_cmd, theirs_cmd) is None

    # Disjoint keys merge cleanly
    theirs_other = 'COMMANDS = {}\nCOMMANDS.update(status="b")\n'
    merged = merge_python_additive_registry(base, ours_upd, theirs_other)
    assert merged is not None
    assert 'COMMANDS.update(run="a")' in merged
    assert 'COMMANDS.update(status="b")' in merged


def test_registry_merger_top_level_definition_before_use_and_post_guard() -> None:
    """Task 54b (5e): Relocate theirs defs before dependent vars; reject post-guard code."""
    # Case 1: theirs adds def status above COMMANDS and updates COMMANDS -> must define before use
    base = "def ping() -> str:\n    return 'ping'\n\n\nCOMMANDS = [ping]\n"
    ours = (
        "def ping() -> str:\n    return 'ping'\n\n\n"
        "def pong() -> str:\n    return 'pong'\n\n\n"
        "COMMANDS = [ping]\n"
    )
    theirs = (
        "def ping() -> str:\n    return 'ping'\n\n\n"
        "def status() -> str:\n    return 'ok'\n\n\n"
        "COMMANDS = [ping, status]\n"
    )
    merged = merge_python_additive_registry(base, ours, theirs)
    assert merged is not None
    ast.parse(merged)
    assert merged.index("def status()") < merged.index("COMMANDS = [ping, status]")

    # Case 2: post-guard statements must fail closed
    base_pg = 'if __name__ == "__main__":\n    run()\n\nTRAILING = 1\n'
    ours_pg = 'if __name__ == "__main__":\n    run()\n\nTRAILING = 1\nEXTRA = 2\n'
    theirs_pg = 'if __name__ == "__main__":\n    run()\n\nTRAILING = 1\nOTHER = 3\n'
    assert merge_python_additive_registry(base_pg, ours_pg, theirs_pg) is None

    # Case 3: unresolvable forward reference (cycle between ours and theirs vars) -> fail closed
    base_fwd = "A = 1\nB = 1\n"
    ours_fwd = "A = B_NEW\nB = 1\n"
    theirs_fwd = "A = 1\nB = 1\nB_NEW = A\n"
    assert merge_python_additive_registry(base_fwd, ours_fwd, theirs_fwd) is None
