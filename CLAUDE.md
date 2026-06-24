# Sherlogs

Log-based root cause analysis agent. Reads raw logs, names the service that broke, grounds the answer in evidence, reports confidence. Benchmarked on RCAEval.

## Stack
- **Drain3** — log templating (raw lines → templates + counts).
- **DuckDB** — SQL over logs.csv; backs the stats and aggregation queries.
- **LangGraph** — agent loop, state, tool-calling, hop budget.
- **Gemini 2.5 Flash** — reasoning (no fine-tuning).

## Dataset: RCAEval RE3 (code-level faults, has logs)
Zenodo: https://zenodo.org/records/14590730. Three systems — RE3-SS (Sock Shop), RE3-OB (Online Boutique), RE3-TT (Train Ticket) — 30 incidents each.

### Data setup (prerequisite)
Data is NOT checked into git. To set up:
1. Create `data/` in the project root: `mkdir -p data/`
2. Download RE3 from the Zenodo link above
3. Unzip into `data/RE3-SS/` so the layout is `data/RE3-SS/carts_f1/1/logs.csv`

### Layout
Folder `service_fault/instance/`, e.g. `carts_f1/2/`. The folder name IS the ground-truth label (service + fault). Each instance has:
- `logs.csv` — what the agent reads
- `inject_time.txt` — Unix ts, "anomaly detected at T"; a legitimate INPUT, not leakage
- `metrics.json`, `traces.csv` — ignored for the log-only system (traces are the escalation path for silent faults)

## Architecture

### Input / Output
- **Input**: path to an incident folder; reads `logs.csv` and `inject_time.txt`.
- **Output**: a verdict `{service, confidence, top3, reasoning}`, compared against the ground-truth folder label for eval.

### Data layer: two DuckDB tables
**`logs`** — raw CSV rows + Drain3 tagging:
| timestamp | service (from container_name) | message | template_id |

**`templates`** — aggregated from logs:
| template_id | template_text | service | count | first_seen | last_seen |

Drain3 takes a message string and returns a template ID + text with variables replaced by `<*>`. No ML, no training — a fixed-depth parse tree. It doesn't know about services, timestamps, or errors.

### Deterministic pipeline (runs once, no LLM)
1. Load logs.csv into a `logs` table.
2. Feed each message through Drain3 → template_id, added as a column.
3. Aggregate into the `templates` table (GROUP BY template_id, service).

### Agent loop (LangGraph + Gemini 2.5 Flash)
The agent starts with a compressed template overview, then investigates with two bounded tools (no call can flood the context):

- **`summarize(service?, window?)`** — recon. Queries the `templates` table: what templates appeared (overall, or for one service) around T. Returns top templates + counts + first/last seen. Never raw lines.
- **`get_lines(template_id, k)`** — evidence. Queries the `logs` table: returns bounded raw log samples for a template, to confirm a hypothesis and cite evidence.

### Multi-hop reasoning
1. Read the overview → see which services have abnormal activity.
2. Classify each error:
   - **Originating** (service crashed / threw itself: exception, OOM, restart) → root-cause candidate.
   - **Relaying** (service reporting another is unreachable: "can't reach X", "timeout calling X") → follow the pointer to X.
   - **Datastore symptom** (a database logging only connection drops) → effect, not cause.
3. Follow the chain to the originating service; pull raw lines as evidence; answer.
4. Guardrails: a hop budget; a forced final answer when the budget runs out; every tool output bounded by construction.

### LangGraph structure
- **Nodes**: `pipeline` (deterministic setup) → `reason` (Gemini) → `tools` (execute summarize/get_lines) → `force_answer` (on budget exhaustion) → `extract` (parse the verdict).
- The `reason → tools → reason` loop is the multi-hop cycle, bounded by the hop budget.

### Domain-agnostic by design
Only the loader knows the input format. Everything downstream works on generic `timestamp / service / message / template_id` columns, so pointing Sherlogs at a new domain is a one-file change.

## Results
90 RE3 incidents (SS + OB + TT). The agent beats a single-call baseline on every metric:

| | Baseline | Agent |
|---|---|---|
| AC@1 | 83.3% | **92.2%** |
| AC@3 | 100% | **100%** |
| MRR | 0.913 | **0.954** |
