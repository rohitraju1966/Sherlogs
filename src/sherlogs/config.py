"""Central configuration — the knobs you might tune, in one place."""

# LLM used for reasoning (baseline + agent).
MODEL_NAME = "gemini-2.5-flash"

# Agent loop: max tool calls before the agent is forced to answer.
MAX_HOPS = 4

# Incident window (nanoseconds) relative to inject_time T0.
# Tools and the baseline only look at logs inside [T0 - BEFORE, T0 + AFTER].
WINDOW_BEFORE_NS = 120_000_000_000  # 120s before the anomaly
WINDOW_AFTER_NS = 300_000_000_000  # 300s after the anomaly

# Drain3 templating: fixed-depth parse tree depth + token similarity threshold.
DRAIN_DEPTH = 4
DRAIN_SIM_TH = 0.4
