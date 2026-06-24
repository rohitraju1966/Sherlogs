"""Single-call baseline: one LLM call per incident, no tools, no hops."""

import json
import logging

from langchain_google_genai import ChatGoogleGenerativeAI

from sherlogs.config import MODEL_NAME
from sherlogs.loader import IncidentCase
from sherlogs.pipeline import build_tables
from sherlogs.prompt import SYSTEM_PROMPT_BASELINE
from sherlogs.types import Verdict
from sherlogs.utils.prompt_builder import build_prompt

logger = logging.getLogger(__name__)


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
