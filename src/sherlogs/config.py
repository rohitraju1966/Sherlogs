"""Central configuration — the knobs you might tune, in one place."""

MODEL_NAME = "gemini-2.5-flash"

# Agent loop
MAX_HOPS = 4

# Incident window (nanoseconds) relative to inject_time T0.
WINDOW_BEFORE_NS = 120_000_000_000  # 120s before the anomaly
WINDOW_AFTER_NS = 300_000_000_000  # 300s after the anomaly

# Drain3 templating
DRAIN_DEPTH = 4
DRAIN_SIM_TH = 0.4
