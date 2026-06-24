"""Eval harness:
- AC@1: The model's top-1 prediction matches the ground truth
- AC@3: The ground truth is in the model's top-3 predictions
- MRR: The reciprocal rank of the ground truth in the model's top-k predictions
"""

import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from sherlogs.agent import run_agent
from sherlogs.baseline import run_baseline
from sherlogs.loader import IncidentCase, load_dataset
from sherlogs.types import EvalResult

logger = logging.getLogger(__name__)


def evaluate_case(case: IncidentCase, mode: str = "baseline") -> EvalResult:
    case_id = f"{case.service}_{case.fault}_{case.instance}"
    logger.info("Evaluating %s...", case_id)

    start = time.monotonic()
    if mode == "agent":
        verdict = run_agent(case)
    else:
        verdict = run_baseline(case)
    elapsed = time.monotonic() - start

    predicted = verdict["service"].lower().strip()
    top3 = [s.lower().strip() for s in verdict.get("top3", [predicted])]
    ground_truth = case.service.lower()

    rr = 0.0
    for i, s in enumerate(top3):
        if s == ground_truth:
            rr = 1.0 / (i + 1)
            break

    return EvalResult(
        case_id=case_id,
        ground_truth=ground_truth,
        predicted=predicted,
        top3=top3,
        confidence=verdict.get("confidence", "unknown"),
        ac1=predicted == ground_truth,
        ac3=ground_truth in top3,
        reciprocal_rank=rr,
        elapsed_s=elapsed,
        reasoning=verdict.get("reasoning", ""),
    )


def run_eval(root: Path, mode: str = "baseline") -> list[EvalResult]:
    """Run eval on all cases and log a summary."""
    load_dotenv()
    cases = load_dataset(root)
    results: list[EvalResult] = []

    logger.info("Running eval in '%s' mode", mode)

    for case in cases:
        result = evaluate_case(case, mode=mode)
        results.append(result)
        status = "✓" if result["ac1"] else ("~" if result["ac3"] else "✗")
        logger.info(
            "  %s %-25s truth=%-12s pred=%-12s RR=%.2f  %5.1fs  conf=%s",
            status,
            result["case_id"],
            result["ground_truth"],
            result["predicted"],
            result["reciprocal_rank"],
            result["elapsed_s"],
            result["confidence"],
        )

    _log_summary(results)
    return results


def _log_summary(results: list[EvalResult]) -> None:
    """Log AC@1, AC@3, and MRR scores."""
    total = len(results)
    ac1 = sum(1 for r in results if r["ac1"])
    ac3 = sum(1 for r in results if r["ac3"])
    mrr = sum(r["reciprocal_rank"] for r in results) / total
    total_time = sum(r["elapsed_s"] for r in results)
    avg_time = total_time / total

    sep = "=" * 60
    logger.info(sep)
    logger.info("EVAL RESULTS (%d cases)", total)
    logger.info(sep)
    logger.info("  AC@1: %d/%d = %.1f%%", ac1, total, ac1 / total * 100)
    logger.info("  AC@3: %d/%d = %.1f%%", ac3, total, ac3 / total * 100)
    logger.info("  MRR:  %.3f", mrr)
    logger.info("  Time: %.0fs total, %.1fs avg/case", total_time, avg_time)

    by_service: dict[str, list[EvalResult]] = {}
    for r in results:
        by_service.setdefault(r["ground_truth"], []).append(r)

    logger.info("Per-service breakdown:")
    for svc, svc_results in sorted(by_service.items()):
        svc_ac1 = sum(1 for r in svc_results if r["ac1"])
        svc_ac3 = sum(1 for r in svc_results if r["ac3"])
        svc_mrr = sum(r["reciprocal_rank"] for r in svc_results) / len(svc_results)
        svc_total = len(svc_results)
        logger.info(
            "  %-15s AC@1=%d/%d  AC@3=%d/%d  MRR=%.3f",
            svc,
            svc_ac1,
            svc_total,
            svc_ac3,
            svc_total,
            svc_mrr,
        )


if __name__ == "__main__":
    # Surface Sherlogs' own progress logs, but keep third-party libraries (Drain3) quiet.
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("sherlogs").setLevel(logging.INFO)
    mode = "agent" if "--agent" in sys.argv else "baseline"
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(args[0]) if args else Path("data/RE3-SS")
    run_eval(root, mode=mode)
