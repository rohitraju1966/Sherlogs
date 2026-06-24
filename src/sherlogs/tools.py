"""Agent tools: summarize (recon) and get_lines (evidence).

- summarize: a compressed template view around the incident time, optionally for one service.
- get_lines: the raw log lines behind a specific template, to confirm a hypothesis.
"""

import duckdb
from langchain_core.tools import BaseTool, tool

from sherlogs.config import WINDOW_AFTER_NS, WINDOW_BEFORE_NS


def create_tools(con: duckdb.DuckDBPyConnection, inject_time: int) -> list[BaseTool]:
    """Create bound tool functions that close over the DuckDB connection and inject_time."""

    inject_ns = inject_time * 1_000_000_000  # convert inject_time to nanoseconds

    @tool
    def summarize(service: str | None = None, window: int = 300) -> str:
        """Recon tool: see what log templates appeared around the incident time. Filtered by service if provided.

        Args:
            service: Service name to inspect. None for an overview of ALL services.
            window: Seconds around inject_time to look at (default 300 = T-120s to T+300s).
        """
        window_start = inject_ns - WINDOW_BEFORE_NS
        window_end = inject_ns + (window * 1_000_000_000)

        if service is None:
            return _summarize_overview(con, window_start, window_end)
        return _summarize_service(con, service, window_start, window_end)

    @tool
    def get_lines(template_id: int, k: int = 20) -> str:
        """Evidence tool: get raw log lines for a specific template.

        Use this AFTER summarize to see the actual log lines behind a template.
        Pass the template_id from summarize output (e.g. T1941 → template_id=1941).

        Args:
            template_id: The template ID from summarize output.
            k: Max lines to return (default 20, max 50).
        """
        k = min(k, 50)
        window_start = inject_ns - WINDOW_BEFORE_NS
        window_end = inject_ns + WINDOW_AFTER_NS

        rows = con.execute(
            """
            SELECT timestamp, service, message
            FROM logs
            WHERE template_id = $1
              AND timestamp BETWEEN $2 AND $3
            ORDER BY timestamp
            LIMIT $4
            """,
            [template_id, window_start, window_end, k],
        ).fetchall()

        if not rows:
            return f"No log lines found for template_id={template_id}"

        lines: list[str] = []
        for ts, svc, msg in rows:
            offset = (ts - inject_ns) / 1_000_000_000
            lines.append(f"[T{offset:+.1f}s] {svc}: {msg}")

        return f"Found {len(lines)} lines:\n\n" + "\n".join(lines)

    return [summarize, get_lines]


def _summarize_overview(
    con: duckdb.DuckDBPyConnection, window_start: int, window_end: int
) -> str:
    """Overview across all services: template count, total log count, top template per service."""

    rows = con.execute(
        """
        SELECT
            service,
            SUM(count) AS total_logs,
            COUNT(*) AS template_count,
            MAX(count) AS top_template_count
        FROM templates
        WHERE first_seen BETWEEN $1 AND $2
        GROUP BY service
        ORDER BY total_logs DESC
        """,
        [window_start, window_end],
    ).fetchall()

    if not rows:
        return "No templates found in the incident window."

    lines = ["Service overview around incident time:\n"]
    for svc, total, tpl_count, top_count in rows:
        lines.append(
            f"  {svc}: {total} logs across {tpl_count} templates (busiest template: {top_count}x)"
        )

    lines.append("\nCall summarize(service='<name>') to inspect a specific service.")
    return "\n".join(lines)


def _summarize_service(
    con: duckdb.DuckDBPyConnection,
    service: str,
    window_start: int,
    window_end: int,
) -> str:
    """Detailed view of one service: top templates with text, counts, and time offsets."""

    inject_ns = window_start + WINDOW_BEFORE_NS  # recover T0 from the window start

    rows = con.execute(
        """
        SELECT template_id, template_text, count, first_seen, last_seen
        FROM templates
        WHERE service = $1
          AND first_seen BETWEEN $2 AND $3
        ORDER BY count DESC
        LIMIT 30
        """,
        [service, window_start, window_end],
    ).fetchall()

    if not rows:
        return f"No templates found for service '{service}' in the incident window."

    total_logs = sum(r[2] for r in rows)
    lines = [f"Service '{service}': {total_logs} logs across {len(rows)} templates\n"]

    for tid, text, count, first, last in rows:
        first_offset = (first - inject_ns) / 1_000_000_000
        last_offset = (last - inject_ns) / 1_000_000_000
        lines.append(
            f"  T{tid} ({count}x, T{first_offset:+.0f}s to T{last_offset:+.0f}s): {text[:200]}"
        )

    return "\n".join(lines)
