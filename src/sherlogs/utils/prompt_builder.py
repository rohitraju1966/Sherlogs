"""Build the template dump prompt shared by baseline and agent. Top 50 templates by count within the inject window."""

import duckdb

from sherlogs.config import WINDOW_AFTER_NS, WINDOW_BEFORE_NS
from sherlogs.loader import IncidentCase


def build_prompt(con: duckdb.DuckDBPyConnection, case: IncidentCase) -> str:
    """Format templates table into a prompt for the LLM."""
    inject_ns = case.inject_time * 1_000_000_000

    rows = con.sql(f"""
        SELECT template_id, template_text, service, count, first_seen, last_seen
        FROM templates
        WHERE first_seen BETWEEN {inject_ns} - {WINDOW_BEFORE_NS} AND {inject_ns} + {WINDOW_AFTER_NS}
        ORDER BY count DESC
        LIMIT 50
    """).fetchall()

    lines = []
    for tid, text, svc, cnt, first, last in rows:
        first_offset = (first - inject_ns) / 1_000_000_000
        last_offset = (last - inject_ns) / 1_000_000_000
        lines.append(
            f"T{tid} | {svc} | {cnt}x | T{first_offset:+.0f}s to T{last_offset:+.0f}s | {text[:150]}"
        )

    header = f"Incident in system with services: {', '.join(case.services_in_logs)}\n"
    header += "Anomaly detected at timestamp T=0\n"
    header += "Templates around incident time (sorted by count):\n\n"

    return header + "\n".join(lines)
