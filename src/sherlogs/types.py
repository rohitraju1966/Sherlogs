"""Shared structured types, so modules can pass them around without importing each other."""

from typing import TypedDict


class Verdict(TypedDict):
    """The diagnosis: which service, how sure, the ranked shortlist, and the evidence."""

    service: str
    confidence: str
    top3: list[str]
    reasoning: str


class EvalResult(TypedDict):
    """One scored case in the eval harness."""

    case_id: str
    ground_truth: str
    predicted: str
    top3: list[str]
    confidence: str
    ac1: bool
    ac3: bool
    reciprocal_rank: float
    elapsed_s: float
    reasoning: str
