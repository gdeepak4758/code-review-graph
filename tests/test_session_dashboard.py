"""Tests for local session metrics and dashboard generation."""

from pathlib import Path

from code_review_graph.graph import GraphStore
from code_review_graph.parser import NodeInfo
from code_review_graph.session_dashboard import (
    build_session_dashboard_data,
    generate_session_dashboard,
)
from code_review_graph.session_metrics import (
    load_session_metrics,
    record_tool_call,
    reset_session_metrics,
)
from code_review_graph.tools.query import query_graph


def _seed_repo(repo: Path) -> None:
    (repo / ".code-review-graph").mkdir()
    src_dir = repo / "src"
    src_dir.mkdir()
    file_path = src_dir / "auth.py"
    file_path.write_text(
        "class AuthService:\n"
        "    def login(self, username, password):\n"
        "        return True\n",
        encoding="utf-8",
    )

    store = GraphStore(repo / ".code-review-graph" / "graph.db")
    store.upsert_node(NodeInfo(
        kind="File",
        name="auth.py",
        file_path=str(file_path),
        line_start=1,
        line_end=3,
        language="python",
        parent_name=None,
        params=None,
        return_type=None,
        modifiers=None,
        is_test=False,
        extra={},
    ))
    store.upsert_node(NodeInfo(
        kind="Function",
        name="login",
        file_path=str(file_path),
        line_start=2,
        line_end=3,
        language="python",
        parent_name="AuthService",
        params="username, password",
        return_type="bool",
        modifiers=None,
        is_test=False,
        extra={},
    ))
    store.commit()
    store.close()


def test_record_tool_call_creates_local_metrics(tmp_path):
    reset_session_metrics()
    repo = tmp_path / "repo"
    repo.mkdir()

    record_tool_call(
        "list_graph_stats",
        args={"repo_root": str(repo)},
        result={"status": "ok", "summary": "stats", "total_nodes": 3},
        repo_root=repo,
    )

    data = load_session_metrics(repo)
    assert len(data["sessions"]) == 1
    session = data["sessions"][0]
    assert session["repo_root"] == str(repo.resolve())
    assert len(session["calls"]) == 1
    assert session["calls"][0]["tool"] == "list_graph_stats"
    assert session["calls"][0]["provider"] == "unknown"
    assert len(session["calls"][0]["day"]) == 10
    assert session["calls"][0]["actual_tokens"] > 0
    assert data["totals"]["tool_call_count"] == 1
    assert data["daily_rollups"][session["calls"][0]["day"]]["tool_call_count"] == 1
    assert data["provider_totals"]["unknown"]["tool_call_count"] == 1


def test_dashboard_uses_saved_tokens_stored_at_write_time(tmp_path):
    reset_session_metrics()
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    result = query_graph(
        pattern="file_summary",
        target=str(repo / "src" / "auth.py"),
        repo_root=str(repo),
        detail_level="minimal",
    )
    record_tool_call(
        "query_graph",
        args={
            "pattern": "file_summary",
            "target": str(repo / "src" / "auth.py"),
            "repo_root": str(repo),
            "detail_level": "minimal",
        },
        result=result,
        repo_root=repo,
    )

    data = build_session_dashboard_data(repo)
    assert data["totals"]["tool_call_count"] == 1
    assert data["sessions"][0]["estimated_saved_tokens"] > 0
    assert data["sessions"][0]["calls"][0]["estimated_saved_tokens"] > 0
    assert data["provider_totals"][0]["provider"] == "unknown"
    assert data["daily_totals"][0]["estimated_saved_tokens"] > 0

    html_path = generate_session_dashboard(
        repo_root=repo,
        output_path=repo / ".code-review-graph" / "session-dashboard.html",
    )
    content = html_path.read_text(encoding="utf-8")
    assert "Code Review Graph Token Dashboard" in content
    assert "Provider Totals" in content
    assert "Daily Saved Tokens" in content


def test_metrics_keep_durable_totals_after_recent_session_cap(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    for _ in range(30):
        reset_session_metrics()
        record_tool_call(
            "list_graph_stats",
            args={"repo_root": str(repo)},
            result={"status": "ok", "summary": "stats", "total_nodes": 3},
            repo_root=repo,
        )

    data = load_session_metrics(repo)
    assert len(data["sessions"]) == 25
    assert data["totals"]["tool_call_count"] == 30
    assert data["provider_totals"]["unknown"]["tool_call_count"] == 30


def test_generate_dashboard_without_metrics_writes_empty_state(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    html_path = generate_session_dashboard(
        repo_root=repo,
        output_path=repo / ".code-review-graph" / "session-dashboard.html",
    )
    content = html_path.read_text(encoding="utf-8")
    assert "No provider data yet" in content
    assert "No recent sessions yet" in content
