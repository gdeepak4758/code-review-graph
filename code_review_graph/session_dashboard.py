"""Generate a local browser dashboard for session token metrics."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .incremental import find_project_root, get_data_dir
from .session_metrics import load_session_metrics

_PROVIDER_ORDER = {"claude": 0, "gemini": 1, "openai": 2, "unknown": 3}


def _sorted_provider_rows(provider_totals: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "provider": provider,
            **totals,
        }
        for provider, totals in sorted(
            provider_totals.items(),
            key=lambda item: (_PROVIDER_ORDER.get(item[0], 99), item[0]),
        )
    ]


def build_session_dashboard_data(
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build dashboard data from stored metrics only."""
    raw = load_session_metrics(repo_root)

    sessions_out: list[dict[str, Any]] = []
    for session in reversed(raw.get("sessions", [])):
        calls = session.get("calls", [])
        tool_counts = Counter(
            str(call.get("tool", "unknown") or "unknown")
            for call in calls
            if isinstance(call, dict)
        )
        sessions_out.append({
            "session_id": session.get("session_id", ""),
            "repo_root": session.get("repo_root", ""),
            "started_at": session.get("started_at", ""),
            "updated_at": session.get("updated_at", ""),
            "tool_call_count": int(session.get("tool_call_count", len(calls)) or 0),
            "actual_tokens": int(session.get("actual_tokens", 0) or 0),
            "estimated_baseline_tokens": int(
                session.get("estimated_baseline_tokens", 0) or 0
            ),
            "estimated_saved_tokens": int(
                session.get("estimated_saved_tokens", 0) or 0
            ),
            "top_tools": [
                {"tool": tool, "count": count}
                for tool, count in tool_counts.most_common(6)
            ],
            "calls": calls,
        })

    daily_rollups = raw.get("daily_rollups", {})
    daily_totals = []
    for day, rollup in sorted(daily_rollups.items(), key=lambda item: item[0]):
        daily_totals.append({
            "date": rollup.get("date", day),
            "tool_call_count": int(rollup.get("tool_call_count", 0) or 0),
            "actual_tokens": int(rollup.get("actual_tokens", 0) or 0),
            "estimated_baseline_tokens": int(
                rollup.get("estimated_baseline_tokens", 0) or 0
            ),
            "estimated_saved_tokens": int(
                rollup.get("estimated_saved_tokens", 0) or 0
            ),
            "providers": _sorted_provider_rows(rollup.get("providers", {})),
        })

    return {
        "sessions": sessions_out,
        "provider_totals": _sorted_provider_rows(raw.get("provider_totals", {})),
        "daily_totals": daily_totals,
        "totals": {
            "session_count": len(sessions_out),
            "tool_call_count": int(raw.get("totals", {}).get("tool_call_count", 0) or 0),
            "actual_tokens": int(raw.get("totals", {}).get("actual_tokens", 0) or 0),
            "estimated_baseline_tokens": int(
                raw.get("totals", {}).get("estimated_baseline_tokens", 0) or 0
            ),
            "estimated_saved_tokens": int(
                raw.get("totals", {}).get("estimated_saved_tokens", 0) or 0
            ),
        },
        "note": (
            "Saved tokens are recorded when each MCP tool runs. "
            "Daily and provider rollups stay accurate even after old "
            "recent-session detail is trimmed."
        ),
    }


def generate_session_dashboard(
    repo_root: str | Path | None = None,
    output_path: str | Path | None = None,
) -> Path:
    """Generate a self-contained HTML dashboard for local session metrics."""
    data = build_session_dashboard_data(repo_root)

    if output_path is None:
        root = Path(repo_root).resolve() if repo_root else find_project_root().resolve()
        output_path = get_data_dir(root) / "session-dashboard.html"
    output_path = Path(output_path)

    html = _HTML_TEMPLATE.replace(
        "__SESSION_DASHBOARD_DATA__",
        json.dumps(data, ensure_ascii=False).replace("</", "<\\/"),
    )
    output_path.write_text(html, encoding="utf-8")
    return output_path.resolve()


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Code Review Graph Token Dashboard</title>
  <style>
    :root {
      --bg: #f4ecdf;
      --panel: rgba(255,255,255,0.82);
      --panel-strong: rgba(255,255,255,0.94);
      --ink: #1f2937;
      --muted: #6b7280;
      --line: rgba(31,41,55,0.10);
      --teal: #0f766e;
      --amber: #b45309;
      --rose: #be123c;
      --slate: #475569;
      --shadow: 0 18px 48px rgba(84, 62, 32, 0.13);
      --radius: 24px;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      color: var(--ink);
      font-family: "Segoe UI", "Aptos", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(15,118,110,0.16), transparent 26%),
        radial-gradient(circle at top right, rgba(180,83,9,0.15), transparent 24%),
        linear-gradient(180deg, #fbf6ed 0%, #f0e5d3 100%);
      min-height: 100vh;
    }

    .shell {
      max-width: 1220px;
      margin: 0 auto;
      padding: 30px 18px 44px;
    }

    .hero,
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 30px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
    }

    .hero {
      padding: 28px;
      margin-bottom: 20px;
    }

    .eyebrow {
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 10px;
    }

    h1 {
      margin: 0 0 8px;
      font-size: clamp(28px, 4vw, 42px);
      line-height: 1.05;
    }

    .sub {
      margin: 0;
      max-width: 920px;
      color: var(--muted);
      line-height: 1.5;
    }

    .stats,
    .provider-grid,
    .daily-grid {
      display: grid;
      gap: 14px;
    }

    .stats {
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      margin-top: 22px;
    }

    .provider-grid {
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      margin-top: 18px;
    }

    .daily-grid {
      margin-top: 18px;
    }

    .stat,
    .provider-card {
      background: var(--panel-strong);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 18px;
    }

    .label {
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }

    .value {
      font-size: 30px;
      font-weight: 700;
    }

    .saved .value { color: var(--teal); }
    .baseline .value { color: var(--amber); }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 20px;
    }

    .panel {
      padding: 22px;
    }

    h2 {
      margin: 0 0 6px;
      font-size: 24px;
    }

    .panel p {
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }

    .provider-card {
      position: relative;
      overflow: hidden;
    }

    .provider-card::after {
      content: "";
      position: absolute;
      inset: auto -30px -30px auto;
      width: 110px;
      height: 110px;
      border-radius: 50%;
      background: rgba(15,118,110,0.08);
    }

    .provider-card[data-provider="claude"]::after { background: rgba(15,118,110,0.12); }
    .provider-card[data-provider="gemini"]::after { background: rgba(180,83,9,0.13); }
    .provider-card[data-provider="openai"]::after { background: rgba(190,18,60,0.10); }
    .provider-card[data-provider="unknown"]::after { background: rgba(71,85,105,0.10); }

    .provider-card h3 {
      margin: 0 0 8px;
      font-size: 22px;
      text-transform: capitalize;
    }

    .provider-meta {
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
      margin-top: 8px;
    }

    .daily-row {
      display: grid;
      grid-template-columns: 120px minmax(0, 1fr) 96px 96px;
      gap: 12px;
      align-items: center;
      padding: 14px 16px;
      border-radius: 20px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.7);
    }

    .daily-bars {
      display: flex;
      height: 14px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(71,85,105,0.12);
    }

    .bar-claude { background: #0f766e; }
    .bar-gemini { background: #b45309; }
    .bar-openai { background: #be123c; }
    .bar-unknown { background: #64748b; }

    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 16px;
      border-radius: 18px;
      overflow: hidden;
      background: rgba(255,255,255,0.74);
    }

    th, td {
      text-align: left;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      font-size: 14px;
    }

    th {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      background: rgba(255,255,255,0.95);
    }

    tr:last-child td { border-bottom: 0; }

    .small {
      color: var(--muted);
      font-size: 13px;
    }

    .empty {
      margin-top: 16px;
      padding: 16px;
      border-radius: 18px;
      background: rgba(255,255,255,0.72);
      color: var(--muted);
    }

    @media (max-width: 860px) {
      .daily-row {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">Local Single-File Analytics</div>
      <h1>Code Review Graph Token Dashboard</h1>
      <p class="sub" id="note"></p>
      <div class="stats" id="stats"></div>
    </section>

    <section class="layout">
      <section class="panel">
        <h2>Provider Totals</h2>
        <p>Totals come from the single local metrics file. Unknown means the MCP launcher did not identify the provider.</p>
        <div class="provider-grid" id="providers"></div>
      </section>

      <section class="panel">
        <h2>Daily Saved Tokens</h2>
        <p>Each day shows stored saved-token totals split by provider, plus the total actual payload sent through the graph tools.</p>
        <div class="daily-grid" id="daily"></div>
      </section>

      <section class="panel">
        <h2>Recent Sessions</h2>
        <p>Recent session detail is kept for traceability. Long-term totals come from the durable daily rollups above.</p>
        <table>
          <thead>
            <tr>
              <th>Session</th>
              <th>Saved</th>
              <th>Actual</th>
              <th>Calls</th>
              <th>Window</th>
            </tr>
          </thead>
          <tbody id="sessions"></tbody>
        </table>
      </section>
    </section>
  </div>

  <script>
    const data = __SESSION_DASHBOARD_DATA__;
    const nf = new Intl.NumberFormat();

    function renderStats() {
      const totals = data.totals || {};
      document.getElementById("note").textContent = data.note || "";
      const cards = [
        ["Estimated Saved", totals.estimated_saved_tokens || 0, "saved"],
        ["Actual Payload", totals.actual_tokens || 0, ""],
        ["Baseline Estimate", totals.estimated_baseline_tokens || 0, "baseline"],
        ["Tool Calls", totals.tool_call_count || 0, ""],
      ];
      document.getElementById("stats").innerHTML = cards.map(([label, value, cls]) => `
        <article class="stat ${cls}">
          <div class="label">${label}</div>
          <div class="value">${nf.format(value)}</div>
        </article>
      `).join("");
    }

    function renderProviders() {
      const rows = data.provider_totals || [];
      const el = document.getElementById("providers");
      if (!rows.length) {
        el.innerHTML = '<div class="empty">No provider data yet. Use the MCP tools, then refresh the dashboard.</div>';
        return;
      }
      el.innerHTML = rows.map((row) => `
        <article class="provider-card" data-provider="${row.provider}">
          <div class="label">Provider</div>
          <h3>${row.provider}</h3>
          <div class="value">${nf.format(row.estimated_saved_tokens || 0)}</div>
          <div class="provider-meta">
            Saved tokens<br>
            Actual: ${nf.format(row.actual_tokens || 0)}<br>
            Calls: ${nf.format(row.tool_call_count || 0)}
          </div>
        </article>
      `).join("");
    }

    function renderDaily() {
      const rows = data.daily_totals || [];
      const el = document.getElementById("daily");
      if (!rows.length) {
        el.innerHTML = '<div class="empty">No daily rollups yet. The first MCP tool call will populate this chart.</div>';
        return;
      }
      const maxSaved = Math.max(...rows.map((row) => row.estimated_saved_tokens || 0), 1);
      el.innerHTML = rows.map((row) => {
        const segments = (row.providers || []).map((provider) => {
          const pct = ((provider.estimated_saved_tokens || 0) / maxSaved) * 100;
          return `<span class="bar-${provider.provider}" style="width:${pct}%"></span>`;
        }).join("");
        return `
          <div class="daily-row">
            <div>
              <strong>${row.date}</strong>
            </div>
            <div class="daily-bars">${segments}</div>
            <div><strong>${nf.format(row.estimated_saved_tokens || 0)}</strong><div class="small">saved</div></div>
            <div><strong>${nf.format(row.actual_tokens || 0)}</strong><div class="small">actual</div></div>
          </div>
        `;
      }).join("");
    }

    function renderSessions() {
      const rows = data.sessions || [];
      const el = document.getElementById("sessions");
      if (!rows.length) {
        el.innerHTML = '<tr><td colspan="5">No recent sessions yet.</td></tr>';
        return;
      }
      el.innerHTML = rows.map((row, index) => `
        <tr>
          <td><strong>Session ${rows.length - index}</strong><div class="small">${row.repo_root || ""}</div></td>
          <td>${nf.format(row.estimated_saved_tokens || 0)}</td>
          <td>${nf.format(row.actual_tokens || 0)}</td>
          <td>${nf.format(row.tool_call_count || 0)}</td>
          <td class="small">${row.started_at ? new Date(row.started_at).toLocaleString() : ""} to ${row.updated_at ? new Date(row.updated_at).toLocaleString() : ""}</td>
        </tr>
      `).join("");
    }

    renderStats();
    renderProviders();
    renderDaily();
    renderSessions();
  </script>
</body>
</html>
"""
