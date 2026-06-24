"""Deterministic pipeline: Drain3 templating + DuckDB table construction.
1. Load logs.csv into a raw_logs table
2. Feed every message through Drain3 to get template IDs
3. Join template IDs back into logs table (timestamp, service, message, template_id)
4. Aggregate into templates table (template_id, template_text, service, count, first_seen, last_seen)
5. Drop intermediate tables, return connection with `logs` and `templates` ready to query
"""

import logging

import duckdb
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from sherlogs.config import DRAIN_DEPTH, DRAIN_SIM_TH
from sherlogs.loader import IncidentCase, load_logs_table

logger = logging.getLogger(__name__)


def build_tables(case: IncidentCase) -> duckdb.DuckDBPyConnection:
    """Run the full deterministic pipeline for one incident case."""

    con = duckdb.connect()
    load_logs_table(
        case, con
    )  # loader owns the on-disk schema; we work on `service` from here on

    miner, id_map = _run_drain3(con)
    _build_logs_table(con, id_map)
    _build_templates_table(con, miner)

    template_row = con.sql("SELECT COUNT(*) FROM templates").fetchone()
    log_row = con.sql("SELECT COUNT(*) FROM logs").fetchone()
    if template_row is None or log_row is None:
        raise ValueError("Failed to query logs or templates table")
    template_count = template_row[0]
    log_count = log_row[0]
    logger.info(
        "Templated %d logs into %d templates (%.0f:1 compression)",
        log_count,
        template_count,
        log_count / template_count,
    )

    return con


def _run_drain3(
    con: duckdb.DuckDBPyConnection,
) -> tuple[TemplateMiner, list[tuple[int, int]]]:
    """Feed every message through Drain3, returning the miner and (row_id, template_id) pairs.

    The miner holds the clustered templates; the pairs map each log row to its template.
    """

    config = TemplateMinerConfig()

    # Fixed-depth tree depth + token similarity threshold (see config.py for rationale).
    config.drain_depth = DRAIN_DEPTH
    config.drain_sim_th = DRAIN_SIM_TH
    miner = TemplateMiner(config=config)

    rows = con.sql("SELECT row_id, message FROM raw_logs ORDER BY row_id").fetchall()

    id_map: list[tuple[int, int]] = []
    for row_id, message in rows:
        if message is None:
            id_map.append((row_id, -1))
            continue
        result = miner.add_log_message(message)
        id_map.append((row_id, result["cluster_id"]))

    return miner, id_map


def _build_logs_table(
    con: duckdb.DuckDBPyConnection, id_map: list[tuple[int, int]]
) -> None:
    """Join template_ids back into logs using a temp table for fast bulk insert. Remove the raw_logs and temp tables after joining."""

    con.sql("CREATE TABLE _tid (row_id BIGINT, template_id INTEGER)")
    con.executemany("INSERT INTO _tid VALUES ($1, $2)", id_map)

    con.sql("""
        CREATE TABLE logs AS
        SELECT r.timestamp, r.service, r.message, t.template_id
        FROM raw_logs r
        JOIN _tid t ON r.row_id = t.row_id
    """)

    con.sql("DROP TABLE raw_logs")
    con.sql("DROP TABLE _tid")


def _build_templates_table(
    con: duckdb.DuckDBPyConnection, miner: TemplateMiner
) -> None:
    """Build templates table: aggregation from logs + template_text from Drain3. Drop the _ttext temp table after joining."""

    template_texts = [(c.cluster_id, c.get_template()) for c in miner.drain.clusters]
    con.sql("CREATE TABLE _ttext (template_id INTEGER, template_text VARCHAR)")
    con.executemany("INSERT INTO _ttext VALUES ($1, $2)", template_texts)

    con.sql("""
        CREATE TABLE templates AS
        SELECT
            agg.template_id,
            tt.template_text,
            agg.service,
            agg.count,
            agg.first_seen,
            agg.last_seen
        FROM (
            SELECT
                template_id,
                service,
                COUNT(*) AS count,
                MIN(timestamp) AS first_seen,
                MAX(timestamp) AS last_seen
            FROM logs
            GROUP BY template_id, service
        ) agg
        JOIN _ttext tt ON agg.template_id = tt.template_id
        ORDER BY agg.count DESC
    """)

    con.sql("DROP TABLE _ttext")
