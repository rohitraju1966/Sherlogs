# Sherlogs

**A log-only root cause analysis agent for microservice incidents.** Point it at the raw logs from a failing system; it names the service that broke, grounds the answer in actual log lines, and reports a calibrated confidence.

Benchmarked on **[RCAEval RE3](https://zenodo.org/records/14590730)** (code-level faults) across three microservice systems - 90 incidents, 7 to 47 services each.

| | Single-call baseline | **Sherlogs agent** |
|---|---|---|
| **AC@1** (top-1 correct) | 83.3% | **92.2%** |
| **AC@3** (correct in top-3) | 100% | **100%** |
| **MRR** (rank quality) | 0.913 | **0.954** |

The agent wins on every metric, and wins biggest exactly where a single LLM call fails: disambiguating a crashed service from its database, and tracing failures through deep service graphs.

Nothing about it is microservice-specific. Only the data loader knows the input format - the templating -> SQL -> agent pipeline downstream works on generic `timestamp / service / message` columns. Pointing it at a different log source (another stack, CI runs, hardware-validation or chip-test logs) is a one-file change.

---

## The problem

When a microservice crashes, the logs lie to you. The service that *throws the loudest errors* is usually the victim, not the cause:

- The **broken service** crashes, restarts, and comes back in a bad state.
- Its **datastore** logs `Connection ended` - but that's a *symptom*; it just lost its client.
- **Upstream callers** log `timeout calling X`, `connection refused` - they're fine, just relaying.

A naive reader blames the noisiest service. Correct RCA means following the error chain back to the service that *originated* the failure. That's a multi-hop reasoning problem over tens of thousands of log lines - and that's what Sherlogs does.

## The build

The project grew in three steps, each one answering the problem the last one left behind.

1. **Feed the logs to an LLM - but how?** Dumping 50 raw lines from around the incident time doesn't carry enough signal; the real pattern is buried in repetition. So I templated the logs first (Drain3) and handed the model the 50 most relevant *templates* around the incident instead. This became the single-call baseline.

2. **Good, but not good enough.** The baseline scored well, but it was guessing from a flat snapshot - it never saw the whole story, so its reasoning was shallow and it couldn't tell a crashed service apart from its failing database.

3. **Let it investigate.** So I built an agent on top of the baseline: same starting view, plus two tools to drill into any service and pull the actual log lines as evidence. Now it follows the error chain to the origin and grounds its answer - which is where the jump to **92.2% AC@1** comes from.

## How it works

```
logs.csv -> Drain3 templating -> DuckDB (two tables) -> LangGraph agent -> verdict
(~85k lines) (~28:1 compression)  logs + templates     Gemini 2.5 Flash    {service, confidence, evidence}
```

**Deterministic pipeline (no LLM):** Raw logs are templated with [Drain3](https://github.com/logpai/Drain3) (`MongoSocketException to host-42` and `...to host-99` collapse into one pattern), then loaded into two DuckDB tables - `logs` (raw lines) and `templates` (aggregated patterns with counts and time ranges). ~85k lines compress to ~3k templates.

**Agent loop (LangGraph + Gemini 2.5 Flash):** The agent gets the compressed overview up front (same starting view as the baseline), then investigates with two bounded tools:

- **`summarize(service?)`** - recon. What error patterns appeared, in which service, when.
- **`get_lines(template_id)`** - evidence. The actual raw log lines behind a pattern, to confirm a hypothesis before answering.

Every tool output is bounded by construction, so no single call can ever flood the context. The loop is capped at a hop budget; the agent is told how many calls remain and is forced to commit to a final answer when the budget runs out.

The agent reasons by **classifying each error** - did this service *crash/throw* (originating → it's the cause) or merely *report another unreachable* (relaying → follow the pointer)? - and follows the chain until it finds the origin.

## How it's scored

Three metrics, borrowed from the RCA literature so the numbers are comparable:

- **AC@1** - is the top guess right? The honest metric; forces a commitment.
- **AC@3** - is the truth in the top 3? Measures whether the shortlist is useful to an on-call engineer.
- **MRR** - how high does the truth rank on average? Captures *calibration*. A system with high AC@1 but low MRR is "right or completely lost"; Sherlogs scores high on both - confident *and* a trustworthy fallback when it's not.

## The benchmark

[RCAEval RE3](https://zenodo.org/records/14590730) injects code-level bugs into three open-source microservice systems and records the resulting logs, with the faulty service as ground truth. 30 incidents per system, 90 total:

- **Sock Shop** - Weaveworks' e-commerce demo, ~13 polyglot services (Node/Java/Go). The classic RCA testbed.
- **Online Boutique** - Google's gRPC "Hipster Shop", 10 services across Go/Python/Java/C#/Node.
- **Train Ticket** - the largest open benchmark, ~47 Spring Boot services with deep call chains. The hardest of the three.

Each incident ships ~40k–87k raw log lines. Spanning Node.js, gRPC, and Spring Boot in one benchmark is what makes it a real test of domain-agnostic reasoning.

## Results

90 incidents, three systems, Gemini 2.5 Flash, ~35–45s and a fraction of a cent per case.

| System | Services | AC@1 | AC@3 | MRR |
|---|---|---|---|---|
| Sock Shop | 13 | 83.3% | 100% | 0.917 |
| Online Boutique | 10 | 100% | 100% | 1.000 |
| Train Ticket | 47 | 93.3% | 100% | 0.944 |
| **Overall** | | **92.2%** | **100%** | **0.954** |

The same prompt and agent handle Node.js, gRPC, and Spring Boot systems with no per-system tuning - only the data loader knows about the dataset format.

## What's next

- **Fault-type recognition** *(in progress)* - today Sherlogs names the *service* that broke; next it will also classify the *fault*: RE3's F1–F5 (incorrect parameters, missing calls, wrong return values, missing exception handlers). The evidence is already in the log lines the agent pulls - it's a matter of teaching it to read the bug, not just locate it.
- **RE1 + RE2 coverage** - extend evaluation beyond RE3 (code-level faults) to resource and network faults, where the signal leans more on metrics and traces - a harder test for a log-only agent, and the path toward escalating to those sources when logs alone fall short.

## Quick start

```bash
# Python 3.11+
python -m venv sherlogs_env && source sherlogs_env/bin/activate
pip install -r requirements.txt

# Gemini API key
echo "GOOGLE_API_KEY=your_key_here" > .env
```

**Data** (not checked in): download RE3 from [Zenodo](https://zenodo.org/records/14590730) and unzip so the layout is `data/RE3-SS/carts_f1/1/logs.csv`.

```bash
# Run the agent on one system (30 incidents)
python -m sherlogs.eval --agent data/RE3-SS

# Run the single-call baseline for comparison
python -m sherlogs.eval data/RE3-SS
```

Each incident folder name is the ground-truth label (`carts_f1` → service `carts`, fault `f1`). `inject_time.txt` is the anomaly timestamp - a legitimate input, the moment monitoring fires.

## Stack

[Drain3](https://github.com/logpai/Drain3) for log templating · [DuckDB](https://duckdb.org/) for SQL over logs · [LangGraph](https://github.com/langchain-ai/langgraph) for the agent loop · [LangChain](https://github.com/langchain-ai/langchain) for the LLM interface (message + tool abstractions, Gemini adapter) · Gemini 2.5 Flash for reasoning.

## License

[MIT](LICENSE). The RCAEval benchmark and its RE3 datasets are also MIT-licensed; the underlying microservice systems (Sock Shop, Online Boutique, Train Ticket) are Apache 2.0. All dependencies are permissively licensed.
