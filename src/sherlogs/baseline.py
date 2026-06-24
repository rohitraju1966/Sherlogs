"""Single-call baseline: one LLM call per incident, no tools, no hops."""

import json
import logging

import duckdb
from langchain_google_genai import ChatGoogleGenerativeAI

from sherlogs.config import MODEL_NAME, WINDOW_AFTER_NS, WINDOW_BEFORE_NS
from sherlogs.loader import IncidentCase
from sherlogs.pipeline import build_tables
from sherlogs.prompt import SYSTEM_PROMPT_BASELINE
from sherlogs.types import Verdict

logger = logging.getLogger(__name__)


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


def run_baseline(case: IncidentCase) -> Verdict:
    """Run the full single-call baseline for one incident."""
    con = build_tables(case)
    prompt = build_prompt(con, case)
    con.close()

    llm = ChatGoogleGenerativeAI(model=MODEL_NAME)
    response = llm.invoke(
        [
            {"role": "system", "content": SYSTEM_PROMPT_BASELINE},
            {"role": "human", "content": prompt},
        ]
    )

    try:
        text = response.content
        if isinstance(text, str):
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            verdict: Verdict = json.loads(text)
            return verdict
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("Failed to parse LLM response: %s", e)

    return Verdict(
        service="unknown",
        confidence="low",
        top3=["unknown"],
        reasoning=f"Parse error. Raw: {response.content}",
    )
