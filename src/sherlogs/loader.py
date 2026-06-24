"""Loads RCAEval RE3 incident cases from disk (Sock Shop, Online Boutique, Train Ticket).

This is the only module that knows the on-disk format — the folder layout and the CSV
column names. Porting Sherlogs to a new data source means changing only this file.
Prerequisites: download RE3 from https://zenodo.org/records/14590730 and unzip into data/.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb


@dataclass
class IncidentCase:
    service: str
    fault: str
    instance: int
    path: Path
    inject_time: int
    log_count: int = 0
    services_in_logs: list[str] = field(default_factory=list)


def parse_label(folder_name: str) -> tuple[str, str]:
    """'carts_f1' -> ('carts', 'f1'), 'ts-route-service_f3_1' -> ('ts-route-service', 'f3_1')"""
    match = re.match(r"^(.+)_(f\d+.*)$", folder_name)
    if not match:
        raise ValueError(f"Cannot parse label from folder: {folder_name}")
    return match.group(1), match.group(2)


def load_case(case_path: Path) -> IncidentCase:
    """Load a single incident instance from its folder. Case Path is of the form `data/<service>_<fault>/<instance>`"""
    fault_folder = case_path.parent.name
    instance = int(case_path.name)
    service, fault = parse_label(fault_folder)

    inject_time_file = case_path / "inject_time.txt"
    inject_time = int(inject_time_file.read_text().strip())

    logs_file = case_path / "logs.csv"
    con = duckdb.connect()
    stats = con.sql(f"""
        SELECT
            COUNT(*) AS log_count,
            LIST(DISTINCT container_name ORDER BY container_name) AS services
        FROM read_csv_auto('{logs_file}')
    """).fetchone()
    con.close()

    if stats is None:
        raise ValueError(f"No logs found in {logs_file}")

    return IncidentCase(
        service=service,
        fault=fault,
        instance=instance,
        path=case_path,
        inject_time=inject_time,
        log_count=stats[0],
        services_in_logs=stats[1],
    )


def load_logs_table(case: IncidentCase, con: duckdb.DuckDBPyConnection) -> None:
    """Populate a `raw_logs` table (row_id, timestamp, service, message) from the incident's CSV.

    This is the ONLY place that knows the on-disk log schema (the `container_name`,
    `timestamp`, `message` column names). Everything downstream operates on the normalized
    `service` column, so porting Sherlogs to a new data source means editing only this loader.
    """
    logs_file = case.path / "logs.csv"
    con.sql(f"""
        CREATE TABLE raw_logs AS
        SELECT
            ROW_NUMBER() OVER () AS row_id,
            timestamp,
            container_name AS service,
            message
        FROM read_csv_auto('{logs_file}')
    """)


def load_dataset(root: Path) -> list[IncidentCase]:
    """Walk a dataset root (e.g. data/RE3-SS) and load every incident instance it contains."""
    cases: list[IncidentCase] = []
    for fault_dir in sorted(root.iterdir()):
        if not fault_dir.is_dir():
            continue
        for instance_dir in sorted(fault_dir.iterdir()):
            if not instance_dir.is_dir():
                continue
            logs_file = instance_dir / "logs.csv"
            if not logs_file.exists():
                continue
            cases.append(load_case(instance_dir))
    return cases
