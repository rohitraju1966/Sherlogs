"""Proves the deterministic stack (loader -> pipeline -> tools) runs on a NON-RE3 domain.

The claim in the README is that only the loader knows the input format; everything
downstream works on generic timestamp/service/message columns. This test backs that
up by synthesizing an incident from a totally different domain (hardware/chip-link
telemetry, not microservices) and running it through the real loader, pipeline, and
tools. No LLM is called, so the test is fast and offline.
"""

import csv
from pathlib import Path

import pytest

from sherlogs.loader import IncidentCase, load_case
from sherlogs.pipeline import build_tables
from sherlogs.tools import create_tools

INJECT_TIME_S = 1_700_000_000
INJECT_NS = INJECT_TIME_S * 1_000_000_000

# (offset_seconds_from_inject, service, message). A foreign domain: chip-link telemetry.
# Repeated messages with varying literals so Drain3 has something to compress.
SYNTHETIC_LOGS: list[tuple[int, str, str]] = [
    (1, "ddr-ctrl", "ECC corrected error at address 0x1a2b"),
    (2, "ddr-ctrl", "ECC corrected error at address 0x9f04"),
    (3, "ddr-ctrl", "ECC corrected error at address 0x33de"),
    (4, "retimer-a", "uncorrectable fault on lane 5, resetting link"),
    (5, "retimer-a", "uncorrectable fault on lane 7, resetting link"),
    (6, "pcie-switch", "timeout waiting for retimer-a, downgrading width"),
    (7, "pcie-switch", "timeout waiting for retimer-a, downgrading width"),
    (8, "pcie-switch", "timeout waiting for retimer-a, downgrading width"),
]


def _write_incident(root: Path, label: str = "retimer-a_f1", instance: int = 1) -> Path:
    """Create a synthetic incident folder in the RE3 on-disk shape but foreign-domain data."""
    case_dir = root / label / str(instance)
    case_dir.mkdir(parents=True)

    (case_dir / "inject_time.txt").write_text(str(INJECT_TIME_S))

    with (case_dir / "logs.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "container_name", "message"])
        for offset, service, message in SYNTHETIC_LOGS:
            writer.writerow([INJECT_NS + offset * 1_000_000_000, service, message])

    return case_dir


@pytest.fixture
def custom_case(tmp_path: Path) -> IncidentCase:
    """A loaded IncidentCase from a synthetic, non-microservice domain."""
    case_dir = _write_incident(tmp_path)
    return load_case(case_dir)


def test_load_case_parses_foreign_domain_label(custom_case: IncidentCase) -> None:
    # The loader is the only domain-aware piece; it should parse our label and find services.
    assert custom_case.service == "retimer-a"
    assert custom_case.fault == "f1"
    assert custom_case.inject_time == INJECT_TIME_S
    assert custom_case.log_count == len(SYNTHETIC_LOGS)
    assert set(custom_case.services_in_logs) == {"ddr-ctrl", "retimer-a", "pcie-switch"}


def test_build_tables_compresses_foreign_domain(custom_case: IncidentCase) -> None:
    con = build_tables(custom_case)
    try:
        log_count = con.sql("SELECT COUNT(*) FROM logs").fetchone()
        template_count = con.sql("SELECT COUNT(*) FROM templates").fetchone()
        assert log_count is not None and template_count is not None

        # All raw lines preserved, and repeated messages collapsed into fewer templates.
        assert log_count[0] == len(SYNTHETIC_LOGS)
        assert 0 < template_count[0] < len(SYNTHETIC_LOGS)
    finally:
        con.close()


def test_tools_query_foreign_domain(custom_case: IncidentCase) -> None:
    con = build_tables(custom_case)
    try:
        summarize, get_lines = create_tools(con, custom_case.inject_time)

        overview: str = summarize.invoke({})
        assert "ddr-ctrl" in overview
        assert "retimer-a" in overview
        assert "pcie-switch" in overview

        # Drilling into a service returns its templates with IDs the agent can cite.
        detail: str = summarize.invoke({"service": "pcie-switch"})
        assert "retimer-a" in detail  # the relaying message names its dependency

        # get_lines fetches raw evidence for a template id pulled from the templates table.
        row = con.sql(
            "SELECT template_id FROM templates WHERE service = 'ddr-ctrl' LIMIT 1"
        ).fetchone()
        assert row is not None
        evidence: str = get_lines.invoke({"template_id": row[0]})
        assert "ddr-ctrl" in evidence
        assert "ECC corrected error" in evidence
    finally:
        con.close()
