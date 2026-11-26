"""Sidecar adapter for code-review-graph v2.3.9 with AST fallback."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sdd_parallel.ast_scanner import ASTHotspotScanner

logger = logging.getLogger(__name__)

_WORKTREE_PREFIX_RE = re.compile(
    r"^(?:.*/)?(?:\.worktrees/[^/]+|\.gemini/antigravity/worktrees/[^/]+/[^/]+)/(.+)$"
)


class CRGSidecarAdapter:
    """Computes semantic blast radius I(T) around modified files.

    Safeguards against the three known limitations of code-review-graph v2.3.9:
    1. Always initializes GraphStore with `main_repo_root` (avoids GraphRootMismatchError).
    2. Never passes `?mode=ro` because `get_impact_radius_sql` creates TEMP tables in WAL mode.
    3. Passes `resolution="direct"` and filters `impact_scores >= min_impact_score` (0.5).
    """

    def __init__(
        self,
        main_repo_root: Path,
        ast_scanner: ASTHotspotScanner | None = None,
    ) -> None:
        self.main_repo_root = main_repo_root.resolve()
        self.ast_scanner = ast_scanner or ASTHotspotScanner(self.main_repo_root)
        self._crg_db_path = self.main_repo_root / ".code-review-graph" / "graph.db"

    def normalize_to_repo_relative(self, file_path: str | Path) -> str:
        """Convert absolute or worktree-relative path to POSIX path relative to main_repo_root."""
        raw_posix = str(file_path).replace("\\", "/").strip()
        wt_match = _WORKTREE_PREFIX_RE.match(raw_posix)
        if wt_match:
            return wt_match.group(1).lstrip("./")

        p = Path(raw_posix)
        if not p.is_absolute():
            return raw_posix.lstrip("./")

        try:
            return p.resolve().relative_to(self.main_repo_root).as_posix()
        except ValueError:
            main_posix = self.main_repo_root.as_posix().lower().rstrip("/") + "/"
            if raw_posix.lower().startswith(main_posix):
                return raw_posix[len(main_posix) :].lstrip("./")
            return raw_posix

    def compute_impact_files(
        self,
        write_files: Iterable[str],
        min_impact_score: float = 0.5,
        max_depth: int = 2,
    ) -> set[str]:
        """Return set of files semantically impacted by changes to `write_files`."""
        rel_targets = [self.normalize_to_repo_relative(f) for f in write_files]
        impacted: set[str] = set()

        # 1. Try native code-review-graph SQLite engine if graph.db exists
        if self._crg_db_path.is_file():
            crg_hits = self._query_crg_impact_radius(
                rel_targets=rel_targets,
                min_impact_score=min_impact_score,
                max_depth=max_depth,
            )
            impacted.update(crg_hits)

        # 2. Always enrich with direct Python AST importers (covers newly created / unindexed files)
        for rel in rel_targets:
            impacted.update(self.ast_scanner.get_importers(rel))

        # Remove self-references
        impacted.difference_update(rel_targets)
        return impacted

    def _query_crg_impact_radius(
        self,
        rel_targets: list[str],
        min_impact_score: float,
        max_depth: int,
    ) -> set[str]:
        """Query code_review_graph.graph.GraphStore in standard WAL mode if available."""
        try:
            import importlib

            graph_mod: Any = importlib.import_module("code_review_graph.graph")
            store = graph_mod.GraphStore(self._crg_db_path)
            try:
                seed_candidates: list[str] = []
                for rel in rel_targets:
                    if rel not in seed_candidates:
                        seed_candidates.append(rel)
                    abs_posix = (self.main_repo_root / rel).resolve().as_posix()
                    if abs_posix not in seed_candidates:
                        seed_candidates.append(abs_posix)
                    abs_native = str((self.main_repo_root / rel).resolve())
                    if abs_native not in seed_candidates:
                        seed_candidates.append(abs_native)

                result: Any = store.get_impact_radius_sql(
                    changed_files=seed_candidates,
                    max_depth=max_depth,
                    resolution="direct",
                )
                files: set[str] = set()
                if isinstance(result, dict):
                    raw_scores = result.get("impact_scores")
                    scores: dict[str, float] = (
                        {str(k): float(v) for k, v in raw_scores.items()}
                        if isinstance(raw_scores, dict)
                        else {}
                    )
                    for node in result.get("impacted_nodes", []):
                        if isinstance(node, dict):
                            qn = node.get("qualified_name")
                            fp = node.get("file_path")
                            node_score = float(node.get("score", scores.get(str(qn), 1.0)))
                        else:
                            qn = getattr(node, "qualified_name", None)
                            fp = getattr(node, "file_path", None)
                            node_score = scores.get(str(qn), 1.0) if qn else 1.0
                        if isinstance(fp, str) and node_score >= min_impact_score:
                            files.add(self.normalize_to_repo_relative(fp))
                return files
            finally:
                store.close()
        except Exception as exc:
            logger.warning("CRGSidecarAdapter fallback to AST (reason: %s)", exc)
            return set()
