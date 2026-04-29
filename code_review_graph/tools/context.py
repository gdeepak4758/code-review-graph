"""Tool: get_minimal_context — ultra-compact context for token-efficient workflows."""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from ._common import _get_store, compact_response

logger = logging.getLogger(__name__)


_GIT_TIMEOUT_SECONDS = 5


def _changed_files_from_status(output: str) -> list[str]:
    """Parse ``git status --porcelain`` paths, including renames."""
    files: list[str] = []
    for line in output.splitlines():
        if len(line) <= 3:
            continue
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        if entry:
            files.append(entry)
    return files


def _get_changed_files_fast(root: Path, base: str) -> list[str]:
    """Best-effort changed-file discovery for the minimal context tool."""
    files: list[str] = []
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base, "--"],
            capture_output=True, text=True,
            cwd=str(root), timeout=_GIT_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0 and result.stdout.strip():
            files.extend(f.strip() for f in result.stdout.splitlines() if f.strip())

        result2 = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True,
            cwd=str(root), timeout=_GIT_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        if result2.returncode == 0 and result2.stdout.strip():
            files.extend(_changed_files_from_status(result2.stdout))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    seen: set[str] = set()
    unique_files: list[str] = []
    for file in files:
        if file not in seen:
            seen.add(file)
            unique_files.append(file)
    return unique_files


def get_minimal_context(
    task: str = "",
    changed_files: list[str] | None = None,
    repo_root: str | None = None,
    base: str = "HEAD~1",
) -> dict[str, Any]:
    """Return minimum context an agent needs to start any task (~100 tokens).

    Combines graph stats, top communities, top flows, risk score,
    and suggested next tools into an ultra-compact response.

    Args:
        task: Natural language description of what the agent is doing
              (e.g. "review PR #42", "debug login timeout").
        changed_files: Explicit changed files. Auto-detected from git if None.
        repo_root: Repository root path. Auto-detected if None.
        base: Git ref for diff comparison.
    """
    store, root = _get_store(repo_root)
    try:
        # 1. Quick stats
        stats = store.get_stats()

        # 2. Cheap changed-file signal.
        #
        # This tool is the first MCP call agents make, so it must stay cheap.
        # Deep risk scoring can traverse callers, flows, and test gaps; leave
        # that to detect_changes_tool after the agent has decided it needs it.
        risk = "unknown"
        risk_score = 0.0
        top_affected: list[str] = []
        test_gap_count = 0
        files = changed_files if changed_files is not None else _get_changed_files_fast(root, base)
        if files:
            top_affected = files[:5]

        # 3. Top 3 communities
        communities: list[str] = []
        try:
            rows = store._conn.execute(
                "SELECT name FROM communities ORDER BY size DESC LIMIT 3"
            ).fetchall()
            communities = [r[0] for r in rows]
        except sqlite3.OperationalError:  # nosec B110 — table may not exist yet
            logger.debug("communities table not yet populated")

        # 4. Top 3 critical flows
        flows: list[str] = []
        try:
            rows = store._conn.execute(
                "SELECT name FROM flows ORDER BY criticality DESC LIMIT 3"
            ).fetchall()
            flows = [r[0] for r in rows]
        except sqlite3.OperationalError:  # nosec B110 — table may not exist yet
            logger.debug("flows table not yet populated")

        # 5. Suggest next tools based on task keywords
        task_lower = task.lower()
        if any(w in task_lower for w in ("review", "pr", "merge", "diff")):
            suggestions = ["detect_changes", "get_affected_flows", "get_review_context"]
        elif any(w in task_lower for w in ("debug", "bug", "error", "fix")):
            suggestions = ["semantic_search_nodes", "query_graph", "get_flow"]
        elif any(w in task_lower for w in ("refactor", "rename", "dead", "clean")):
            suggestions = ["refactor", "find_large_functions", "get_architecture_overview"]
        elif any(w in task_lower for w in ("onboard", "understand", "explore", "arch")):
            suggestions = [
                "get_architecture_overview", "list_communities", "list_flows",
            ]
        else:
            suggestions = [
                "detect_changes", "semantic_search_nodes",
                "get_architecture_overview",
            ]

        # Build summary
        summary_parts = [
            f"{stats.total_nodes} nodes, {stats.total_edges} edges"
            f" across {stats.files_count} files.",
        ]
        if risk != "unknown":
            summary_parts.append(f"Risk: {risk} ({risk_score:.2f}).")
        elif files:
            summary_parts.append(
                f"{len(files)} changed file(s); run detect_changes for risk."
            )
        if test_gap_count:
            summary_parts.append(f"{test_gap_count} test gaps.")

        return compact_response(
            summary=" ".join(summary_parts),
            key_entities=top_affected or None,
            risk=risk,
            communities=communities or None,
            flows_affected=flows or None,
            next_tool_suggestions=suggestions,
        )
    finally:
        store.close()
