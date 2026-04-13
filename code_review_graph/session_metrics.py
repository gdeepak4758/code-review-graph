"""Local-only session metrics for MCP tool usage.

Tracks tool calls in a single local JSON file:
``.code-review-graph/session_metrics.json``.

The file stores two layers of data:
- durable rollups for daily/provider/overall totals
- recent session detail for lightweight inspection
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from .incremental import find_project_root, get_data_dir

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2
_MAX_RECENT_SESSIONS = 25

_KNOWN_PROVIDERS = {"claude", "gemini", "openai", "unknown"}

_current_session_id = f"{os.getpid()}-{uuid4().hex[:10]}"
_current_session_started_at = datetime.now(timezone.utc).isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_provider() -> str:
    """Detect the upstream AI provider from the environment."""
    raw = (
        os.environ.get("CRG_CLIENT_PROVIDER", "")
        or os.environ.get("CRG_PROVIDER", "")
    ).strip().lower()
    platform = os.environ.get("CRG_CLIENT_PLATFORM", "").strip().lower()

    if not raw and platform:
        raw = {
            "claude": "claude",
            "claude-code": "claude",
            "gemini": "gemini",
            "antigravity": "gemini",
            "codex": "openai",
            "openai": "openai",
        }.get(platform, "unknown")

    if raw in _KNOWN_PROVIDERS:
        return raw
    return "unknown"


def estimate_tokens(payload: Any) -> int:
    """Estimate tokens from a JSON-serializable payload."""
    return len(json.dumps(payload, default=str, ensure_ascii=False)) // 4


def _strip_hints(payload: Any) -> Any:
    """Drop volatile hint metadata from token accounting."""
    if isinstance(payload, dict):
        return {
            key: _strip_hints(value)
            for key, value in payload.items()
            if key != "_hints"
        }
    if isinstance(payload, list):
        return [_strip_hints(item) for item in payload]
    return payload


def _jsonable(value: Any) -> Any:
    """Best-effort JSON-safe conversion for CLI / MCP args."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def _resolve_repo_root(repo_root: str | Path | None) -> Path:
    if repo_root:
        return Path(repo_root).resolve()
    return find_project_root().resolve()


def _metrics_path(repo_root: str | Path | None) -> Path:
    return get_data_dir(_resolve_repo_root(repo_root)) / "session_metrics.json"


def _empty_counter() -> dict[str, int]:
    return {
        "tool_call_count": 0,
        "actual_tokens": 0,
        "estimated_baseline_tokens": 0,
        "estimated_saved_tokens": 0,
    }


def _empty_daily_rollup(day: str) -> dict[str, Any]:
    return {
        "date": day,
        **_empty_counter(),
        "providers": {},
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "sessions": [],
        "totals": _empty_counter(),
        "provider_totals": {},
        "daily_rollups": {},
    }


def _normalize_counter(data: Any) -> dict[str, int]:
    base = _empty_counter()
    if not isinstance(data, dict):
        return base
    for key in base:
        try:
            base[key] = int(data.get(key, 0) or 0)
        except (TypeError, ValueError):
            base[key] = 0
    return base


def _increment_counter(
    counter: dict[str, int],
    actual_tokens: int,
    baseline_tokens: int,
    saved_tokens: int,
) -> None:
    counter["tool_call_count"] += 1
    counter["actual_tokens"] += actual_tokens
    counter["estimated_baseline_tokens"] += baseline_tokens
    counter["estimated_saved_tokens"] += saved_tokens


@lru_cache(maxsize=1)
def _get_baseline_replayers() -> dict[str, Any]:
    from .tools import (
        detect_changes_func,
        get_architecture_overview_func,
        get_community_func,
        get_flow,
        get_impact_radius,
        get_review_context,
        list_communities_func,
        list_flows,
        query_graph,
        semantic_search_nodes,
    )

    return {
        "detect_changes": detect_changes_func,
        "get_architecture_overview": get_architecture_overview_func,
        "get_community": get_community_func,
        "get_flow": get_flow,
        "get_impact_radius": get_impact_radius,
        "get_review_context": get_review_context,
        "list_communities": list_communities_func,
        "list_flows": list_flows,
        "query_graph": query_graph,
        "semantic_search_nodes": semantic_search_nodes,
    }


def _estimate_baseline_tokens(
    tool_name: str,
    args: dict[str, Any],
    repo_root: Path,
    actual_tokens: int,
    status: str = "ok",
) -> int:
    """Estimate standard-mode tokens for a minimal call at record time."""
    if status != "ok":
        return actual_tokens

    detail_level = str(args.get("detail_level", "standard")).lower()
    if detail_level != "minimal":
        return actual_tokens

    replay = _get_baseline_replayers().get(tool_name)
    if replay is None:
        return actual_tokens

    replay_args = dict(args)
    replay_args["detail_level"] = "standard"
    if not replay_args.get("repo_root"):
        replay_args["repo_root"] = str(repo_root)

    try:
        replay_result = replay(**replay_args)
    except Exception as exc:
        logger.debug("Baseline replay failed for %s: %s", tool_name, exc)
        return actual_tokens

    if not isinstance(replay_result, dict):
        return actual_tokens

    return estimate_tokens(_strip_hints(replay_result))


def _normalize_session(
    session: Any,
    fallback_repo_root: Path,
    recompute_call_metrics: bool,
) -> dict[str, Any]:
    """Normalize one session entry and optionally backfill stored metrics."""
    if not isinstance(session, dict):
        session = {}

    session_repo_root = Path(
        session.get("repo_root") or str(fallback_repo_root)
    ).resolve()
    calls_raw = session.get("calls", [])
    if not isinstance(calls_raw, list):
        calls_raw = []

    calls_out: list[dict[str, Any]] = []
    summary = _empty_counter()

    for raw_call in calls_raw:
        if not isinstance(raw_call, dict):
            continue

        call = dict(raw_call)
        timestamp = str(call.get("timestamp", "") or _utc_now())
        day = str(call.get("day", "") or timestamp[:10] or "unknown")
        tool_name = str(call.get("tool", "unknown") or "unknown")
        provider = str(call.get("provider", "") or "unknown").lower()
        if provider not in _KNOWN_PROVIDERS:
            provider = "unknown"

        try:
            actual_tokens = int(call.get("actual_tokens", 0) or 0)
        except (TypeError, ValueError):
            actual_tokens = 0

        needs_backfill = recompute_call_metrics or (
            "estimated_baseline_tokens" not in call
            or "estimated_saved_tokens" not in call
        )

        if needs_backfill:
            args = call.get("args", {})
            if not isinstance(args, dict):
                args = {}
            baseline_tokens = _estimate_baseline_tokens(
                tool_name=tool_name,
                args=args,
                repo_root=session_repo_root,
                actual_tokens=actual_tokens,
                status=str(call.get("status", "ok")),
            )
            saved_tokens = max(baseline_tokens - actual_tokens, 0)
        else:
            try:
                baseline_tokens = int(
                    call.get("estimated_baseline_tokens", actual_tokens)
                    or actual_tokens
                )
            except (TypeError, ValueError):
                baseline_tokens = actual_tokens
            try:
                saved_tokens = int(
                    call.get(
                        "estimated_saved_tokens",
                        max(baseline_tokens - actual_tokens, 0),
                    ) or 0
                )
            except (TypeError, ValueError):
                saved_tokens = max(baseline_tokens - actual_tokens, 0)

        call["timestamp"] = timestamp
        call["day"] = day
        call["tool"] = tool_name
        call["provider"] = provider
        call["args"] = _jsonable(call.get("args", {}))
        call["status"] = str(call.get("status", "ok"))
        call["summary"] = str(call.get("summary", "") or "")
        call["actual_tokens"] = actual_tokens
        call["estimated_baseline_tokens"] = baseline_tokens
        call["estimated_saved_tokens"] = saved_tokens

        calls_out.append(call)
        _increment_counter(summary, actual_tokens, baseline_tokens, saved_tokens)

    return {
        "session_id": str(session.get("session_id", "")),
        "repo_root": str(session_repo_root),
        "started_at": str(
            session.get("started_at", "") or _current_session_started_at
        ),
        "updated_at": str(
            session.get("updated_at", "")
            or session.get("started_at", "")
            or _current_session_started_at
        ),
        **summary,
        "calls": calls_out,
    }


def _rebuild_rollups_from_sessions(
    sessions: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, dict[str, int]], dict[str, dict[str, Any]]]:
    totals = _empty_counter()
    provider_totals: dict[str, dict[str, int]] = {}
    daily_rollups: dict[str, dict[str, Any]] = {}

    for session in sessions:
        for call in session.get("calls", []):
            actual = int(call.get("actual_tokens", 0) or 0)
            baseline = int(
                call.get("estimated_baseline_tokens", actual) or actual
            )
            saved = int(
                call.get(
                    "estimated_saved_tokens",
                    max(baseline - actual, 0),
                ) or 0
            )
            provider = str(call.get("provider", "unknown") or "unknown")
            day = str(call.get("day", "") or "unknown")

            _increment_counter(totals, actual, baseline, saved)

            provider_entry = provider_totals.setdefault(
                provider, _empty_counter()
            )
            _increment_counter(provider_entry, actual, baseline, saved)

            daily_entry = daily_rollups.setdefault(
                day, _empty_daily_rollup(day)
            )
            _increment_counter(daily_entry, actual, baseline, saved)
            provider_daily = daily_entry["providers"].setdefault(
                provider, _empty_counter()
            )
            _increment_counter(provider_daily, actual, baseline, saved)

    return totals, provider_totals, daily_rollups


def load_session_metrics(repo_root: str | Path | None = None) -> dict[str, Any]:
    """Load and normalize the metrics file for a repository."""
    resolved_root = _resolve_repo_root(repo_root)
    path = _metrics_path(resolved_root)
    if not path.exists():
        return _empty_metrics()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Failed to load session metrics from %s: %s", path, exc)
        return _empty_metrics()

    if not isinstance(raw, dict):
        return _empty_metrics()

    sessions_raw = raw.get("sessions", [])
    if not isinstance(sessions_raw, list):
        sessions_raw = []

    needs_rollup_rebuild = (
        raw.get("schema_version") != _SCHEMA_VERSION
        or not isinstance(raw.get("totals"), dict)
        or not isinstance(raw.get("provider_totals"), dict)
        or not isinstance(raw.get("daily_rollups"), dict)
    )

    sessions = [
        _normalize_session(
            session,
            fallback_repo_root=resolved_root,
            recompute_call_metrics=needs_rollup_rebuild,
        )
        for session in sessions_raw
    ]

    if len(sessions) > _MAX_RECENT_SESSIONS:
        sessions = sessions[-_MAX_RECENT_SESSIONS:]

    if needs_rollup_rebuild:
        totals, provider_totals, daily_rollups = _rebuild_rollups_from_sessions(
            sessions
        )
    else:
        totals = _normalize_counter(raw.get("totals"))
        provider_totals = {
            provider: _normalize_counter(counter)
            for provider, counter in raw.get("provider_totals", {}).items()
            if isinstance(counter, dict)
        }
        daily_rollups = {}
        for day, rollup in raw.get("daily_rollups", {}).items():
            if not isinstance(rollup, dict):
                continue
            entry = {
                "date": str(rollup.get("date", day) or day),
                **_normalize_counter(rollup),
                "providers": {},
            }
            providers = rollup.get("providers", {})
            if isinstance(providers, dict):
                entry["providers"] = {
                    provider: _normalize_counter(counter)
                    for provider, counter in providers.items()
                    if isinstance(counter, dict)
                }
            daily_rollups[str(day)] = entry

    return {
        "schema_version": _SCHEMA_VERSION,
        "sessions": sessions,
        "totals": totals,
        "provider_totals": provider_totals,
        "daily_rollups": daily_rollups,
    }


def _write_metrics(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def record_tool_call(
    tool_name: str,
    args: dict[str, Any],
    result: dict[str, Any],
    repo_root: str | Path | None = None,
) -> None:
    """Persist a tool call to the local session metrics file."""
    resolved_root = _resolve_repo_root(repo_root)
    path = _metrics_path(resolved_root)
    data = load_session_metrics(resolved_root)

    sessions = data["sessions"]
    session = next(
        (s for s in sessions if s.get("session_id") == _current_session_id),
        None,
    )
    if session is None:
        session = {
            "session_id": _current_session_id,
            "repo_root": str(resolved_root),
            "started_at": _current_session_started_at,
            "updated_at": _current_session_started_at,
            **_empty_counter(),
            "calls": [],
        }
        sessions.append(session)

    now = _utc_now()
    provider = _detect_provider()
    actual_tokens = estimate_tokens(_strip_hints(result))
    baseline_tokens = _estimate_baseline_tokens(
        tool_name=tool_name,
        args=args,
        repo_root=resolved_root,
        actual_tokens=actual_tokens,
        status=str(result.get("status", "ok")),
    )
    saved_tokens = max(baseline_tokens - actual_tokens, 0)

    call = {
        "timestamp": now,
        "day": now[:10],
        "tool": tool_name,
        "provider": provider,
        "args": _jsonable(args),
        "status": result.get("status", "ok"),
        "summary": result.get("summary", ""),
        "actual_tokens": actual_tokens,
        "estimated_baseline_tokens": baseline_tokens,
        "estimated_saved_tokens": saved_tokens,
    }
    session["calls"].append(call)
    session["updated_at"] = now
    _increment_counter(session, actual_tokens, baseline_tokens, saved_tokens)

    if len(sessions) > _MAX_RECENT_SESSIONS:
        del sessions[:-_MAX_RECENT_SESSIONS]

    _increment_counter(
        data["totals"], actual_tokens, baseline_tokens, saved_tokens
    )

    provider_entry = data["provider_totals"].setdefault(
        provider, _empty_counter()
    )
    _increment_counter(
        provider_entry, actual_tokens, baseline_tokens, saved_tokens
    )

    daily_entry = data["daily_rollups"].setdefault(
        call["day"], _empty_daily_rollup(call["day"])
    )
    _increment_counter(
        daily_entry, actual_tokens, baseline_tokens, saved_tokens
    )
    provider_daily = daily_entry["providers"].setdefault(
        provider, _empty_counter()
    )
    _increment_counter(
        provider_daily, actual_tokens, baseline_tokens, saved_tokens
    )

    _write_metrics(path, data)


def reset_session_metrics() -> None:
    """Reset the in-memory session identity for tests."""
    global _current_session_id, _current_session_started_at
    _current_session_id = f"{os.getpid()}-{uuid4().hex[:10]}"
    _current_session_started_at = _utc_now()
